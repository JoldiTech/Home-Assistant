#!/usr/bin/env python3
"""End-to-end test of the add-on image against a simulated printer.

Runs the real container (CUPS, WeasyPrint, the agent) with fakes on a private
docker network: an IPP printer, an SNMP responder serving its page counter,
and a fake dashboard that verifies the agent's signed result posts. The "printer" is a
second CUPS sharing one queue whose backend bumps a page counter
(fakes/fake_printer.sh); ippeveprinter would be closer, but it needs IPv6 and
DNS-SD, which the test sandbox lacks.

    docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm \
        -t nmt-print-agent:test print_agent/nmt_print_agent
    python3 print_agent/tests/e2e.py
"""

import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "nmt_print_agent", "agent"))
import agent as A  # noqa: E402
sys.path.insert(0, HERE)
from testpdf import make_pdf  # noqa: E402

IMAGE = os.environ.get("E2E_IMAGE", "nmt-print-agent:test")
NET = "nmt-e2e"
PORT = 18080
KEY = secrets.token_hex(32)
SUP_TOKEN = secrets.token_hex(16)
WORK = tempfile.mkdtemp(prefix="nmt-e2e-")
SHARED, DATA = os.path.join(WORK, "shared"), os.path.join(WORK, "data")
TIMINGS = {"NMT_CUPS_WAIT": "30", "NMT_COUNTER_STABLE": "3", "NMT_COUNTER_WAIT": "15",
           "NMT_BLOCKED_GRACE": "25", "NMT_POLL_INTERVAL": "5", "NMT_RETRY_DELAYS": "2,4,6,8"}
PDF = make_pdf()

results = []
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via an HTTP proxy


def sh(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True)


def setshared(name, value):
    with open(os.path.join(SHARED, name), "w") as f:
        f.write(value)


def printed():
    try:
        with open(os.path.join(SHARED, "printed.log")) as f:
            return len(f.read().splitlines())
    except FileNotFoundError:
        return 0


