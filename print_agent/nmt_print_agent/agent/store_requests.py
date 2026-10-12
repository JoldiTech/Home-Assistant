"""Typed requests for the store agent: POST /v1/requests and friends.

Printing keeps its own route, queue and state file (agent.py). Everything
else the dashboard asks of the box arrives here as a typed, signed request
and runs on its handler's own worker thread, so a slow or failing handler
can never hold up a print job or another handler.

Rules this file is built around:
- A request is written to /data before it runs, bound to the hash of its
  whole validated content (type, target, dry_run, expiry, payload). An exact
  repeat is answered from the record and never runs again; the same id with
  different content is a conflict.
- At most once on the wire. A handler that sends something marks the record
  durably *before* the send. A record found mid-send after a restart is
  `uncertain` and is never replayed.
- Expiry is checked when a request is accepted and again just before it is
  sent, so an outage never delivers yesterday's alert.
- Records and logs carry ids, types, targets, states and error codes. Never a
  payload's text, never a header value.

Handlers live in a static registry (handlers.py). Callers name a configured
target id; nothing here accepts a host, address, port, URL, path or command
from a request.
"""

import collections
import datetime
import hashlib
import json
import os
import queue
import re
import threading
import time

API_VERSION = 1
MAX_BODY = 16 * 1024            # bytes of JSON in one POST /v1/requests
RETENTION_S = 7 * 24 * 3600     # how long a finished request (and its id) is remembered
MAX_RECORDS = 5000              # hard cap; the oldest finished records go first
RECENT_ERRORS = 20
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
TERMINAL = {"completed", "dry_run_passed", "sent", "failed", "uncertain", "expired"}
ENVELOPE_KEYS = {"version", "request_id", "type", "target", "dry_run", "expires_at", "payload"}


class Reject(Exception):
    """A request refused before anything was stored: HTTP status, stable
    code, short safe message."""

    def __init__(self, http, code, message):
        super().__init__(message)
        self.http, self.code, self.message = http, code, message


