#!/usr/bin/env python3
"""NMT downtown print agent.

The dashboard pushes pickup-order HTML to POST /print. The agent renders it
to a Letter PDF, prints it through a driverless CUPS queue, confirms with the
printer's page counter, and reports the outcome back to the dashboard. The
contract is in print_agent/INSTRUCTIONS.md (Phase 3) in the repository.

Rules this file is built around:
- Never print a job twice: a job id is recorded in /data before anything is
  sent to CUPS, a repeated id is answered from the record, and a job that was
  in flight when the agent stopped is reported `uncertain`, never resent.
- Never log job content or header values: logs carry ids, order numbers,
  timings, page counts and errors only.
"""

import hashlib
import hmac
import json
import logging
import os
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

VERSION = "1.0.0"


def _env_num(name, default):
    # Timings are overridable only so the test harness can shorten them.
    return float(os.environ.get(name, default))


DATA = os.environ.get("NMT_DATA", "/data")
STATE_FILE = os.path.join(DATA, "state.json")
SPOOL = os.path.join(DATA, "spool")
CUPS_QUEUE = "downtown"
CUPS_URI = f"ipp://localhost:631/printers/{CUPS_QUEUE}"
PAGE_COUNT_OID = "1.3.6.1.2.1.43.10.2.1.4.1.1"
LETTER = "na_letter_8.5x11in"

MAX_SKEW = 300            # seconds a signed timestamp may differ from our clock
MAX_BODY = 5 * 1024 * 1024
KEEP_JOBS = 1000          # job ids remembered for duplicate answers
CUPS_WAIT = _env_num("NMT_CUPS_WAIT", 180)        # wait for CUPS to finish a job
COUNTER_STABLE = _env_num("NMT_COUNTER_STABLE", 10)
COUNTER_WAIT = _env_num("NMT_COUNTER_WAIT", 60)
BLOCKED_GRACE = _env_num("NMT_BLOCKED_GRACE", 600)  # hold before failing held jobs
POLL_INTERVAL = _env_num("NMT_POLL_INTERVAL", 30)
PROBE_MAX_AGE = 10        # /print re-probes the printer if the cached state is older
RETRY_DELAYS = [5, 30, 120]
RETRY_EVERY = 300