def delivered():
    try:
        with open(os.path.join(SHARED, "results.jsonl")) as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def request(method, path, body=b"", ts=None, key=KEY, sign=True):
    ts = str(int(time.time()) if ts is None else ts)
    headers = {"Content-Type": "application/json"}
    if sign:
        headers.update({"X-NMT-Timestamp": ts, "X-NMT-Signature": A.sign(key, ts, method, path, body)})
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body if method == "POST" else None,
                                 method=method, headers=headers)
    t0 = time.time()
    try:
        with DIRECT.open(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"null"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null"), time.time() - t0
    except (urllib.error.URLError, ConnectionError):
        return 0, {"printer": {}, "queue": None}, time.time() - t0


def job_body(job_id, pdf=PDF, kind="test"):
    return json.dumps({"id": job_id, "order": f"T-{job_id}", "kind": kind,
                       "pdf": base64.b64encode(pdf).decode()}).encode()


def push(job_id, pdf=PDF, kind="test"):
    return request("POST", "/print", job_body(job_id, pdf, kind))


def wait_for(pred, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return pred()


def notifications():
    try:
        with open(os.path.join(SHARED, "notifications.jsonl")) as f:
            return [json.loads(line) for line in f]
    except FileNotFoundError:
        return []


def alert(nid, service="create", timeout=30):
    return wait_for(lambda: next((n for n in notifications()
                                  if n["notification_id"] == nid and n["service"] == service), None), timeout)


def result_for(job_id, timeout=90):
    return wait_for(lambda: next((r for r in delivered() if r["id"] == job_id), None), timeout)


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def start_printer():
    sh("docker", "run", "-d", "--name", "printer", "--network", NET, "-e", "SHARED=/shared",
       "-v", f"{SHARED}:/shared", "-v", f"{HERE}/fakes:/fakes:ro", "--entrypoint", "/fakes/fake_printer.sh", IMAGE)
    wait_for(lambda: "lpadmin" not in sh("docker", "top", "printer", check=False).stdout
             and "tail" in sh("docker", "top", "printer", check=False).stdout, 20)


def start_agent():
    # No S6_KEEP_ENV: the agent must read these from s6's container
    # environment, the same way it reads SUPERVISOR_TOKEN in production.
    env = sum((["-e", f"{k}={v}"] for k, v in {**TIMINGS, "SUPERVISOR_TOKEN": SUP_TOKEN}.items()), [])
    sh("docker", "run", "-d", "--name", "agent", "--network", NET, "-p", f"127.0.0.1:{PORT}:8080",
       "-v", f"{DATA}:/data", *env, IMAGE)
    ok = wait_for(lambda: request("GET", "/status")[0] == 200, 60)
    if not ok:
        print(sh("docker", "logs", "agent", check=False).stdout[-3000:])
        raise SystemExit("agent did not come up")


def cleanup():
    for c in ("agent", "printer", "dashboard"):
        sh("docker", "rm", "-f", c, check=False)
    sh("docker", "network", "rm", NET, check=False)


def main():
    cleanup()
    os.makedirs(SHARED)
    os.makedirs(DATA)
    os.chmod(SHARED, 0o777)
    setshared("count", "7163")
    with open(os.path.join(DATA, "options.json"), "w") as f:
        json.dump({"dashboard_url": "http://dashboard:8000", "signing_key": KEY, "agent_name": "downtown",
                   "printer_uri": "ipp://printer:631/printers/print", "printer_ip": "printer",
                   "snmp_community": "public", "listen_port": 8080}, f)
    sh("docker", "network", "create", NET)
    sh("docker", "run", "-d", "--name", "dashboard", "--network", NET, "-e", "SHARED=/shared",
       "--network-alias", "supervisor", "-e", f"TEST_SUPERVISOR_TOKEN={SUP_TOKEN}",
       "-e", f"TEST_KEY={KEY}", "-v", f"{SHARED}:/shared", "-v", f"{HERE}/fakes:/fakes:ro",
       "--entrypoint", "python3", IMAGE, "/fakes/fake_dashboard.py")
    start_printer()
    start_agent()

    # 1. Normal print
    code, body, secs = push(1)
    check("1 normal: 202 within 5 s", code == 202 and body == {"accepted": True} and secs < 5, f"{code} {secs:.2f}s")
    r = result_for(1)
    check("1 normal: result printed, delta 1",
          r and r["outcome"] == "printed" and r["pages_after"] - r["pages_before"] == 1, r)
    check("1 normal: one sheet", printed() == 1, printed())
    check("1 normal: no alert", not [n for n in notifications() if n["service"] == "create"], notifications())

    # 2. Duplicate
    code, body, _ = push(1)
    time.sleep(5)
    check("2 duplicate: 200 duplicate printed",
          code == 200 and body == {"accepted": True, "duplicate": True, "outcome": "printed"}, body)
    check("2 duplicate: no second sheet", printed() == 1, printed())

    # 3. Refusals
    body = job_body(30)
    c1 = request("POST", "/print", body, key="wrong" * 13)[0]
    c2 = request("POST", "/print", body, ts=int(time.time()) - 301)[0]
    c3 = request("POST", "/print", body, sign=False)[0]
    c4 = request("GET", "/status", sign=False)[0]
    time.sleep(3)
    check("3 refusals: bad sig/old ts/unsigned -> 401", (c1, c2, c3, c4) == (401, 401, 401, 401), (c1, c2, c3, c4))
    check("3 refusals: nothing printed or recorded",
          printed() == 1 and "30" not in json.load(open(os.path.join(DATA, "state.json")))["jobs"])

    # Bad requests: 400 bad-request, nothing printed
    bad = [push(31, pdf=b"<!doctype html><p>not a pdf</p>"),
           request("POST", "/print", json.dumps({"id": 32, "order": "T-32", "kind": "test",
                                                 "html": "<p>old format</p>"}).encode()),
           request("POST", "/print", json.dumps({"id": 33, "order": "T-33", "kind": "test",
                                                 "pdf": "%%% not base64 %%%"}).encode()),
           push(34, pdf=b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024))]
    time.sleep(3)
    check("400: not a PDF / HTML body / bad base64 / over 5 MB",
          [(c, b) for c, b, _ in bad] == [(400, {"accepted": False, "reason": "bad-request"})] * 4,
          [(c, b) for c, b, _ in bad])
    check("400: nothing printed", printed() == 1, printed())

    # Status
    code, st, _ = request("GET", "/status")
    check("status: shape, formats and live counter",
          code == 200 and st["agent"] == "downtown" and st["version"] == A.VERSION and st["queue"] == 0
          and st["formats"] == ["pdf"] and st["printer"]["reachable"] and st["printer"]["page_count"] == 7164, st)

    # Multi-page PDF
    base = printed()
    push(40, pdf=make_pdf(pages=3))
    r = result_for(40)
    check("multi-page PDF: printed once, counter rose", r and r["outcome"] == "printed"
          and r["pages_after"] > r["pages_before"] and printed() == base + 1, r)

    # 6. Printer powered off
    sh("docker", "stop", "-t", "1", "printer")
    ok = wait_for(lambda: request("GET", "/status")[1]["printer"]["reachable"] is False, 20)
    code, body, secs = push(60)
    check("6 power off: /status reachable false", ok)
    check("6 power off: /print 503 unreachable",
          code == 503 and body == {"accepted": False, "reason": "unreachable"} and secs < 5, f"{code} {body} {secs:.1f}s")
    a = alert("nmt_print_agent_printer")
    check("6 power off: HA alert raised", a and "unreachable" in a["message"], a)
    sh("docker", "rm", "-f", "printer")
    start_printer()
    wait_for(lambda: request("GET", "/status")[1]["printer"]["reachable"], 20)
    check("6 power off: HA alert cleared when the printer is back", alert("nmt_print_agent_printer", "dismiss"))

    # 9. Dashboard unreachable
    base = printed()
    setshared("dash_mode", "down")
    push(90)
    time.sleep(15)
    check("9 dashboard down: printed but not yet delivered", printed() == base + 1 and not result_for(90, 0))
    setshared("dash_mode", "")
    r = result_for(90, 60)
    check("9 dashboard down: delivered once access returns", r and r["outcome"] == "printed", r)
    time.sleep(3)
    check("9 dashboard down: delivered exactly once", sum(1 for x in delivered() if x["id"] == 90) == 1)

    # 9b. Dashboard down for longer than the 4 retries: give up and alert
    setshared("dash_mode", "down")
    push(91)
    a = alert("nmt_print_agent_report_91", timeout=60)
    setshared("dash_mode", "")
    time.sleep(12)
    check("9b dashboard down too long: HA alert after 4 retries", a and "after 4 retries" in a["message"], a)
    check("9b dashboard down too long: result dropped, not delivered later", not result_for(91, 0))
    code, body, _ = push(91)
    check("9b dashboard down too long: outcome still answered as duplicate",
          code == 200 and body["outcome"] == "printed", body)

    # 7. Restart mid-job
    base = printed()
    setshared("mode", "hang")
    push(70)
    wait_for(lambda: printed() == base + 1, 30)
    sh("docker", "restart", "-t", "2", "agent")
    r = result_for(70, 90)
    check("7 restart mid-job: uncertain", r and r["outcome"] == "uncertain"
          and r["error"] == "agent restarted during the job", r)
    a = alert("nmt_print_agent_job_70")
    check("7 restart mid-job: HA alert 'may not have printed'", a and "may not have printed" in a["message"], a)
    setshared("mode", "")
    sh("docker", "rm", "-f", "printer")  # the fake printer is still "printing" job 70 forever
    start_printer()
    wait_for(lambda: request("GET", "/status")[1]["printer"]["reachable"], 20)
    time.sleep(8)
    check("7 restart mid-job: never printed again", printed() == base + 1, (base, printed()))
    code, body, _ = push(70)
    check("7 restart mid-job: resend answered duplicate uncertain",
          code == 200 and body["outcome"] == "uncertain", body)

    # 5. Blocked mid-queue: hold, then print once when the printer comes back
    base = printed()
    setshared("mode", "slow")
    push(51)
    push(52)
    wait_for(lambda: printed() == base + 1, 30)
    sh("docker", "pause", "printer")       # unreachable while 51 is printing and 52 waits
    r51 = result_for(51, 120)
    held = wait_for(lambda: request("GET", "/status")[1]["queue"] == 1, 10)
    time.sleep(8)
    sh("docker", "unpause", "printer")
    setshared("mode", "")
    r52 = result_for(52, 120)
    check("5 blocked: in-flight job timed out -> uncertain (never failed)",
          r51 and r51["outcome"] == "uncertain" and r51["error"].startswith("timeout"), r51)
    check("5 blocked: next job held, then printed once", held and r52 and r52["outcome"] == "printed", r52)

    # 5b. Blocked past the grace period -> failed, never printed
    base = printed()
    setshared("mode", "slow")
    push(53)
    push(54)
    wait_for(lambda: printed() == base + 1, 30)
    n = printed()
    sh("docker", "pause", "printer")
    r54 = result_for(54, 180)
    sh("docker", "unpause", "printer")
    setshared("mode", "")
    time.sleep(10)
    check("5b blocked past grace: held job failed unreachable",
          r54 and r54["outcome"] == "failed" and r54["error"] == "unreachable", r54)
    check("5b blocked past grace: held job never printed", printed() == n, (n, printed()))
    a = alert("nmt_print_agent_job_54")
    check("5b blocked past grace: HA alert 'didn't print'", a and "unreachable" in a["message"], a)
    alerts_text = json.dumps(notifications())
    check("alerts: no job content", "TEST PAGE" not in alerts_text and "PDF-" not in alerts_text)

    # Logs never carry job content or header values
    logs = sh("docker", "logs", "agent", check=False)
    text = logs.stdout + logs.stderr
    check("logs: no job content, key or signature", "TEST PAGE" not in text and "PDF-" not in text
          and base64.b64encode(PDF).decode()[:40] not in text and KEY not in text and "X-NMT" not in text, "")
    check("results: signed to printAgent.php with User-Agent",
          {(r["path"], r["ua"]) for r in delivered()} == {("/tools/webhook/printAgent.php",
                                                         f"nmt-print-agent/{A.VERSION}")}, "")
    left = os.listdir(os.path.join(DATA, "spool"))
    check("spool: no PDFs left", left == [], left)
    agent_lines = [ln for ln in text.splitlines() if ln[:2] == "20"]
    print("\n--- agent log ---\n" + "\n".join(agent_lines[-80:]))

    failed = [n for n, ok in results if not ok]
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        if not os.environ.get("E2E_KEEP"):
            cleanup()
            shutil.rmtree(WORK, ignore_errors=True)
    sys.exit(rc)
