#!/usr/bin/env python3
"""Send one signed request to the live agent from inside the HA box.

The agent has no host port; it is reachable at http://local-nmt-print-agent:8080
from the add-on network, which the `ssh homeassistant` shell is on. The request
is signed here with $PRINT_AGENT_SIGNING_KEY (never sent anywhere) and the
body goes over ssh stdin, so neither appears on a command line.

    remote_request.py status
    remote_request.py print 2100000010            # a test page PDF, kind=test
    remote_request.py print 2100000010 --pages 3  # a 3-page test PDF
    remote_request.py print 2100000010 --not-pdf  # expect 400 bad-request
    remote_request.py print 2100000010 --bad-sig  # or --old-ts / --unsigned
"""

import argparse
import base64
import json
import os
import shlex
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "nmt_print_agent", "agent"))
from agent import sign  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from testpdf import make_pdf  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["status", "print"])
    p.add_argument("job", nargs="?", type=int)
    p.add_argument("--bad-sig", action="store_true")
    p.add_argument("--old-ts", action="store_true")
    p.add_argument("--unsigned", action="store_true")
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--not-pdf", action="store_true")
    a = p.parse_args()
    key = os.environ["PRINT_AGENT_SIGNING_KEY"]

    if a.what == "status":
        method, path, body = "GET", "/status", b""
    else:
        method, path = "POST", "/print"
        pdf = b"<!doctype html><p>not a pdf</p>" if a.not_pdf else make_pdf(pages=a.pages)
        body = json.dumps({"id": a.job, "order": f"TEST-{a.job}", "kind": "test",
                           "pdf": base64.b64encode(pdf).decode()}).encode()
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