def iso(ts):
    if ts is None:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value):
    """An ISO-8601 time with an explicit UTC offset ("Z" or "+00:00") as a
    unix timestamp, or None. Local times without an offset are refused: a
    request's expiry must not depend on whose clock read it."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]00:00)", value):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp()


def content_hash(envelope):
    canon = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode()).hexdigest()


class Engine:
    """The durable record of every request, one bounded queue and worker
    thread per handler, and the read-only views the routes return."""

    def __init__(self, path, handlers, log, clock=time.time):
        self.path = path
        self.handlers = {h.type: h for h in handlers}
        self.log = log
        self.clock = clock
        self.lock = threading.RLock()
        self.records = {}
        self.queues = {t: queue.Queue(maxsize=h.queue_max) for t, h in self.handlers.items()}
        self.accepted_at = {t: collections.deque() for t in self.handlers}  # for rate limits
        self.recent_errors = collections.deque(maxlen=RECENT_ERRORS)
        self.started = clock()
        try:
            with open(path) as f:
                self.records = json.load(f).get("requests", {})
        except FileNotFoundError:
            pass

    # ---- durability ----------------------------------------------------------

    def save(self):
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"version": API_VERSION, "requests": self.records}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            fd = os.open(os.path.dirname(self.path) or ".", os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def prune(self):
        now = self.clock()
        with self.lock:
            done = [r for r in self.records.values() if r["state"] in TERMINAL]
            for r in done:
                if now - (r.get("finished_at") or r["accepted_at"]) > RETENTION_S:
                    del self.records[r["request_id"]]
            excess = len(self.records) - MAX_RECORDS
            if excess > 0:
                for r in sorted((r for r in self.records.values() if r["state"] in TERMINAL),
                                key=lambda r: r["accepted_at"])[:excess]:
                    del self.records[r["request_id"]]

    def recover(self):
        """Requests found unfinished at start. Not yet started: queued again
        (the expiry check before sending still applies). Started but not yet
        on the wire: failed, nothing went out. Mid-send: uncertain, never
        replayed."""
        requeue = []
        with self.lock:
            for r in sorted(self.records.values(), key=lambda r: r["accepted_at"]):
                if r["state"] == "accepted":
                    requeue.append(r)
                elif r["state"] == "running":
                    if r.get("send_started_at"):
                        self._finish(r, "uncertain", "agent_restarted_mid_send",
                                     "the agent restarted while sending; it may or may not have gone out")
                    else:
                        self._finish(r, "failed", "agent_restarted",
                                     "the agent restarted before sending; nothing was sent")
            self.prune()
            self.save()
        for r in requeue:
            try:
                self.queues[r["type"]].put_nowait(r["request_id"])
            except (KeyError, queue.Full):
                with self.lock:
                    self._finish(r, "failed", "not_requeued", "could not be queued again after a restart")
                    self.save()

    # ---- accepting -----------------------------------------------------------

    def validate(self, body):
        """The validated envelope (with the handler's cleaned payload), or
        Reject. Checks shape only; ids, expiry and availability come later."""
        try:
            req = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise Reject(422, "invalid_json", "the body is not JSON")
        if not isinstance(req, dict):
            raise Reject(422, "invalid_request", "the body must be a JSON object")
        extra = set(req) - ENVELOPE_KEYS
        if extra:
            raise Reject(422, "unknown_field", "unknown field(s): " + ", ".join(sorted(extra))[:120])
        if req.get("version") != API_VERSION or isinstance(req.get("version"), bool):
            raise Reject(422, "unsupported_version", f"version must be {API_VERSION}")
        rid = req.get("request_id")
        if not isinstance(rid, str) or not REQUEST_ID.fullmatch(rid):
            raise Reject(422, "invalid_request_id",
                         "request_id: 8-128 characters of A-Z a-z 0-9 . _ : - starting with a letter or digit")
        handler = self.handlers.get(req.get("type"))
        if handler is None:
            raise Reject(422, "unknown_handler", "type is not a handler this agent runs")
        if not isinstance(req.get("dry_run"), bool):
            raise Reject(422, "dry_run_required", "dry_run must be true or false, explicitly")
        target = req.get("target")
        if not isinstance(target, str) or target not in handler.targets():
            raise Reject(422, "invalid_target", "target is not a configured target id for this type")
        expires = parse_utc(req.get("expires_at"))
        if expires is None:
            raise Reject(422, "invalid_expires_at", "expires_at must be an ISO-8601 UTC time, e.g. 2026-01-01T00:00:00Z")
        payload = req.get("payload")
        if not isinstance(payload, dict):
            raise Reject(422, "invalid_payload", "payload must be a JSON object")
        clean, problem = handler.validate_payload(payload, target, req["dry_run"])
        if problem:
            raise Reject(422, "invalid_payload", problem)
        return {"version": API_VERSION, "request_id": rid, "type": handler.type, "target": target,
                "dry_run": req["dry_run"], "expires_at": req["expires_at"], "payload": clean}, expires

    def submit(self, body):
        """POST /v1/requests: (http status, response body)."""
        try:
            env, expires = self.validate(body)
        except Reject as e:
            return e.http, self.error(e.code, e.message)
        handler = self.handlers[env["type"]]
        digest = content_hash(env)
        now = self.clock()
        with self.lock:
            seen = self.records.get(env["request_id"])
            if seen is not None:
                if seen["hash"] != digest:
                    return 409, self.error("request_id_conflict",
                                           "this request_id was already used for different content")
                return 200, dict(self.view(seen), duplicate=True)
            if expires <= now:
                return 410, self.error("expired", "expires_at is already in the past")
            if expires - now > handler.max_ttl:
                return 422, self.error("ttl_too_long", f"expires_at may be at most {handler.max_ttl:g} s ahead")
            ok, code, why = handler.permitted(env["target"], env["payload"], env["dry_run"])
            if not ok:
                return (503 if code == "handler_unavailable" else 422), self.error(code, why)
            window = self.accepted_at[handler.type]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= handler.per_minute:
                return 429, self.error("rate_limited", f"at most {handler.per_minute} {handler.type} requests a minute")
            if self.queues[handler.type].full():
                return 503, self.error("queue_full", "this handler's queue is full; try again shortly")
            record = {
                "request_id": env["request_id"], "hash": digest, "type": handler.type,
                "target": env["target"], "dry_run": env["dry_run"], "expires_at": expires,
                "payload": env["payload"], "state": "accepted", "accepted_at": now,
                "started_at": None, "finished_at": None, "attempts": 0,
                "handler_version": handler.version, "evidence": {}, "result": None, "error": None,
            }
            self.records[env["request_id"]] = record
            self.prune()
            self.save()  # durable before it can run
            window.append(now)
            answer = self.view(record)  # before a worker can pick it up
            self.queues[handler.type].put_nowait(env["request_id"])
        self.log(f"request {env['request_id']} type={handler.type} target={env['target']} "
                 f"dry_run={env['dry_run']} accepted")
        return 202, answer

    # ---- running -------------------------------------------------------------

    def _finish(self, r, state, code=None, message=None, evidence=None, result=None):
        r["state"] = state
        r["finished_at"] = self.clock()
        if evidence:
            r["evidence"].update(evidence)
        if result is not None:
            r["result"] = result
        if code:
            r["error"] = {"code": code, "message": message}
            self.recent_errors.appendleft({"at": r["finished_at"], "type": r["type"], "code": code})
        # A finished request needs only its hash to answer repeats; the
        # payload (a name and number, one day a customer's) goes.
        r.pop("payload", None)

    def worker(self, handler):
        q = self.queues[handler.type]
        while True:
            rid = q.get()
            with self.lock:
                r = self.records.get(rid)
                if r is None or r["state"] != "accepted":
                    continue
                if self.clock() >= r["expires_at"]:
                    self._finish(r, "expired", "expired", "not run: its expiry passed while it waited")
                    self.save()
                    self.log(f"request {rid} expired before it ran")
                    continue
                r.update(state="running", started_at=self.clock(), attempts=r["attempts"] + 1)
                self.save()
            try:
                outcome = handler.execute(dict(r), self._send_gate(r))
            except Exception as e:  # noqa: BLE001 — a handler bug must not kill the worker
                outcome = {"state": "uncertain" if r.get("send_started_at") else "failed",
                           "code": "handler_error", "message": type(e).__name__}
            with self.lock:
                self._finish(r, outcome["state"], outcome.get("code"), outcome.get("message"),
                             outcome.get("evidence"), outcome.get("result"))
                self.prune()
                self.save()
            self.log(f"request {rid} type={r['type']} target={r['target']} -> {r['state']}"
                     f"{' (' + r['error']['code'] + ')' if r.get('error') else ''}")

    def _send_gate(self, r):
        """Called by a handler immediately before it puts bytes on the wire.
        Re-checks expiry and records, durably, that the send has begun.
        Returns False (and the handler must not send) once expired."""
        def gate():
            with self.lock:
                if self.clock() >= r["expires_at"]:
                    return False
                r["send_started_at"] = self.clock()
                self.save()
                return True
        return gate

    def start(self):
        for h in self.handlers.values():
            threading.Thread(target=self.worker, args=(h,), daemon=True, name=f"handler:{h.type}").start()

    # ---- views ---------------------------------------------------------------

    @staticmethod
    def error(code, message):
        return {"agent_api": API_VERSION, "error": {"code": code, "message": message}}

    def view(self, r):
        h = self.handlers.get(r["type"])
        out = {
            "agent_api": API_VERSION,
            "request_id": r["request_id"], "type": r["type"], "target": r["target"], "dry_run": r["dry_run"],
            "state": r["state"], "terminal": r["state"] in TERMINAL,
            "expires_at": iso(r["expires_at"]), "accepted_at": iso(r["accepted_at"]),
            "started_at": iso(r.get("started_at")), "send_started_at": iso(r.get("send_started_at")),
            "finished_at": iso(r.get("finished_at")), "attempts": r["attempts"],
            "handler_version": r["handler_version"], "evidence": r.get("evidence") or {},
            "result": r.get("result"), "error": r.get("error"),
        }
        if h is not None and hasattr(h, "observations"):
            out["observations"] = h.observations(r)
        return out

    def get(self, rid):
        if not isinstance(rid, str) or not REQUEST_ID.fullmatch(rid):
            return 422, self.error("invalid_request_id", "not a valid request_id")
        with self.lock:
            r = self.records.get(rid)
            if r is None:
                return 404, self.error("unknown_request", "no request with this id (or it is older than the retention)")
            return 200, self.view(r)

    def queue_stats(self):
        now = self.clock()
        with self.lock:
            out = {}
            for t in self.handlers:
                waiting = [r for r in self.records.values() if r["type"] == t and r["state"] in ("accepted", "running")]
                oldest = min((r["accepted_at"] for r in waiting), default=None)
                out[t] = {"depth": len(waiting), "max": self.handlers[t].queue_max,
                          "oldest_age_s": round(now - oldest, 1) if oldest else None}
            return out

    def last_for_target(self, type_, target):
        with self.lock:
            rs = [r for r in self.records.values() if r["type"] == type_ and r["target"] == target]
            if not rs:
                return None
            r = max(rs, key=lambda r: r["accepted_at"])
            return {"request_id": r["request_id"], "state": r["state"], "dry_run": r["dry_run"],
                    "at": iso(r.get("finished_at") or r["accepted_at"]),
                    "error": (r.get("error") or {}).get("code"), "evidence": r.get("evidence") or {}}

    def errors(self):
        with self.lock:
            return [dict(e, at=iso(e["at"])) for e in self.recent_errors]
