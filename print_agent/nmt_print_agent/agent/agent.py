#!/usr/bin/env python3
"""NMT downtown print agent.

The dashboard pushes each pickup order as a finished PDF to POST /print. The
agent prints it unchanged through a driverless CUPS queue, confirms with the
printer's page counter, and reports the outcome back to the dashboard. The
contract is print_agent/INSTRUCTIONS.md (Phase 3) as amended by
print_agent/INSTRUCTIONS-UPDATE-PDF.md in the repository.

Rules this file is built around:
- Never print a job twice: a job id is recorded in /data before anything is
  sent to CUPS, a repeated id is answered from the record, and a job that was
  in flight when the agent stopped is reported `uncertain`, never resent.
- Never log job content or header values: logs carry ids, order numbers,
  timings, page counts and errors only.
"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ipp
import snmp

VERSION = "1.2.1"


def container_env(name, default=None):
    """A container environment variable. s6-overlay (the base image's init)
    starts the agent with a clean environment and keeps the container's
    variables in /run/s6/container_environment."""
    value = os.environ.get(name)
    if value is None:
        try:
            with open(os.path.join("/run/s6/container_environment", name)) as f:
                value = f.read()
        except OSError:
            pass
    return default if value is None else value


def _env_num(name, default):
    # Timings are overridable only so the test harness can shorten them.
    return float(container_env(name, default))


DATA = os.environ.get("NMT_DATA", "/data")
STATE_FILE = os.path.join(DATA, "state.json")
SPOOL = os.path.join(DATA, "spool")
CUPS_QUEUE = "downtown"
CUPS_URI = f"ipp://localhost:631/printers/{CUPS_QUEUE}"
PAGE_COUNT_OID = "1.3.6.1.2.1.43.10.2.1.4.1.1"
LETTER = "na_letter_8.5x11in"

MAX_SKEW = 300            # seconds a signed timestamp may differ from our clock
MAX_PDF = 5 * 1024 * 1024          # decoded PDF
MAX_BODY = MAX_PDF * 4 // 3 + 64 * 1024  # its base64 plus the JSON around it
KEEP_JOBS = 1000          # job ids remembered for duplicate answers
CUPS_WAIT = _env_num("NMT_CUPS_WAIT", 180)        # wait for CUPS to finish a job
COUNTER_STABLE = _env_num("NMT_COUNTER_STABLE", 10)
COUNTER_WAIT = _env_num("NMT_COUNTER_WAIT", 60)
BLOCKED_GRACE = _env_num("NMT_BLOCKED_GRACE", 600)  # hold before failing held jobs
POLL_INTERVAL = _env_num("NMT_POLL_INTERVAL", 30)
PROBE_MAX_AGE = 10        # /print re-probes the printer if the cached state is older
# Result delivery: one attempt, then one retry after each delay; after the
# last one the agent gives up and raises a Home Assistant alert.
RETRY_DELAYS = [float(s) for s in container_env("NMT_RETRY_DELAYS", "5,30,120,300").split(",")]
PRINTER_ALERT = "nmt_print_agent_printer"
STATUS_ENTITY = "sensor.instore_order_printing"  # read by the HA "Instore Order Printing" dashboard

# Printer-state-reasons that mean the printer cannot print, after stripping the
# -error/-warning/-report suffix and folding synonyms into the names the
# dashboard knows (media-empty, media-jam, door-open, offline, unreachable).
# Not spool-area-full: the M203 reports it while it is busy receiving a job,
# and CUPS already waits for a busy printer.
BLOCKING = {
    "media-empty", "media-jam", "door-open", "offline", "unreachable",
    "toner-empty", "input-tray-missing", "output-area-full", "marker-waste-full",
    "paused",
}
ALIASES = {
    "media-needed": "media-empty",
    "cover-open": "door-open",
    "interlock-open": "door-open",
    "marker-supply-empty": "toner-empty",
    "shutdown": "offline",
    "moving-to-paused": "paused",
}


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + msg, flush=True)


def sign(key, ts, method, path, body):
    msg = f"{ts}\n{method}\n{path}\n".encode() + body
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def verify(key, headers, method, path, body, now=None):
    """True only for a fresh timestamp and a matching signature."""
    if not key:
        return False
    ts = headers.get("X-NMT-Timestamp", "")
    sig = headers.get("X-NMT-Signature", "")
    if not re.fullmatch(r"\d{1,12}", ts) or not re.fullmatch(r"[0-9a-fA-F]{64}", sig):
        return False
    if abs((now or time.time()) - int(ts)) > MAX_SKEW:
        return False
    return hmac.compare_digest(sign(key, ts, method, path, body).encode(), sig.lower().encode())


def normalize_reasons(raw):
    """IPP printer-state-reasons -> (all reasons, blocking reasons), normalized."""
    names, blocking = [], []
    for r in raw:
        if not isinstance(r, str) or r == "none":
            continue
        severity = ""
        for suffix in ("-error", "-warning", "-report"):
            if r.endswith(suffix):
                r, severity = r[: -len(suffix)], suffix[1:]
                break
        r = ALIASES.get(r, r)
        if r not in names:
            names.append(r)
        if severity in ("warning", "report"):
            continue
        if (r in BLOCKING or severity == "error") and r not in blocking:
            blocking.append(r)
    return names, blocking


# --------------------------------------------------------------------------
# Printer
# --------------------------------------------------------------------------

class Printer:
    STATES = {3: "idle", 4: "processing", 5: "stopped"}

    def __init__(self, uri, ip, community):
        self.uri, self.ip, self.community = uri, ip, community
        self.lock = threading.Lock()
        self.snap = {"reachable": False, "state": "stopped", "reasons": ["unreachable"],
                     "blocking": ["unreachable"], "page_count": None, "at": 0.0}

    def page_count(self):
        try:
            return snmp.get(self.ip, PAGE_COUNT_OID, self.community, timeout=2.0, retries=1)
        except (snmp.SNMPError, OSError):
            return None

    def poll(self, counter=True, timeout=4.0):
        try:
            a = ipp.get_printer_attributes(self.uri, ["printer-state", "printer-state-reasons"], timeout=timeout)
            state = self.STATES.get((a.get("printer-state") or [5])[0], "stopped")
            reasons, blocking = normalize_reasons(a.get("printer-state-reasons", []))
            if state == "stopped" and not blocking:
                blocking = ["offline"]
            snap = {"reachable": True, "state": state, "reasons": reasons, "blocking": blocking}
        except (ipp.IPPError, OSError, ValueError) as e:
            snap = {"reachable": False, "state": "stopped", "reasons": ["unreachable"],
                    "blocking": ["unreachable"], "error": type(e).__name__}
        with self.lock:
            prev = self.snap
            if counter:
                snap["page_count"] = self.page_count() if snap["reachable"] else None
            else:
                snap["page_count"] = prev.get("page_count")
            snap["at"] = time.time()
            self.snap = snap
        changed = (prev["reachable"], prev["state"], prev["reasons"]) != (snap["reachable"], snap["state"], snap["reasons"])
        if changed:
            log(f"printer: reachable={snap['reachable']} state={snap['state']} reasons={','.join(snap['reasons']) or '-'}")
        return snap

    def current(self):
        with self.lock:
            return dict(self.snap)


# --------------------------------------------------------------------------
# CUPS (runs inside this container)
# --------------------------------------------------------------------------

class Cups:
    def __init__(self, device_uri):
        self.device_uri = device_uri
        self.proc = None
        self.ready = False
        self.lock = threading.Lock()

    def start(self):
        os.makedirs("/run/cups", exist_ok=True)
        self.proc = subprocess.Popen(["cupsd", "-f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("localhost", 631), timeout=1).close()
                return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("cupsd did not start")

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()

    def ensure_queue(self):
        """Create the driverless queue once. `-m everywhere` asks the printer
        for its capabilities, so this only succeeds while the printer answers."""
        with self.lock:
            return self._ensure_queue()

    def _ensure_queue(self):
        if self.ready:
            return True
        if subprocess.run(["lpstat", "-p", CUPS_QUEUE], capture_output=True).returncode != 0:
            r = subprocess.run(
                ["lpadmin", "-p", CUPS_QUEUE, "-E", "-v", self.device_uri, "-m", "everywhere",
                 "-o", "printer-error-policy=abort-job", "-o", f"media-default={LETTER}",
                 "-o", "sides-default=one-sided"],
                capture_output=True, text=True, timeout=60)
            if r.returncode != 0:
                log(f"cups: could not create queue: {r.stderr.strip()[:200]}")
                return False
            log(f"cups: queue '{CUPS_QUEUE}' created (driverless) -> {self.device_uri}")
        self.ready = True
        return True

    def submit(self, path, title):
        r = subprocess.run(["lp", "-d", CUPS_QUEUE, "-t", title, "-o", f"media={LETTER}",
                            "-o", "sides=one-sided", path], capture_output=True, text=True, timeout=30)
        m = re.search(rf"request id is {CUPS_QUEUE}-(\d+)", r.stdout)
        if r.returncode != 0 or not m:
            raise RuntimeError((r.stderr or r.stdout).strip()[:200] or f"lp exit {r.returncode}")
        return int(m.group(1))

    def job_state(self, job_id):
        try:
            a = ipp.get_job_attributes(CUPS_URI, job_id, ["job-state"], timeout=4)
            return (a.get("job-state") or [None])[0]
        except (ipp.IPPError, OSError):
            return None

    def cancel(self, job_id):
        subprocess.run(["cancel", f"{CUPS_QUEUE}-{job_id}"], capture_output=True, timeout=30)


# --------------------------------------------------------------------------
# Durable state in /data
# --------------------------------------------------------------------------

class Store:
    """jobs: {id: record} in arrival order; queue: ids waiting to print;
    outbox: results not yet accepted by the dashboard."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.wake = threading.Condition(self.lock)
        try:
            with open(path) as f:
                d = json.load(f)
        except FileNotFoundError:
            d = {}
        self.jobs = d.get("jobs", {})
        self.queue = d.get("queue", [])
        self.outbox = d.get("outbox", [])

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"jobs": self.jobs, "queue": self.queue, "outbox": self.outbox}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            fd = os.open(os.path.dirname(self.path), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def prune(self):
        excess = len(self.jobs) - KEEP_JOBS
        if excess <= 0:
            return
        for key in list(self.jobs):
            if excess <= 0:
                break
            if self.jobs[key]["status"] == "done":
                del self.jobs[key]
                excess -= 1


# --------------------------------------------------------------------------
# Home Assistant: alerts and the status entity
# --------------------------------------------------------------------------

class HomeAssistant:
    """Talks to Home Assistant through the Supervisor's Core API proxy
    (`homeassistant_api: true`): persistent notifications, and the state of
    STATUS_ENTITY. Calls run on a background thread so /print never waits on
    HA. Notifications get a few retries on network errors or 5xx; a state
    update gets none, because the next one replaces it within POLL_INTERVAL.
    Never a retry on a 4xx."""

    def __init__(self):
        self.token = (container_env("SUPERVISOR_TOKEN") or "").strip()
        self.base = container_env("NMT_HA_API", "http://supervisor/core/api").rstrip("/")
        self.queue = queue.Queue()
        self.state_failing = False

    def start(self):
        if not self.token:
            log("home assistant: no SUPERVISOR_TOKEN; alerts and the status entity are off")
        threading.Thread(target=self.run, daemon=True, name="homeassistant").start()

    def notify(self, nid, title, message):
        self.queue.put(("services/persistent_notification/create",
                        {"notification_id": nid, "title": title, "message": message}))

    def dismiss(self, nid):
        self.queue.put(("services/persistent_notification/dismiss", {"notification_id": nid}))

    def set_state(self, entity_id, state, attributes):
        self.queue.put((f"states/{entity_id}", {"state": state, "attributes": attributes}))

    def call(self, path, data):
        req = urllib.request.Request(
            f"{self.base}/{path}", data=json.dumps(data).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except (urllib.error.URLError, OSError):
            return None

    def run(self):
        while True:
            path, data = self.queue.get()
            if not self.token:
                continue
            if path.startswith("states/"):
                code = self.call(path, data)
                failing = code not in (200, 201)
                if failing != self.state_failing:  # log changes only: this runs every 30 s
                    self.state_failing = failing
                    log(f"home assistant: status entity {'not updated (' + str(code or 'no connection') + ')' if failing else 'updating again'}")
                continue
            for attempt in range(3):
                code = self.call(path, data)
                if code is not None and code < 500:
                    break
                time.sleep(10)
            outcome = "sent" if code == 200 else f"failed ({code or 'no connection'})"
            log(f"alert {path.rsplit('/', 1)[1]} {data['notification_id']} {outcome}")


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(ts)) if ts else None


def job_label(job):
    return "Test page" if job.get("kind") == "test" else f"Order {job.get('order')}"


def spool_path(job_id, ext):
    return os.path.join(SPOOL, f"{job_id}.{ext}")


def remove_files(job_id):
    try:
        os.remove(spool_path(job_id, "pdf"))
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------------

class Agent:
    def __init__(self, opts):
        self.opts = opts
        self.key = opts.get("signing_key") or ""
        self.name = opts.get("agent_name") or "downtown"
        self.dashboard = (opts.get("dashboard_url") or "https://dashboard.nmteaco.com").rstrip("/")
        self.result_url = self.dashboard + "/tools/webhook/printAgent.php"
        self.printer = Printer(opts["printer_uri"], opts["printer_ip"], opts.get("snmp_community") or "public")
        self.cups = Cups(opts["printer_uri"])
        self.store = Store(STATE_FILE)
        self.current = None  # job id being printed
        self.ha = HomeAssistant()
        self.printer_alerted = False

    def printer_blocked(self, reason):
        """An order wants to print and the printer can't: one alert per
        incident, cleared by printer_ok()."""
        if not self.printer_alerted:
            self.printer_alerted = True
            self.ha.notify(PRINTER_ALERT, "Downtown printer can't print",
                               f"The pickup printer reports **{reason}**. Orders can't print until it's fixed.")
            self.publish()

    def printer_ok(self):
        if self.printer_alerted:
            self.printer_alerted = False
            self.ha.dismiss(PRINTER_ALERT)

    # ---- results -----------------------------------------------------------

    def finish(self, job_id, outcome, before, after, error, started=None):
        """Record the outcome and queue the report, in one durable write, then
        delete the job's files."""
        with self.store.lock:
            job = self.store.jobs[str(job_id)]
            job.update(status="done", outcome=outcome, pages_before=before, pages_after=after, error=error,
                       finished=time.time())
            if self.current == job["id"]:
                self.current = None  # before publish(), or the entity shows it still printing
            self.store.outbox.append({
                "payload": {"action": "result", "agent": self.name, "id": job["id"], "outcome": outcome,
                            "pages_before": before, "pages_after": after, "error": error},
                "attempt": 0, "next": 0})
            self.store.prune()
            self.store.save()
            self.store.wake.notify_all()
        remove_files(job_id)
        self.publish()
        nid = f"nmt_print_agent_job_{job['id']}"
        if outcome == "failed":
            self.ha.notify(nid, "Pickup order didn't print",
                               f"{job_label(job)} (job {job['id']}) did not print: **{error}**.")
        elif outcome == "uncertain":
            self.ha.notify(nid, "Pickup order may not have printed",
                               f"{job_label(job)} (job {job['id']}) may not have printed: {error}. "
                               "Check the printer tray and reprint it from the curbside tablet if it's missing.")
        delta = after - before if before is not None and after is not None else None
        secs = f"{time.time() - started:.0f}s" if started else "-"
        log(f"job {job['id']} order={job['order']} kind={job['kind']} outcome={outcome} "
            f"pages_delta={delta if delta is not None else '?'} took={secs} error={error or '-'}")

    def recover(self):
        """A job marked printing when we start was in flight when the agent
        stopped. It may or may not have printed: report it uncertain, never
        print it again."""
        with self.store.lock:
            stale = [j for j in self.store.jobs.values() if j["status"] == "printing"]
        for job in stale:
            after = self.printer.page_count()
            self.finish(job["id"], "uncertain", job.get("pages_before"), after,
                        "agent restarted during the job")
        # Files left behind by a crash between finishing and deleting.
        with self.store.lock:
            live = {str(i) for i in self.store.queue}
        for name in os.listdir(SPOOL):
            if name.split(".")[0] not in live:
                os.remove(os.path.join(SPOOL, name))

    # ---- printing ----------------------------------------------------------

    def settle_counter(self, before, expect_change):
        """Poll the page counter until it has been stable for COUNTER_STABLE s.
        When the job completed we expect it to move, so an unchanged counter
        keeps us polling for up to COUNTER_WAIT s before concluding."""
        start = time.monotonic()
        value = self.printer.page_count()
        since = time.monotonic()
        while time.monotonic() - start < COUNTER_WAIT:
            time.sleep(2)
            v = self.printer.page_count()
            if v is None:
                continue
            if v != value:
                value, since = v, time.monotonic()
                continue
            stable = time.monotonic() - since >= COUNTER_STABLE
            moved = before is not None and value is not None and value != before
            if stable and (moved or not expect_change):
                break
        return value

    def run_job(self, job_id):
        key = str(job_id)
        started = time.time()
        with self.store.lock:
            job = self.store.jobs[key]
            job.update(status="printing", started=started)
            self.store.queue.remove(job["id"])
            self.current = job["id"]
            self.store.save()  # durable BEFORE anything reaches CUPS
        self.publish()
        try:
            before = self.printer.page_count()
            with self.store.lock:
                job["pages_before"] = before
                self.store.save()
            try:
                cups_id = self.cups.submit(spool_path(job_id, "pdf"), f"NMT job {job['id']}")
            except Exception as e:  # noqa: BLE001 — lp refused: nothing was queued
                self.finish(job_id, "failed", before, before, f"submit-failed: {str(e)[:120]}", started)
                return
            with self.store.lock:
                job["cups_job"] = cups_id
                self.store.save()

            deadline = time.monotonic() + CUPS_WAIT
            state = None
            while time.monotonic() < deadline:
                state = self.cups.job_state(cups_id)
                if state in ipp.JOB_TERMINAL:
                    break
                time.sleep(2)
            timed_out = state not in ipp.JOB_TERMINAL
            if timed_out:
                self.cups.cancel(cups_id)
                for _ in range(10):
                    time.sleep(1)
                    state = self.cups.job_state(cups_id)
                    if state in ipp.JOB_TERMINAL:
                        break

            after = self.settle_counter(before, expect_change=(state == ipp.JOB_COMPLETED and not timed_out))
            reason = (self.printer.poll(counter=False)["blocking"] or [None])[0]
            outcome, error = decide(state, timed_out, before, after, reason)
            if outcome != "printed" and state not in ipp.JOB_TERMINAL:
                self.cups.cancel(cups_id)
            self.finish(job_id, outcome, before, after, error, started)
        finally:
            with self.store.lock:
                self.current = None

    def worker(self):
        blocked_since = None
        while True:
            with self.store.lock:
                while not self.store.queue:
                    blocked_since = None
                    self.store.wake.wait(5)
                job_id = self.store.queue[0]
            snap = self.printer.poll(counter=False)
            reason = (snap["blocking"] or [None])[0]
            if reason is None and not self.cups.ensure_queue():
                reason = "offline"
            if reason:
                if blocked_since is None:
                    blocked_since = time.monotonic()
                    log(f"queue held: printer {reason}, {len(self.store.queue)} job(s) waiting")
                    self.printer_blocked(reason)
                if time.monotonic() - blocked_since >= BLOCKED_GRACE:
                    with self.store.lock:
                        held = list(self.store.queue)
                        self.store.queue.clear()
                    for jid in held:
                        self.finish(jid, "failed", None, None, reason)
                    blocked_since = None
                else:
                    time.sleep(5)
                continue
            if blocked_since is not None:
                log("queue released: printer ready")
                blocked_since = None
                self.printer_ok()
            try:
                self.run_job(job_id)
            except Exception as e:  # noqa: BLE001
                # Without a worker, accepted jobs would never print. Exit so the
                # Supervisor restarts us; recover() then reports this job uncertain.
                log(f"job {job_id} worker error {type(e).__name__}: {str(e)[:120]}; restarting")
                os._exit(1)

    def monitor(self):
        while True:
            snap = self.printer.poll()
            if snap["reachable"]:
                self.cups.ensure_queue()
            if not snap["blocking"]:
                self.printer_ok()
            self.publish()
            if not self.cups.alive():
                log("cupsd exited; stopping so the Supervisor restarts the add-on")
                os._exit(1)
            time.sleep(POLL_INTERVAL)

    # ---- reporting back ----------------------------------------------------

    def post_result(self, payload):
        """POST one result. Returns (verdict, detail): "ok"; "unknown" (the
        dashboard never sent that id); "reject" (a 4xx: resending the same
        report can't fix it); or "retry" (network error, 5xx, odd answer)."""
        body = json.dumps(payload, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        path = urllib.parse.urlsplit(self.result_url).path
        req = urllib.request.Request(self.result_url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": f"nmt-print-agent/{VERSION}",
            "X-NMT-Timestamp": ts,
            "X-NMT-Signature": sign(self.key, ts, "POST", path, body),
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                reply = json.loads(r.read(65536) or b"null")
                how = f"HTTP {r.status}"
                if isinstance(reply, dict) and reply.get("ok") is True:
                    return ("unknown" if reply.get("unknown") is True else "ok"), how
                return "retry", how + " without ok"
        except urllib.error.HTTPError as e:
            return ("retry" if e.code >= 500 else "reject"), f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            return "retry", type(e).__name__

    def reporter(self):
        while True:
            with self.store.lock:
                now = time.time()
                due = next((e for e in self.store.outbox if e["next"] <= now), None)
                if due is None:
                    self.store.wake.wait(1)
                    continue
            verdict, how = self.post_result(due["payload"])
            with self.store.lock:
                p = due["payload"]
                if verdict == "ok":
                    self.store.outbox.remove(due)
                    log(f"result job {p['id']} delivered ({how})")
                elif verdict == "unknown":
                    self.store.outbox.remove(due)
                    log(f"result job {p['id']}: dashboard does not know this id ({how}); not retried")
                elif verdict == "reject" or due["attempt"] >= len(RETRY_DELAYS):
                    # Give up. The outcome stays recorded, so the dashboard
                    # still learns it if it pushes the same job id again.
                    self.store.outbox.remove(due)
                    job = self.store.jobs.get(str(p["id"]), {"kind": None, "order": f"job {p['id']}"})
                    why = (f"refused it ({how})" if verdict == "reject"
                           else f"didn't accept it after {due['attempt']} retries ({how})")
                    log(f"result job {p['id']} not delivered: dashboard {why}; giving up")
                    self.ha.notify(
                        f"nmt_print_agent_report_{p['id']}", "Print result not reported",
                        f"{job_label(job)} (job {p['id']}): the outcome was **{p['outcome']}**, but the dashboard "
                        f"{why}. The dashboard may still show it as sending.")
                else:
                    n = due["attempt"]
                    due["attempt"] = n + 1
                    delay = RETRY_DELAYS[n]
                    due["next"] = time.time() + delay
                    log(f"result job {due['payload']['id']} not accepted ({how}); "
                        f"retry {n + 1} of {len(RETRY_DELAYS)} in {delay:g}s")
                self.store.save()

    # ---- HTTP --------------------------------------------------------------

    def publish(self):
        """Push STATUS_ENTITY: overall state, printer details and the last
        three jobs. Order numbers only, never job content."""
        snap = self.printer.current()
        with self.store.lock:
            queued = len(self.store.queue) + (1 if self.current is not None else 0)
            recent = list(self.store.jobs.values())[-3:][::-1]
        jobs = []
        for j in recent:
            b, a = j.get("pages_before"), j.get("pages_after")
            jobs.append({"id": j["id"], "order": j["order"], "kind": j["kind"],
                         "status": j.get("outcome") or j["status"],
                         "pages": a - b if a is not None and b is not None else None,
                         "error": j.get("error"),
                         "time": iso(j.get("finished") or j.get("started") or j.get("accepted"))})
        if not snap["reachable"]:
            state = "unreachable"
        elif snap["blocking"]:
            state = "problem"
        elif queued:
            state = "printing"
        else:
            state = "ready"
        self.ha.set_state(STATUS_ENTITY, state, {
            "friendly_name": "Instore Order Printing",
            "icon": "mdi:printer-check" if state in ("ready", "printing") else "mdi:printer-alert",
            "problem": (snap["blocking"] or [None])[0],
            "printer_state": snap["state"],
            "reasons": snap["reasons"],
            "page_count": snap["page_count"],
            "queue": queued,
            "last_jobs": jobs,
            "agent_version": VERSION,
            "checked": iso(snap["at"]),
        })

    def status(self):
        snap = self.printer.current()
        with self.store.lock:
            queued = len(self.store.queue) + (1 if self.current is not None else 0)
        return {"agent": self.name, "version": VERSION, "formats": ["pdf"],
                "printer": {"reachable": snap["reachable"], "state": snap["state"],
                            "reasons": snap["reasons"], "page_count": snap["page_count"]},
                "queue": queued}

    def accept(self, job):
        """Answer for a validated POST /print: (code, body)."""
        key = str(job["id"])
        with self.store.lock:
            seen = self.store.jobs.get(key)
            if seen:
                return 200, {"accepted": True, "duplicate": True, "outcome": seen.get("outcome") or "pending"}
        snap = self.printer.current()
        if time.time() - snap["at"] > PROBE_MAX_AGE:
            snap = self.printer.poll(counter=False, timeout=2.5)
        if snap["blocking"]:
            self.printer_blocked(snap["blocking"][0])
            return 503, {"accepted": False, "reason": snap["blocking"][0]}
        with self.store.lock:
            seen = self.store.jobs.get(key)  # a concurrent request may have won
            if seen:
                return 200, {"accepted": True, "duplicate": True, "outcome": seen.get("outcome") or "pending"}
            fd = os.open(spool_path(key, "pdf"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(job["pdf_bytes"])
            self.store.jobs[key] = {"id": job["id"], "order": job["order"], "kind": job["kind"],
                                    "status": "queued", "outcome": None, "accepted": time.time()}
            self.store.queue.append(job["id"])
            self.store.prune()
            self.store.save()
            self.store.wake.notify_all()
        self.publish()
        return 202, {"accepted": True}


def decide(state, timed_out, before, after, reason):
    """Outcome rules (INSTRUCTIONS.md, Phase 3):
    printed   = CUPS completed AND the page counter rose by at least 1;
    failed    = CUPS/the printer aborted the job and the counter did not move;
    uncertain = anything we cannot tell, including a job still unfinished
                after CUPS_WAIT. The dashboard resends failed jobs under a new
                id, so `failed` must mean nothing came out and nothing will."""
    known = before is not None and after is not None
    delta = after - before if known else None
    if state == ipp.JOB_COMPLETED and not timed_out:
        if known and delta >= 1:
            return "printed", None
        if not known:
            return "uncertain", "page counter unreadable"
        return "uncertain", reason or "printer reported the job done but its page counter did not move"
    if timed_out:
        # The printer may still hold the job (a jam, a network blip) and print
        # it later; a resend under a new id would then print twice.
        return "uncertain", f"timeout{': ' + reason if reason else ''}"
    if not known:
        return "uncertain", "page counter unreadable"
    if delta >= 1:
        return "uncertain", f"job aborted after the counter rose by {delta}"
    return "failed", reason or "job-aborted"


BAD_REQUEST = {"accepted": False, "reason": "bad-request"}


def parse_job(body):
    """A validated /print body with the PDF decoded into "pdf_bytes", or None.
    The dashboard reads the resulting 400 as "certainly not printed"."""
    try:
        job = json.loads(body)
    except ValueError:
        return None
    if not (isinstance(job, dict) and isinstance(job.get("id"), int) and not isinstance(job["id"], bool)
            and job["id"] > 0 and isinstance(job.get("order"), str) and len(job["order"]) <= 64
            and job.get("kind") in ("auto", "reprint", "test") and isinstance(job.get("pdf"), str)):
        return None
    try:
        pdf = base64.b64decode("".join(job["pdf"].split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not pdf.startswith(b"%PDF-") or len(pdf) > MAX_PDF:
        return None
    job["pdf_bytes"] = pdf
    del job["pdf"]
    return job


def make_handler(agent):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nmt-print-agent"
        sys_version = ""
        timeout = 15

        def log_message(self, fmt, *args):  # the default logs raw request lines
            pass

        def reply(self, code, body, job_id="-"):
            data = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            log(f"{self.command} {self.path.split('?')[0][:64]} job={job_id} -> {code}")

        def body(self):
            n = self.headers.get("Content-Length")
            if n is None:
                return b""
            if not n.isdigit() or int(n) > MAX_BODY:
                return None
            return self.rfile.read(int(n))

        def authed(self, body):
            return verify(agent.key, self.headers, self.command, self.path.split("?")[0], body)

        def do_GET(self):
            if self.path.split("?")[0] != "/status":
                return self.reply(404, {"error": "not found"})
            body = self.body()
            if body is None or not self.authed(body):
                return self.reply(401, {"error": "unauthorized"})
            self.reply(200, agent.status())

        def do_POST(self):
            if self.path.split("?")[0] != "/print":
                return self.reply(404, {"error": "not found"})
            body = self.body()
            if body is None:  # over the size limit: unread, so drop the connection
                self.close_connection = True
                return self.reply(400, BAD_REQUEST)
            if not self.authed(body):
                return self.reply(401, {"error": "unauthorized"})
            job = parse_job(body)
            if job is None:
                return self.reply(400, BAD_REQUEST)
            code, answer = agent.accept(job)
            self.reply(code, answer, job["id"])

    return Handler


def main():
    with open(os.path.join(DATA, "options.json")) as f:
        opts = json.load(f)
    os.makedirs(SPOOL, mode=0o700, exist_ok=True)
    agent = Agent(opts)
    if len(agent.key) < 32:
        log("WARNING: signing_key is not set (or shorter than 32 characters); every request will be refused")
    log(f"nmt-print-agent {VERSION} starting: agent={agent.name} printer={opts['printer_uri']}")

    agent.cups.start()

    def stop(signum, _frame):
        log(f"signal {signum}: stopping")
        agent.cups.stop()
        os._exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    agent.ha.start()
    snap = agent.printer.poll()
    agent.recover()
    if snap["reachable"]:
        agent.cups.ensure_queue()
    if not snap["blocking"]:
        agent.ha.dismiss(PRINTER_ALERT)  # one raised before a restart may be stale
    for target in (agent.worker, agent.monitor, agent.reporter):
        threading.Thread(target=target, daemon=True, name=target.__name__).start()

    port = int(opts.get("listen_port") or 8080)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(agent))
    server.daemon_threads = True
    log(f"listening on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