# Printer-state-reasons that mean the printer cannot print, after stripping the
# -error/-warning/-report suffix and folding synonyms into the names the
# dashboard knows (media-empty, media-jam, door-open, offline, unreachable).
BLOCKING = {
    "media-empty", "media-jam", "door-open", "offline", "unreachable",
    "toner-empty", "input-tray-missing", "output-area-full", "marker-waste-full",
    "paused", "spool-area-full",
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


def spool_path(job_id, ext):
    return os.path.join(SPOOL, f"{job_id}.{ext}")


def remove_files(job_id):
    for ext in ("html", "pdf"):
        try:
            os.remove(spool_path(job_id, ext))
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
        self.result_url = self.dashboard + "/tools/orders/curbsideAgent.php"
        self.printer = Printer(opts["printer_uri"], opts["printer_ip"], opts.get("snmp_community") or "public")
        self.cups = Cups(opts["printer_uri"])
        self.store = Store(STATE_FILE)
        self.current = None  # job id being printed

    # ---- results -----------------------------------------------------------

    def finish(self, job_id, outcome, before, after, error, started=None, pages=None):
        """Record the outcome and queue the report, in one durable write, then
        delete the job's files."""
        with self.store.lock:
            job = self.store.jobs[str(job_id)]
            job.update(status="done", outcome=outcome, pages_before=before, pages_after=after, error=error)
            self.store.outbox.append({
                "payload": {"action": "result", "agent": self.name, "id": job["id"], "outcome": outcome,
                            "pages_before": before, "pages_after": after, "error": error},
                "attempt": 0, "next": 0})
            self.store.prune()
            self.store.save()
            self.store.wake.notify_all()
        remove_files(job_id)
        delta = after - before if before is not None and after is not None else None
        secs = f"{time.time() - started:.0f}s" if started else "-"
        log(f"job {job['id']} order={job['order']} kind={job['kind']} outcome={outcome} "
            f"pages_delta={delta if delta is not None else '?'} rendered_pages={pages or '?'} "
            f"took={secs} error={error or '-'}")

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

    def render(self, job_id):
        from weasyprint import CSS, HTML  # imported lazily: slow to load
        with open(spool_path(job_id, "html"), encoding="utf-8") as f:
            html = f.read()
        doc = HTML(string=html, base_url=self.dashboard + "/", url_fetcher=self.fetch).render(
            stylesheets=[CSS(string="@page { size: letter !important; }")])
        doc.write_pdf(spool_path(job_id, "pdf"))
        return len(doc.pages)

    def fetch(self, url, timeout=10, ssl_context=None):
        """Resources a job may load: inline data: URIs, and files from the
        dashboard's own host. Nothing else, so no job content or URL leaves
        for a third party."""
        from weasyprint import default_url_fetcher
        u = urllib.parse.urlsplit(url)
        if u.scheme == "data" or (u.scheme == "https" and u.hostname == urllib.parse.urlsplit(self.dashboard).hostname):
            return default_url_fetcher(url, timeout=timeout)
        raise ValueError("resource host not allowed")

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
        try:
            try:
                pages = self.render(job_id)
            except Exception as e:  # noqa: BLE001 — any render failure means nothing printed
                self.finish(job_id, "failed", None, None, f"render-failed: {type(e).__name__}", started)
                return
            before = self.printer.page_count()
            with self.store.lock:
                job["pages_before"] = before
                self.store.save()
            try:
                cups_id = self.cups.submit(spool_path(job_id, "pdf"), f"NMT job {job['id']}")
            except Exception as e:  # noqa: BLE001 — lp refused: nothing was queued
                self.finish(job_id, "failed", before, before, f"submit-failed: {str(e)[:120]}", started, pages)
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
            self.finish(job_id, outcome, before, after, error, started, pages)
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
            try:
                self.run_job(job_id)
            except Exception as e:  # noqa: BLE001
                # Without a worker, accepted jobs would never print. Exit so the
                # Supervisor restarts us; recover() then reports this job uncertain.
                log(f"job {job_id} worker error {type(e).__name__}: {str(e)[:120]}; restarting")
                os._exit(1)

    def monitor(self):
        while True:
            self.printer.poll()
            if self.printer.current()["reachable"]:
                self.cups.ensure_queue()
            if not self.cups.alive():
                log("cupsd exited; stopping so the Supervisor restarts the add-on")
                os._exit(1)
            time.sleep(POLL_INTERVAL)

    # ---- reporting back ----------------------------------------------------

    def post_result(self, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        path = urllib.parse.urlsplit(self.result_url).path
        req = urllib.request.Request(self.result_url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-NMT-Timestamp": ts,
            "X-NMT-Signature": sign(self.key, ts, "POST", path, body),
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                reply = json.loads(r.read(65536) or b"null")
                return isinstance(reply, dict) and reply.get("ok") is True, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, type(e).__name__

    def reporter(self):
        while True:
            with self.store.lock:
                now = time.time()
                due = next((e for e in self.store.outbox if e["next"] <= now), None)
                if due is None:
                    self.store.wake.wait(1)
                    continue
            ok, how = self.post_result(due["payload"])
            with self.store.lock:
                if ok:
                    self.store.outbox.remove(due)
                    log(f"result job {due['payload']['id']} delivered ({how})")
                else:
                    n = due["attempt"]
                    due["attempt"] = n + 1
                    delay = RETRY_DELAYS[n] if n < len(RETRY_DELAYS) else RETRY_EVERY
                    due["next"] = time.time() + delay
                    log(f"result job {due['payload']['id']} not accepted ({how}); retry in {delay}s")
                self.store.save()

    # ---- HTTP --------------------------------------------------------------

    def status(self):
        snap = self.printer.current()
        with self.store.lock:
            queued = len(self.store.queue) + (1 if self.current is not None else 0)
        return {"agent": self.name, "version": VERSION,
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
            return 503, {"accepted": False, "reason": snap["blocking"][0]}
        with self.store.lock:
            seen = self.store.jobs.get(key)  # a concurrent request may have won
            if seen:
                return 200, {"accepted": True, "duplicate": True, "outcome": seen.get("outcome") or "pending"}
            with open(spool_path(key, "html"), "w", encoding="utf-8") as f:
                f.write(job["html"])
            self.store.jobs[key] = {"id": job["id"], "order": job["order"], "kind": job["kind"],
                                    "status": "queued", "outcome": None, "accepted": time.time()}
            self.store.queue.append(job["id"])
            self.store.prune()
            self.store.save()
            self.store.wake.notify_all()
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
            if body is None:
                return self.reply(413, {"error": "too large"})
            if not self.authed(body):
                return self.reply(401, {"error": "unauthorized"})
            try:
                job = json.loads(body)
                ok = (isinstance(job, dict) and isinstance(job.get("id"), int) and not isinstance(job["id"], bool)
                      and job["id"] > 0 and isinstance(job.get("order"), str) and len(job["order"]) <= 64
                      and job.get("kind") in ("auto", "reprint", "test")
                      and isinstance(job.get("html"), str) and job["html"])
            except ValueError:
                ok = False
            if not ok:
                return self.reply(400, {"error": "bad request"})
            code, answer = agent.accept(job)
            self.reply(code, answer, job["id"])

    return Handler


def main():
    # WeasyPrint/fontTools warnings can quote job markup or URLs: silence them.
    logging.basicConfig(level=logging.CRITICAL)
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

    agent.printer.poll()
    agent.recover()
    if agent.printer.current()["reachable"]:
        agent.cups.ensure_queue()
    for target in (agent.worker, agent.monitor, agent.reporter):
        threading.Thread(target=target, daemon=True, name=target.__name__).start()

    port = int(opts.get("listen_port") or 8080)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(agent))
    server.daemon_threads = True
    log(f"listening on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
