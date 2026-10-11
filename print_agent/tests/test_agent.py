"""Unit tests for the print agent: no CUPS, printer or network needed.

    python3 print_agent/tests/test_agent.py
"""

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest

TMP = tempfile.mkdtemp()
os.environ["NMT_DATA"] = TMP
os.environ["NMT_BLOCKED_GRACE"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nmt_print_agent", "agent"))

import agent  # noqa: E402
import ipp  # noqa: E402
sys.path.insert(0, os.path.dirname(__file__))
from testpdf import make_pdf  # noqa: E402

PDF = make_pdf()

KEY = "k" * 64
OPTS = {"signing_key": KEY, "agent_name": "downtown", "dashboard_url": "https://dash.example",
        "printer_uri": "ipp://192.0.2.1/ipp/print", "printer_ip": "192.0.2.1"}


def snap(blocking=(), reachable=True):
    return {"reachable": reachable, "state": "idle", "reasons": list(blocking),
            "blocking": list(blocking), "page_count": 100, "at": time.time()}


def fresh_agent():
    for name in os.listdir(TMP):
        p = os.path.join(TMP, name)
        if os.path.isfile(p):
            os.remove(p)
    os.makedirs(agent.SPOOL, exist_ok=True)
    for name in os.listdir(agent.SPOOL):
        os.remove(os.path.join(agent.SPOOL, name))
    a = agent.Agent(OPTS)
    a.printer.snap = snap()
    a.ha = FakeHA()
    return a


class FakeHA:
    """Records notifications in .sent and status-entity updates in .states."""

    def __init__(self):
        self.sent = []
        self.states = []

    def set_state(self, entity_id, state, attributes):
        self.states.append((entity_id, state, attributes))

    def notify(self, nid, title, message):
        self.sent.append(("create", nid, message))

    def dismiss(self, nid):
        self.sent.append(("dismiss", nid, None))


def job(i):
    """An accepted job as parse_job() hands it to Agent.accept()."""
    return {"id": i, "order": f"T-{i}", "kind": "test", "pdf_bytes": PDF}


def body(i=1, pdf=PDF, **extra):
    d = {"id": i, "order": f"T-{i}", "kind": "test", "pdf": base64.b64encode(pdf).decode()}
    d.update(extra)
    return json.dumps(d).encode()


class Signing(unittest.TestCase):
    def headers(self, ts, body=b"", method="POST", path="/print", key=KEY):
        return {"X-NMT-Timestamp": str(ts), "X-NMT-Signature": agent.sign(key, str(ts), method, path, body)}

    def test_valid(self):
        now = int(time.time())
        self.assertTrue(agent.verify(KEY, self.headers(now, b"{}"), "POST", "/print", b"{}"))

    def test_matches_reference_construction(self):
        import hashlib
        import hmac
        expected = hmac.new(KEY.encode(), b"1700000000\nGET\n/status\n", hashlib.sha256).hexdigest()
        self.assertEqual(agent.sign(KEY, "1700000000", "GET", "/status", b""), expected)

    def test_rejects(self):
        now = int(time.time())
        h = self.headers(now, b"{}")
        self.assertFalse(agent.verify(KEY, h, "POST", "/print", b"{ }"), "body changed")
        self.assertFalse(agent.verify(KEY, h, "GET", "/print", b"{}"), "method changed")
        self.assertFalse(agent.verify(KEY, h, "POST", "/status", b"{}"), "path changed")
        self.assertFalse(agent.verify("x" * 64, h, "POST", "/print", b"{}"), "other key")
        self.assertFalse(agent.verify("", self.headers(now, key=""), "POST", "/print", b""), "empty key")
        self.assertFalse(agent.verify(KEY, self.headers(now - 301, b"{}"), "POST", "/print", b"{}"), "old")
        self.assertFalse(agent.verify(KEY, self.headers(now + 301, b"{}"), "POST", "/print", b"{}"), "future")
        self.assertTrue(agent.verify(KEY, self.headers(now - 299, b"{}"), "POST", "/print", b"{}"))
        self.assertFalse(agent.verify(KEY, {}, "POST", "/print", b"{}"), "no headers")
        bad = dict(h, **{"X-NMT-Signature": "zz" * 32})
        self.assertFalse(agent.verify(KEY, bad, "POST", "/print", b"{}"), "non-hex")
        upper = dict(h, **{"X-NMT-Signature": h["X-NMT-Signature"].upper()})
        self.assertTrue(agent.verify(KEY, upper, "POST", "/print", b"{}"), "hex case-insensitive")


class Reasons(unittest.TestCase):
    def test_normalize(self):
        names, blocking = agent.normalize_reasons(["toner-low-warning"])
        self.assertEqual((names, blocking), (["toner-low"], []))
        names, blocking = agent.normalize_reasons(["media-empty-error", "toner-low-warning"])
        self.assertEqual(blocking, ["media-empty"])
        self.assertEqual(agent.normalize_reasons(["media-needed"])[1], ["media-empty"])
        self.assertEqual(agent.normalize_reasons(["cover-open-report"])[1], [])
        self.assertEqual(agent.normalize_reasons(["cover-open"])[1], ["door-open"])
        self.assertEqual(agent.normalize_reasons(["media-jam"])[1], ["media-jam"])
        self.assertEqual(agent.normalize_reasons(["none"]), ([], []))
        self.assertEqual(agent.normalize_reasons(["other-error"])[1], ["other"])


class Decide(unittest.TestCase):
    def test_rules(self):
        D = agent.decide
        self.assertEqual(D(ipp.JOB_COMPLETED, False, 10, 11, None), ("printed", None))
        self.assertEqual(D(ipp.JOB_COMPLETED, False, 10, 12, None), ("printed", None))
        self.assertEqual(D(ipp.JOB_COMPLETED, False, 10, 10, None)[0], "uncertain")
        self.assertEqual(D(ipp.JOB_COMPLETED, False, None, 11, None)[0], "uncertain")
        self.assertEqual(D(ipp.JOB_ABORTED, False, 10, 10, "media-jam"), ("failed", "media-jam"))
        self.assertEqual(D(ipp.JOB_ABORTED, False, 10, 10, None), ("failed", "job-aborted"))
        self.assertEqual(D(ipp.JOB_ABORTED, False, 10, 11, None)[0], "uncertain")
        self.assertEqual(D(ipp.JOB_ABORTED, False, 10, None, None)[0], "uncertain")
        # A timed-out job may still be in the printer: never "failed".
        self.assertEqual(D(ipp.JOB_CANCELED, True, 10, 10, "door-open"), ("uncertain", "timeout: door-open"))
        self.assertEqual(D(None, True, 10, 10, None)[0], "uncertain")


class Accept(unittest.TestCase):
    def test_accept_then_duplicate(self):
        a = fresh_agent()
        self.assertEqual(a.accept(job(1)), (202, {"accepted": True}))
        with open(agent.spool_path(1, "pdf"), "rb") as f:
            self.assertEqual(f.read(), PDF, "the PDF is spooled unchanged")
        self.assertEqual(os.stat(agent.spool_path(1, "pdf")).st_mode & 0o777, 0o600)
        self.assertEqual(a.accept(job(1)), (200, {"accepted": True, "duplicate": True, "outcome": "pending"}))
        self.assertEqual(a.store.queue, [1])
        # Survives a restart.
        b = agent.Agent(OPTS)
        b.printer.snap = snap(["media-empty"])
        self.assertEqual(b.accept(job(1))[0], 200, "duplicate answered even while printer is blocked")

    def test_duplicate_reports_outcome(self):
        a = fresh_agent()
        a.accept(job(5))
        a.store.queue.remove(5)
        a.finish(5, "printed", 100, 101, None)
        self.assertEqual(a.accept(job(5))[1]["outcome"], "printed")
        self.assertFalse(os.path.exists(agent.spool_path(5, "pdf")), "files deleted after the outcome")
        self.assertEqual(a.store.outbox[0]["payload"],
                         {"action": "result", "agent": "downtown", "id": 5, "outcome": "printed",
                          "pages_before": 100, "pages_after": 101, "error": None})

    def test_blocked_printer_503_queues_nothing(self):
        a = fresh_agent()
        a.printer.snap = snap(["media-empty"])
        self.assertEqual(a.accept(job(2)), (503, {"accepted": False, "reason": "media-empty"}))
        a.printer.snap = snap(["unreachable"], reachable=False)
        self.assertEqual(a.accept(job(2))[1]["reason"], "unreachable")
        self.assertEqual((a.store.queue, a.store.jobs), ([], {}))

    def test_concurrent_same_id_accepted_once(self):
        a = fresh_agent()
        results = []
        threads = [threading.Thread(target=lambda: results.append(a.accept(job(9))[0])) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200] * 7 + [202])
        self.assertEqual(a.store.queue, [9])

    def test_prune_keeps_queued(self):
        a = fresh_agent()
        a.accept(job(1))
        for i in range(2, agent.KEEP_JOBS + 10):
            a.store.jobs[str(i)] = {"id": i, "order": "o", "kind": "test", "status": "done", "outcome": "printed"}
        a.store.prune()
        self.assertIn("1", a.store.jobs)
        self.assertEqual(len(a.store.jobs), agent.KEEP_JOBS)


