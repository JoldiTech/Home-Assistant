"""Fake dashboard for the e2e test: verifies the agent's signed result posts
and appends them to $SHARED/results.jsonl. `$SHARED/dash_mode` = "down"
answers 503, so the agent must retry.

Also plays the Supervisor's Core API proxy on port 80 (network alias
`supervisor`): with the right bearer token, persistent_notification
create/dismiss calls are appended to $SHARED/notifications.jsonl and state
updates to $SHARED/states.jsonl."""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/opt/nmt-print-agent")
import agent  # noqa: E402

SHARED = os.environ.get("SHARED", "/shared")
KEY = os.environ["TEST_KEY"]
SUP_TOKEN = os.environ["TEST_SUPERVISOR_TOKEN"]


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
        elif self.path != "/tools/webhook/printAgent.php":
            code, out = 404, {"ok": False}
        elif not agent.verify(KEY, self.headers, "POST", self.path, body):
            code, out = 401, {"ok": False}
        else:
            with open(os.path.join(SHARED, "results.jsonl"), "a") as f:
                f.write(json.dumps({"t": time.time(), "path": self.path,
                                    "ua": self.headers.get("User-Agent"), **json.loads(body)}) + "\n")
            code, out = 200, {"ok": True}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Supervisor(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        prefix = "/core/api/services/persistent_notification/"
        if self.headers.get("Authorization") != f"Bearer {SUP_TOKEN}":
            code = 401
        elif self.path.startswith("/core/api/states/"):
            with open(os.path.join(SHARED, "states.jsonl"), "a") as f:
                f.write(json.dumps({"t": time.time(), "entity_id": self.path[len("/core/api/states/"):],
                                    **json.loads(body)}) + "\n")
            code = 200
        elif not self.path.startswith(prefix):
            code = 404
        else:
            with open(os.path.join(SHARED, "notifications.jsonl"), "a") as f:
                f.write(json.dumps({"t": time.time(), "service": self.path[len(prefix):], **json.loads(body)}) + "\n")
            code = 200
        self.send_response(code)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"[]")


threading.Thread(target=HTTPServer(("0.0.0.0", 80), Supervisor).serve_forever, daemon=True).start()
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
