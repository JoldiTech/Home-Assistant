"""Fake dashboard for the e2e test: verifies the agent's signed result posts
and appends them to $SHARED/results.jsonl. `$SHARED/dash_mode` = "down"
answers 503, so the agent must retry."""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/opt/nmt-print-agent")
import agent  # noqa: E402

SHARED = os.environ.get("SHARED", "/shared")
KEY = os.environ["TEST_KEY"]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            mode = open(os.path.join(SHARED, "dash_mode")).read().strip()
        except FileNotFoundError:
            mode = ""
        if mode == "down":
            code, out = 503, {"ok": False}
        elif self.path != "/tools/orders/curbsideAgent.php":
            code, out = 404, {"ok": False}
        elif not agent.verify(KEY, self.headers, "POST", self.path, body):
            code, out = 401, {"ok": False}
        else:
            with open(os.path.join(SHARED, "results.jsonl"), "a") as f:
                f.write(json.dumps({"t": time.time(), **json.loads(body)}) + "\n")
            code, out = 200, {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


HTTPServer(("0.0.0.0", 8000), H).serve_forever()