class Recover(unittest.TestCase):
    def test_in_flight_job_reported_uncertain_not_reprinted(self):
        a = fresh_agent()
        a.accept(job(3))
        a.accept(job(4))
        a.store.jobs["3"].update(status="printing", pages_before=50)
        a.store.queue.remove(3)
        a.store.save()
        b = agent.Agent(OPTS)
        b.printer.page_count = lambda: 51
        b.recover()
        self.assertEqual(b.store.jobs["3"]["outcome"], "uncertain")
        self.assertEqual(b.store.outbox[0]["payload"]["error"], "agent restarted during the job")
        self.assertEqual(b.store.outbox[0]["payload"]["pages_after"], 51)
        self.assertEqual(b.store.queue, [4], "the rest of the queue continues")
        self.assertFalse(os.path.exists(agent.spool_path(3, "pdf")))
        self.assertTrue(os.path.exists(agent.spool_path(4, "pdf")))


class HeldQueue(unittest.TestCase):
    def test_blocked_jobs_fail_after_grace_and_never_reach_cups(self):
        a = fresh_agent()
        a.accept(job(7))
        a.accept(job(8))
        a.printer.poll = lambda counter=True, timeout=4.0: snap(["door-open"])
        a.cups.ensure_queue = lambda: True
        a.run_job = lambda jid: self.fail("submitted while blocked")
        threading.Thread(target=a.worker, daemon=True).start()
        deadline = time.time() + 15
        while time.time() < deadline and len(a.store.outbox) < 2:
            time.sleep(0.2)
        got = {e["payload"]["id"]: (e["payload"]["outcome"], e["payload"]["error"]) for e in a.store.outbox}
        self.assertEqual(got, {7: ("failed", "door-open"), 8: ("failed", "door-open")})


