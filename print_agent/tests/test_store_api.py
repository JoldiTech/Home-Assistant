"""Tests for the store agent's typed-request API (/v1): no CUPS, printer,
Revel or network beyond localhost needed.

    python3 print_agent/tests/test_store_api.py

The agent's real HTTP handler serves on 127.0.0.1; a UDP socket on
127.0.0.1 stands in for a register listening for caller-ID records.
"""

import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

TMP = tempfile.mkdtemp()
os.environ["NMT_DATA"] = TMP
for v in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
    os.environ.pop(v, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nmt_print_agent", "agent"))

import agent  # noqa: E402
import callerid  # noqa: E402
import store_requests  # noqa: E402
from store_requests import iso  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from test_agent import FakeHA, job, snap  # noqa: E402

KEY = "k" * 64
TEST_NUMBER = "505-555-0100"


def listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.3)
    return s


def closed_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def drain(sock, wait=0.3):
    got = []
    end = time.time() + wait
    while time.time() < end:
        try:
            got.append(sock.recv(2048))
        except socket.timeout:
            pass
    return got


def opts(port, mode="test"):
    return {"signing_key": KEY, "agent_name": "downtown", "dashboard_url": "https://dash.example",
            "printer_uri": "ipp://192.0.2.1/ipp/print", "printer_ip": "192.0.2.1",
            "lan_subnet": "127.0.0.0/8", "revel_caller_id_mode": mode, "revel_caller_id_port": port,
            "revel_test_number": TEST_NUMBER,
            "revel_targets": [{"id": "pink-ipad", "name": "Pink Ipad", "ip": "127.0.0.1", "revel_station": 14, "test": True},
                              {"id": "blue-ipad", "name": "Blue Ipad", "ip": "127.0.0.2", "revel_station": 12, "test": False}]}


