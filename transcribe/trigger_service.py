#!/usr/bin/env python3
"""Tiny LAN-only trigger endpoint on the AI box. Home Assistant POSTs to it to
kick off the Captain's Log pipeline; it returns immediately (202) and runs the
~20-minute job in the background, so HA's rest_command doesn't block.

HA and the AI box are on the same LAN, so this binds to the LAN interface and is
gated by a shared secret - no Cloudflare in the path. Nothing here is exposed
through the public tunnel (that's only aibox.nmteaco.com -> the imagegen app).

Secret from /etc/nmteaco/captains.env (mode 600):
  TRIGGER_TOKEN   shared secret; HA sends it as the X-Trigger-Token header
Bind host/port default to 0.0.0.0:8190 (override with TRIGGER_HOST/TRIGGER_PORT).
"""
import hmac
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "captains_pipeline.py"
TZ = ZoneInfo("America/Denver")


def _load_token():
    p = Path("/etc/nmteaco/captains.env")
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line.startswith("TRIGGER_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("TRIGGER_TOKEN", "")


TRIGGER_TOKEN = _load_token()
_run_lock = threading.Lock()  # never run two pipelines at once
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _run_pipeline(date_str: str):
    if not _run_lock.acquire(blocking=False):
        print("[trigger] pipeline already running; ignoring", file=sys.stderr, flush=True)
        return
    try:
        subprocess.run([sys.executable, str(PIPELINE), date_str])
    finally:
        _run_lock.release()


@app.post("/run")
async def run(request: Request, x_trigger_token: str = Header(default="")):
    if not TRIGGER_TOKEN or not hmac.compare_digest(x_trigger_token, TRIGGER_TOKEN):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    # HA may pass an explicit date; default to today (America/Denver).
    date_str = (body.get("date") or "").strip() or datetime.now(TZ).strftime("%Y-%m-%d")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return JSONResponse({"error": "bad date, want YYYY-MM-DD"}, status_code=400)
    threading.Thread(target=_run_pipeline, args=(date_str,), daemon=True).start()
    return JSONResponse({"status": "started", "date": date_str}, status_code=202)


@app.get("/health")
async def health():
    return {"ok": True, "running": _run_lock.locked()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("TRIGGER_HOST", "0.0.0.0"),
        port=int(os.environ.get("TRIGGER_PORT", "8190")),
        log_level="info",
    )