class Reporter(unittest.TestCase):
    def test_retry_schedule_until_accepted(self):
        a = fresh_agent()
        a.accept(job(11))
        a.store.queue.remove(11)
        answers = [("retry", "HTTP 503"), ("retry", "URLError"), ("ok", "HTTP 200")]
        calls = []

        def post(payload):
            calls.append(time.time())
            return answers.pop(0)

        a.post_result = post
        agent.RETRY_DELAYS[:] = [0.2, 0.2, 0.2]
        a.finish(11, "printed", 1, 2, None)
        threading.Thread(target=a.reporter, daemon=True).start()
        deadline = time.time() + 10
        while time.time() < deadline and a.store.outbox:
            time.sleep(0.1)
        self.assertEqual(len(calls), 3)
        self.assertEqual(a.store.outbox, [])
        with a.store.lock, open(agent.STATE_FILE) as f:  # removal and save happen under the lock
            self.assertEqual(json.load(f)["outbox"], [], "delivery persisted")
        self.assertEqual(a.ha.sent, [], "a delivered result raises no alert")

    def test_gives_up_after_four_retries_and_alerts(self):
        a = fresh_agent()
        a.accept(job(12))
        a.store.queue.remove(12)
        calls = []
        a.post_result = lambda payload: (calls.append(1), ("retry", "HTTP 502"))[1]
        agent.RETRY_DELAYS[:] = [0.1, 0.1, 0.1, 0.1]
        a.finish(12, "printed", 1, 2, None)
        threading.Thread(target=a.reporter, daemon=True).start()
        deadline = time.time() + 10
        while time.time() < deadline and not a.ha.sent:
            time.sleep(0.1)
        time.sleep(0.5)
        self.assertEqual(len(calls), 5, "first attempt + 4 retries, then stop")
        self.assertEqual(a.store.outbox, [])
        (kind, nid, message), = a.ha.sent
        self.assertEqual((kind, nid), ("create", "nmt_print_agent_report_12"))
        self.assertIn("after 4 retries", message)
        self.assertEqual(a.accept(job(12))[1]["outcome"], "printed", "outcome still answerable as a duplicate")


    def run_reporter_once(self, verdict):
        a = fresh_agent()
        a.accept(job(13))
        a.store.queue.remove(13)
        calls = []
        a.post_result = lambda payload: (calls.append(1), verdict)[1]
        agent.RETRY_DELAYS[:] = [0.1, 0.1, 0.1, 0.1]
        a.finish(13, "printed", 1, 2, None)
        threading.Thread(target=a.reporter, daemon=True).start()
        time.sleep(1.5)
        return a, calls

    def test_unknown_id_stops_quietly(self):
        a, calls = self.run_reporter_once(("unknown", "HTTP 200"))
        self.assertEqual((len(calls), a.store.outbox, a.ha.sent), (1, [], []))

    def test_4xx_stops_at_once_and_alerts(self):
        a, calls = self.run_reporter_once(("reject", "HTTP 401"))
        self.assertEqual((len(calls), a.store.outbox), (1, []))
        (kind, nid, message), = a.ha.sent
        self.assertIn("refused it (HTTP 401)", message)