class Base(unittest.TestCase):
    mode = "test"

    def setUp(self):
        for name in os.listdir(TMP):
            p = os.path.join(TMP, name)
            if os.path.isfile(p):
                os.remove(p)
        os.makedirs(agent.SPOOL, exist_ok=True)
        self.register = listener()
        self.agent = self.make_agent()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), agent.make_handler(self.agent))
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.n = 0

    def make_agent(self, start=True):
        a = agent.Agent(opts(self.register.getsockname()[1], self.mode))
        a.printer.snap = snap()
        a.ha = FakeHA()
        a.requests.recover()
        if start:
            a.requests.start()
        return a

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.register.close()

    # ---- client --------------------------------------------------------------

    def call(self, method, path, body=b"", signed=True, ts=None):
        h = {"Content-Type": "application/json"}
        if signed:
            t = str(ts or int(time.time()))
            h.update({"X-NMT-Timestamp": t, "X-NMT-Signature": agent.sign(KEY, t, method, path, body)})
        req = urllib.request.Request(self.base + path, data=body if method == "POST" else None, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null"), dict(e.headers)

    def envelope(self, type_="diagnostic.echo", target="agent", payload=None, dry_run=True, ttl=60, rid=None, **extra):
        self.n += 1
        if payload is None:
            payload = {"challenge": f"chal-{self.n:04d}-xyz"}
        env = {"version": 1, "request_id": rid or f"test:{self.id().rsplit('.', 1)[1][:40]}:{self.n}",
               "type": type_, "target": target, "dry_run": dry_run,
               "expires_at": iso(time.time() + ttl), "payload": payload}
        env.update(extra)
        return env

    def submit(self, env):
        return self.call("POST", "/v1/requests", json.dumps(env).encode())

    def wait(self, rid, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            code, body, _ = self.call("GET", f"/v1/requests/{rid}")
            if body.get("terminal"):
                return body
            time.sleep(0.05)
        self.fail(f"{rid} not finished: {body}")

    def caller(self, target="pink-ipad", name="TEST CURBSIDE", number=TEST_NUMBER, dry_run=True, **kw):
        return self.envelope("revel.caller_id", target, {"name": name, "number": number}, dry_run=dry_run, **kw)


class Auth(Base):
    def test_every_route_needs_a_signature(self):
        for method, path in (("GET", "/status"), ("GET", "/v1/capabilities"), ("GET", "/v1/targets"),
                             ("GET", "/v1/diagnostics"), ("GET", "/v1/requests/test:abcdefgh"),
                             ("POST", "/v1/requests"), ("POST", "/print")):
            code, body, _ = self.call(method, path, b"{}" if method == "POST" else b"", signed=False)
            self.assertEqual(code, 401, path)
            self.assertNotIn("agent", json.dumps(body), "nothing about the agent leaks")
        code, _, _ = self.call("GET", "/v1/capabilities", ts=int(time.time()) - 400)
        self.assertEqual(code, 401, "stale clock")

    def test_signature_binds_the_path(self):
        t = str(int(time.time()))
        h = {"X-NMT-Timestamp": t, "X-NMT-Signature": agent.sign(KEY, t, "GET", "/v1/targets", b"")}
        req = urllib.request.Request(self.base + "/v1/diagnostics", headers=h)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(e.exception.code, 401)

    def test_unknown_paths_and_query_strings(self):
        self.assertEqual(self.call("GET", "/v1/nothing")[0], 404)
        t = str(int(time.time()))
        h = {"X-NMT-Timestamp": t, "X-NMT-Signature": agent.sign(KEY, t, "GET", "/v1/capabilities", b"")}
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(urllib.request.Request(self.base + "/v1/capabilities?x=1", headers=h), timeout=5)
        self.assertEqual(e.exception.code, 400, "a query string is refused even when the path is signed")

    def test_no_store(self):
        _, _, headers = self.call("GET", "/v1/capabilities")
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class StatusCompat(Base):
    def test_status_keeps_its_fields(self):
        code, body, _ = self.call("GET", "/status")
        self.assertEqual(code, 200)
        for k in ("agent", "version", "formats", "printer", "queue"):
            self.assertIn(k, body)
        self.assertEqual(body["formats"], ["pdf"])
        self.assertEqual(set(body["printer"]), {"reachable", "state", "reasons", "page_count"})
        self.assertEqual(body["handlers"], ["print", "diagnostic.echo", "diagnostic.probe", "revel.caller_id"])

    def test_print_route_unchanged(self):
        import base64
        from testpdf import make_pdf
        b = json.dumps({"id": 2100009001, "order": "TEST", "kind": "test",
                        "pdf": base64.b64encode(make_pdf()).decode()}).encode()
        self.agent.store.wake = threading.Condition(self.agent.store.lock)  # no print worker running here
        self.assertEqual(self.call("POST", "/print", b)[:2], (202, {"accepted": True}))
        self.assertEqual(self.call("POST", "/print", b)[:2], (200, {"accepted": True, "duplicate": True, "outcome": "pending"}))
        self.assertFalse(os.path.exists(store_requests.__file__.replace(".py", ".json")))
        with open(agent.STATE_FILE) as f:
            self.assertEqual(json.load(f)["queue"], [2100009001], "print state file is the same shape")


class Echo(Base):
    def test_challenge_returns_through_the_queue_once(self):
        runs = []
        h = self.agent.requests.handlers["diagnostic.echo"]
        real = h.execute
        h.execute = lambda r, g: (runs.append(r["request_id"]), real(r, g))[1]
        env = self.envelope(dry_run=False)
        code, body, _ = self.submit(env)
        self.assertEqual((code, body["state"]), (202, "accepted"))
        done = self.wait(env["request_id"])
        self.assertEqual((done["state"], done["result"]), ("completed", {"challenge": env["payload"]["challenge"]}))
        self.assertEqual(done["evidence"]["outbound_sends"], 0)
        # Exact repeat: stored answer, no second run.
        code, again, _ = self.submit(env)
        self.assertEqual((code, again["duplicate"], again["state"]), (200, True, "completed"))
        time.sleep(0.2)
        self.assertEqual(runs, [env["request_id"]])
        # Same id, other content: 409, no run.
        changed = dict(env, payload={"challenge": "something-else"})
        code, body, _ = self.submit(changed)
        self.assertEqual((code, body["error"]["code"]), (409, "request_id_conflict"))
        self.assertEqual(runs, [env["request_id"]])

    def test_unknown_request_id(self):
        code, body, _ = self.call("GET", "/v1/requests/test:never-sent")
        self.assertEqual((code, body["error"]["code"]), (404, "unknown_request"))
        self.assertEqual(self.call("GET", "/v1/requests/bad%20id")[0], 422)


class Rejections(Base):
    def test_rejected_without_effects(self):
        cases = [
            (dict(self.envelope(), version=2), 422, "unsupported_version"),
            (dict(self.envelope(), type="shell.exec"), 422, "unknown_handler"),
            (dict(self.envelope(), type="print"), 422, "unknown_handler"),
            (self.envelope(target="192.168.22.91"), 422, "invalid_target"),
            (self.caller(target="agent"), 422, "invalid_target"),
            (self.envelope(payload={"challenge": "x"}), 422, "invalid_payload"),
            (self.envelope(payload={"challenge": "abcdefgh", "cmd": "ls"}), 422, "invalid_payload"),
            ({k: v for k, v in self.envelope().items() if k != "dry_run"}, 422, "dry_run_required"),
            (dict(self.envelope(), dry_run="false"), 422, "dry_run_required"),
            (self.envelope(rid="short"), 422, "invalid_request_id"),
            (self.envelope(rid="has/slash/in-it"), 422, "invalid_request_id"),
            (dict(self.envelope(), expires_at="2026-10-12T10:00:00"), 422, "invalid_expires_at"),
            (dict(self.envelope(), host="192.168.22.1"), 422, "unknown_field"),
            (self.envelope(ttl=-5), 410, "expired"),
            (self.envelope(ttl=3600), 422, "ttl_too_long"),
            (self.caller(ttl=300), 422, "ttl_too_long"),
            (self.caller(name="TEST CURBSIDE ORDER"), 422, "invalid_payload"),  # 19 chars: refused, not cut
            (self.caller(name="TEST<^$>"), 422, "invalid_payload"),
            (self.caller(number="505-555-01001"), 422, "invalid_payload"),
            (self.caller(number="+1 505 555 0100"), 422, "invalid_payload"),
        ]
        for env, code, err in cases:
            got, body, _ = self.submit(env)
            self.assertEqual((got, body["error"]["code"]), (code, err), env)
        self.assertEqual(self.call("POST", "/v1/requests", b"not json")[1]["error"]["code"], "invalid_json")
        self.assertEqual(self.agent.requests.records, {}, "nothing stored")
        self.assertEqual(drain(self.register), [], "nothing sent")

    def test_oversized_body(self):
        code, body, _ = self.call("POST", "/v1/requests", b"{" + b" " * store_requests.MAX_BODY + b"}")
        self.assertEqual((code, body["error"]["code"]), (413, "body_too_large"))


class CallerId(Base):
    def test_dry_run_sends_nothing(self):
        env = self.caller()
        self.assertEqual(self.submit(env)[0], 202)
        done = self.wait(env["request_id"])
        self.assertEqual(done["state"], "dry_run_passed")
        ev = done["evidence"]
        self.assertEqual((ev["outbound_sends"], ev["recipients"], ev["packet_bytes"], ev["self_check"]), (0, 1, 83, "ok"))
        self.assertIn("pink-ipad", ev["recipient_scope"])
        self.assertIn("505-555-0100   TEST CURBSIDE", ev["packet_preview"].replace("\x00", ""))
        self.assertIsNone(done["observations"])
        self.assertEqual(drain(self.register), [])

    def test_dry_run_id_never_goes_live(self):
        env = self.caller()
        self.submit(env)
        self.wait(env["request_id"])
        code, body, _ = self.submit(dict(env, dry_run=False))
        self.assertEqual((code, body["error"]["code"]), (409, "request_id_conflict"))
        self.assertEqual(drain(self.register), [])

    def test_live_send_once_then_duplicate(self):
        env = self.caller(dry_run=False)
        self.assertEqual(self.submit(env)[0], 202)
        done = self.wait(env["request_id"])
        self.assertEqual(done["state"], "sent", done)
        self.assertEqual((done["evidence"]["outbound_sends"], done["evidence"]["bytes_sent"]), (1, 83))
        self.assertEqual(done["observations"]["displayed"], "unknown")
        got = drain(self.register)
        self.assertEqual(len(got), 1)
        rec = callerid.parse(got[0])
        self.assertEqual((rec["direction"], rec["event"], rec["number"], rec["name"]), ("I", "S", TEST_NUMBER, "TEST CURBSIDE"))
        code, again, _ = self.submit(env)
        self.assertEqual((code, again["state"], again["attempts"]), (200, "sent", 1))
        self.assertEqual(drain(self.register), [], "the repeat sent nothing")
        self.assertNotIn("payload", self.agent.requests.records[env["request_id"]], "payload dropped once finished")

    def test_test_mode_limits_live_sends(self):
        for env, err in ((self.caller(target="blue-ipad", dry_run=False), "live_not_permitted"),
                         (self.caller(name="JANE DOE", dry_run=False), "live_not_permitted"),
                         (self.caller(number="5055551234", dry_run=False), "live_not_permitted")):
            code, body, _ = self.submit(env)
            self.assertEqual((code, body["error"]["code"]), (422, err))
        self.assertEqual(self.submit(self.caller(target="blue-ipad"))[0], 202, "dry runs are fine anywhere")
        self.assertEqual(drain(self.register), [])

    def test_port_unreachable_is_failed(self):
        self.agent.requests.handlers["revel.caller_id"].port = closed_port()
        env = self.caller(dry_run=False)
        self.submit(env)
        done = self.wait(env["request_id"])
        self.assertEqual((done["state"], done["error"]["code"]), ("failed", "port_unreachable"))

    def test_customer_identity_never_echoed(self):
        self.agent.requests.handlers["revel.caller_id"].mode = "on"
        env = self.caller(target="pink-ipad", name="JANE DOE", number="5055551234", dry_run=True)
        self.submit(env)
        done = self.wait(env["request_id"])
        blob = json.dumps(done)
        self.assertNotIn("JANE", blob)
        self.assertNotIn("1234", blob)
        self.assertIsNone(done["evidence"]["packet_preview"])

    def test_rate_limit(self):
        codes = [self.submit(self.caller())[0] for _ in range(7)]
        self.assertEqual(codes, [202] * 6 + [429])


class ModeOff(Base):
    mode = "off"

    def test_live_refused_dry_run_allowed(self):
        code, body, _ = self.submit(self.caller(dry_run=False))
        self.assertEqual((code, body["error"]["code"]), (422, "live_disabled"))
        self.assertEqual(self.submit(self.caller())[0], 202)


class Durability(Base):
    def test_restart_mid_send_is_uncertain_and_not_replayed(self):
        # Isolated fixture: records written as a crash would leave them.
        self.agent.requests.handlers["revel.caller_id"].execute = lambda r, g: time.sleep(30)
        mid = self.caller(dry_run=False)
        before = self.caller(dry_run=False)
        waiting = self.envelope(dry_run=False)
        e = self.agent.requests
        now = time.time()
        for env, state, marker in ((mid, "running", now), (before, "running", None), (waiting, "accepted", None)):
            e.records[env["request_id"]] = {
                "request_id": env["request_id"], "hash": store_requests.content_hash(e.validate(json.dumps(env).encode())[0]), "type": env["type"],
                "target": env["target"], "dry_run": False, "expires_at": now + 60, "payload": env["payload"],
                "state": state, "accepted_at": now, "started_at": now if state == "running" else None,
                "finished_at": None, "attempts": 1 if state == "running" else 0, "handler_version": "1",
                "evidence": {}, "result": None, "error": None}
            if marker:
                e.records[env["request_id"]]["send_started_at"] = marker
        e.save()
        b = self.make_agent()  # a fresh process reading the same /data
        self.assertEqual(b.requests.records[mid["request_id"]]["state"], "uncertain")
        self.assertEqual(b.requests.records[mid["request_id"]]["error"]["code"], "agent_restarted_mid_send")
        self.assertEqual(b.requests.records[before["request_id"]]["state"], "failed")
        time.sleep(0.5)
        self.assertEqual(b.requests.records[waiting["request_id"]]["state"], "completed", "queued work resumes")
        self.assertEqual(drain(self.register), [], "nothing replayed")
        # Repeating the uncertain request answers from the record.
        self.agent = b
        code, body = b.requests.submit(json.dumps(mid).encode())
        self.assertEqual((code, body["state"], body["duplicate"]), (200, "uncertain", True))

    def test_expiry_checked_again_before_sending(self):
        e = self.agent.requests
        h = e.handlers["revel.caller_id"]
        # A fresh queue the already-running worker does not watch, so this
        # request waits until we start a worker of our own.
        e.queues["revel.caller_id"] = queue.Queue(maxsize=20)
        env = self.caller(dry_run=False, ttl=2)
        self.assertEqual(e.submit(json.dumps(env).encode())[0], 202)
        e.clock = lambda: time.time() + 5  # it waited past its expiry
        ran = []
        h.execute = lambda r, g: ran.append(1)
        threading.Thread(target=e.worker, args=(h,), daemon=True).start()
        time.sleep(0.4)
        self.assertEqual(e.records[env["request_id"]]["state"], "expired")
        self.assertEqual(ran, [], "the handler never ran")
        self.assertEqual(drain(self.register), [])

    def test_gate_refuses_after_expiry(self):
        e = self.agent.requests
        r = {"expires_at": time.time() - 1}
        self.assertFalse(e._send_gate(r)())
        self.assertNotIn("send_started_at", r)

    def test_records_survive_restart_and_prune(self):
        env = self.envelope(dry_run=False)
        self.submit(env)
        self.wait(env["request_id"])
        b = self.make_agent(start=False)
        self.assertEqual(b.requests.get(env["request_id"])[1]["state"], "completed")
        b.requests.records[env["request_id"]]["finished_at"] = time.time() - store_requests.RETENTION_S - 1
        b.requests.prune()
        self.assertEqual(b.requests.records, {})


class Isolation(Base):
    def test_a_stuck_handler_blocks_nothing_else(self):
        release = threading.Event()
        self.agent.requests.handlers["revel.caller_id"].execute = lambda r, g: (release.wait(10), {"state": "failed", "code": "x"})[1]
        self.submit(self.caller(dry_run=False))
        env = self.envelope(dry_run=False)
        self.submit(env)
        self.assertEqual(self.wait(env["request_id"], timeout=2)["state"], "completed")
        self.assertEqual(self.agent.accept(job(2100009002))[0], 202, "printing still accepts")
        release.set()

    def test_queue_full(self):
        e = self.agent.requests
        e.queues["revel.caller_id"] = queue.Queue(maxsize=1)
        e.queues["revel.caller_id"].put("occupied")
        code, body = e.submit(json.dumps(self.caller()).encode())
        self.assertEqual((code, body["error"]["code"]), (503, "queue_full"))


class Views(Base):
    def test_reads_change_nothing(self):
        for path in ("/v1/capabilities", "/v1/targets", "/v1/diagnostics", "/status"):
            self.assertEqual(self.call("GET", path)[0], 200, path)
        self.assertEqual(self.agent.requests.records, {})
        self.assertEqual(drain(self.register), [])

    def test_capabilities_publish_the_rules(self):
        caps = self.call("GET", "/v1/capabilities")[1]
        cid = next(h for h in caps["handlers"] if h["type"] == "revel.caller_id")
        self.assertEqual((cid["mode"], cid["max_ttl_s"], cid["test_targets"]), ("test", 120, ["pink-ipad"]))
        self.assertIn("never truncated", cid["payload"]["name"])
        self.assertEqual(caps["idempotency"]["retention_s"], 7 * 24 * 3600)
        self.assertNotIn(KEY, json.dumps(caps))

    def test_targets_and_diagnostics(self):
        t = self.call("GET", "/v1/targets")[1]
        self.assertEqual([x["id"] for x in t["targets"]], ["agent", "pink-ipad", "blue-ipad"])
        d = self.call("GET", "/v1/diagnostics")[1]
        self.assertFalse(d["network"]["broadcast_reaches_lan"])
        self.assertIn("revel.caller_id", d["queues"])
        self.assertNotIn(KEY, json.dumps(d))

    def test_bad_target_config_is_dropped_and_reported(self):
        o = opts(1)
        o["revel_targets"] = o["revel_targets"] + [{"id": "public", "name": "x", "ip": "8.8.8.8", "test": True},
                                                   {"id": "Bad Id", "name": "x", "ip": "127.0.0.3"}]
        a = agent.Agent(o)
        self.assertEqual(sorted(a.registers.items), ["blue-ipad", "pink-ipad"])
        self.assertEqual(len(a.registers.problems), 2)


class Packet(unittest.TestCase):
    def test_layout(self):
        when = time.struct_time((2026, 10, 12, 16, 5, 0, 0, 285, -1))
        p = callerid.build("TEST CURBSIDE", "5055550100", when)
        self.assertEqual(len(p), 83)
        self.assertEqual(p[:5], b"^^<U>")
        self.assertEqual(p[11:14], b"<S>")
        self.assertEqual(p[20:21], b"$")
        self.assertNotIn(b"$", p[5:20], "no '$' inside the binary header")
        self.assertEqual(p[21:].decode(), "01 I S 0000 G A1 10/12 04:05 PM 505-555-0100   TEST CURBSIDE  ")
        self.assertEqual(p[68:83], b"TEST CURBSIDE  ")
        self.assertEqual(p[53:67], b"505-555-0100  ")
        midnight = callerid.build("TEST", "5055550100", time.struct_time((2026, 1, 2, 0, 7, 0, 0, 2, -1)))
        self.assertIn(b"01/02 12:07 AM", midnight)

    def test_rules(self):
        self.assertIsNone(callerid.check_name("A" * 15))
        self.assertIsNotNone(callerid.check_name("A" * 16))
        self.assertIsNotNone(callerid.check_name(""))
        self.assertIsNotNone(callerid.check_name(" LEADING"))
        self.assertIsNotNone(callerid.check_name("NAME\r\n"))
        self.assertIsNone(callerid.check_number("505-555-0100"))
        self.assertIsNotNone(callerid.check_number("1505555010"))
        self.assertIsNotNone(callerid.check_number("505-155-0100"))
        self.assertIsNotNone(callerid.check_number(5055550100))


if __name__ == "__main__":
    unittest.main(verbosity=2)
