"""Unit tests for the print agent: no CUPS, printer or network needed.

    python3 print_agent/tests/test_agent.py
"""

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
    return a


def job(i, html="<p>x</p>"):
    return {"id": i, "order": f"T-{i}", "kind": "test", "html": html}


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
        self.assertTrue(os.path.exists(agent.spool_path(1, "html")))
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
        self.assertFalse(os.path.exists(agent.spool_path(5, "html")), "files deleted after the outcome")
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
        self.assertFalse(os.path.exists(agent.spool_path(3, "html")))
        self.assertTrue(os.path.exists(agent.spool_path(4, "html")))


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
        answers = [(False, "HTTP 503"), (False, "URLError"), (True, "HTTP 200")]
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