class PostResult(unittest.TestCase):
    """post_result against a local stand-in for printAgent.php."""

    def serve(self, code, reply):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        seen = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                seen.update(path=self.path, ua=self.headers.get("User-Agent"),
                            ok=agent.verify(KEY, self.headers, "POST", self.path, raw), body=json.loads(raw))
                data = json.dumps(reply).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        a = fresh_agent()
        a.result_url = f"http://127.0.0.1:{srv.server_port}/tools/webhook/printAgent.php"
        return a, seen

    def test_signed_to_new_path_with_user_agent(self):
        self.assertTrue(fresh_agent().result_url.endswith("/tools/webhook/printAgent.php"))
        a, seen = self.serve(200, {"ok": True})
        os.environ.pop("http_proxy", None), os.environ.pop("HTTP_PROXY", None)
        self.assertEqual(a.post_result({"id": 1})[0], "ok")
        self.assertEqual((seen["path"], seen["ua"], seen["ok"]),
                         ("/tools/webhook/printAgent.php", f"nmt-print-agent/{agent.VERSION}", True))

    def test_verdicts(self):
        os.environ.pop("http_proxy", None), os.environ.pop("HTTP_PROXY", None)
        for code, reply, verdict in ((200, {"ok": True, "unknown": True}, "unknown"), (401, {}, "reject"),
                                     (400, {}, "reject"), (503, {}, "retry"), (200, {"ok": False}, "retry")):
            a, _ = self.serve(code, reply)
            self.assertEqual(a.post_result({"id": 1})[0], verdict, (code, reply))


