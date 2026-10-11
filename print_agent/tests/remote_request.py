#!/usr/bin/env python3
"""Send one signed request to the live agent from inside the HA box.

The agent has no host port; it is reachable at http://local-nmt-print-agent:8080
from the add-on network, which the `ssh homeassistant` shell is on. The request
is signed here with $PRINT_AGENT_SIGNING_KEY (never sent anywhere) and the
body goes over ssh stdin, so neither appears on a command line.

    remote_request.py status
    remote_request.py print 2100000001            # a test page, kind=test
    remote_request.py print 2100000001 --bad-sig  # or --old-ts / --unsigned
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nmt_print_agent", "agent"))
from agent import sign  # noqa: E402

TEST_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
body{{font-family:sans-serif;margin:1in}} h1{{font-size:28pt}} td{{padding:4pt 12pt 4pt 0}}
</style></head><body>
<h1>NMT print agent — TEST PAGE</h1>
<p>This is a test of the downtown print agent. It is not an order.</p>
<table><tr><td>Job</td><td>{job}</td></tr><tr><td>Sent</td><td>{sent}</td></tr></table>
</body></html>"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["status", "print"])
    p.add_argument("job", nargs="?", type=int)
    p.add_argument("--bad-sig", action="store_true")
    p.add_argument("--old-ts", action="store_true")
    p.add_argument("--unsigned", action="store_true")
    a = p.parse_args()
    key = os.environ["PRINT_AGENT_SIGNING_KEY"]

    if a.what == "status":
        method, path, body = "GET", "/status", b""
    else:
        method, path = "POST", "/print"
        html = TEST_PAGE.format(job=a.job, sent=time.strftime("%Y-%m-%d %H:%M:%S %Z"))
        body = json.dumps({"id": a.job, "order": f"TEST-{a.job}", "kind": "test", "html": html}).encode()
    ts = str(int(time.time()) - (400 if a.old_ts else 0))
    sig = sign("wrong-key-" * 4 if a.bad_sig else key, ts, method, path, body)

    curl = ["curl", "-sS", "-m", "10", "-X", method, "-w", r"\nHTTP %{http_code} %{time_total}s\n"]
    if not a.unsigned:
        curl += ["-H", f"X-NMT-Timestamp: {ts}", "-H", f"X-NMT-Signature: {sig}"]
    if body:
        curl += ["-H", "Content-Type: application/json", "--data-binary", "@-"]
    curl.append(f"http://local-nmt-print-agent:8080{path}")
    r = subprocess.run(["ssh", "homeassistant", shlex.join(curl)], input=body, capture_output=True)
    sys.stdout.write(r.stdout.decode())
    sys.stderr.write(r.stderr.decode())
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