class ParseJob(unittest.TestCase):
    def test_valid_pdf(self):
        j = agent.parse_job(body(7))
        self.assertEqual((j["id"], j["pdf_bytes"]), (7, PDF))
        self.assertNotIn("pdf", j)

    def test_bad_requests(self):
        big = b"%PDF-1.4\n" + b"0" * (agent.MAX_PDF + 1)
        for b in (body(pdf=b"<html>not a pdf</html>"), body(pdf=big), b"not json", b"[]",
                  json.dumps({"id": 1, "order": "T-1", "kind": "test", "html": "<p>x</p>"}).encode(),
                  json.dumps({"id": 1, "order": "T-1", "kind": "test", "pdf": "%%%not base64%%%"}).encode(),
                  body(kind="other"), body(i=0), body(order=5)):
            self.assertIsNone(agent.parse_job(b), b[:60])
        self.assertIsNotNone(agent.parse_job(body(pdf=b"%PDF-" + b"0" * (agent.MAX_PDF - 5))), "exactly 5 MB is fine")

    def test_status_advertises_pdf(self):
        self.assertEqual(fresh_agent().status()["formats"], ["pdf"])


class StatusEntity(unittest.TestCase):
    def test_last_three_jobs_newest_first_no_content(self):
        a = fresh_agent()
        for i in (61, 62, 63, 64):
            a.accept(job(i))
        for i, outcome in ((61, "printed"), (62, "failed"), (63, "printed")):
            a.store.queue.remove(i)
            a.finish(i, outcome, 100, 101 if outcome == "printed" else 100, None if outcome == "printed" else "media-jam")
        entity, state, attrs = a.ha.states[-1]
        self.assertEqual(entity, "sensor.instore_order_printing")
        self.assertEqual(state, "printing", "job 64 is still queued")
        self.assertEqual([(j["id"], j["status"], j["pages"]) for j in attrs["last_jobs"]],
                         [(64, "queued", None), (63, "printed", 1), (62, "failed", 0)])
        self.assertEqual(attrs["last_jobs"][2]["error"], "media-jam")
        self.assertTrue(attrs["last_jobs"][1]["time"].endswith("+00:00"))
        self.assertNotIn("PDF", json.dumps(attrs))
        self.assertNotIn("pdf_bytes", json.dumps(attrs))

    def test_states(self):
        a = fresh_agent()
        a.publish()
        self.assertEqual(a.ha.states[-1][1], "ready")
        a.printer.snap = snap(["media-empty"])
        a.publish()
        self.assertEqual((a.ha.states[-1][1], a.ha.states[-1][2]["problem"]), ("problem", "media-empty"))
        a.printer.snap = snap(["unreachable"], reachable=False)
        a.publish()
        self.assertEqual(a.ha.states[-1][1], "unreachable")


class AlertsOnOutcome(unittest.TestCase):
    def test_failed_and_uncertain_alert_printed_does_not(self):
        a = fresh_agent()
        for i, outcome in ((21, "printed"), (22, "failed"), (23, "uncertain")):
            a.accept(job(i))
            a.store.queue.remove(i)
            a.finish(i, outcome, 1, 1 if outcome != "printed" else 2, None if outcome == "printed" else "media-jam")
        self.assertEqual([(k, n) for k, n, _ in a.ha.sent],
                         [("create", "nmt_print_agent_job_22"), ("create", "nmt_print_agent_job_23")])
        self.assertNotIn("%PDF", " ".join(m for _, _, m in a.ha.sent), "no job content in alerts")

    def test_printer_alert_once_per_incident_then_cleared(self):
        a = fresh_agent()
        a.printer.snap = snap(["media-empty"])
        a.accept(job(31))
        a.accept(job(32))
        self.assertEqual([(k, n) for k, n, _ in a.ha.sent], [("create", agent.PRINTER_ALERT)])
        self.assertIn("media-empty", a.ha.sent[0][2])
        a.printer_ok()
        a.printer_ok()
        self.assertEqual([k for k, _, _ in a.ha.sent], ["create", "dismiss"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
