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
import glob
import hmac
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# The CUDA-enabled llama-cpp-python needs the CUDA runtime libs (cublas, etc.)
# that ship with torch's nvidia-* packages in the venv. Put them on
# LD_LIBRARY_PATH for the pipeline subprocess so the summarizer loads on GPU.
_NVIDIA_LIBS = ":".join(
    glob.glob(os.path.expanduser("~/transcribe-env/lib/python*/site-packages/nvidia/*/lib"))
)

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
PIPELINE = HERE / "captains_pipeline.py"
TRANSCRIBE_SCRIPT = HERE / "transcribe_day.py"
TRANSCRIPT_DIR = Path.home() / "captains_transcripts"
TZ = ZoneInfo("America/Denver")

# What the single worker is doing right now, surfaced by /status so the HA panel
# can show "transcribing 2026-07-21…" instead of a bare "running".
_current: dict = {"job": None, "date": None}  # job in {None,"log","transcribe"}


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


def _worker_env() -> dict:
    env = {**os.environ}
    if _NVIDIA_LIBS:
        env["LD_LIBRARY_PATH"] = _NVIDIA_LIBS + ":" + env.get("LD_LIBRARY_PATH", "")
    return env


def _run_job(job: str, script: Path, date_str: str):
    """One worker at a time (the single 6GB GPU can't run two). job is 'log'
    (full pipeline) or 'transcribe' (audio only, leaves the transcript on disk
    for a later log build)."""
    if not _run_lock.acquire(blocking=False):
        print(f"[trigger] busy; ignoring {job} {date_str}", file=sys.stderr, flush=True)
        return
    _current.update(job=job, date=date_str)
    try:
        subprocess.run([sys.executable, str(script), date_str], env=_worker_env())
    finally:
        _current.update(job=None, date=None)
        _run_lock.release()


def _transcript_dates() -> list[str]:
    """Dates that already have a non-empty transcript on disk, newest first."""
    out = []
    try:
        for p in TRANSCRIPT_DIR.glob("tea_one_*.log"):
            try:
                if p.stat().st_size > 0:
                    out.append(p.stem[len("tea_one_"):])
            except OSError:
                pass
    except OSError:
        pass
    return sorted(out, reverse=True)


def _authed(tok: str) -> bool:
    return bool(TRIGGER_TOKEN) and hmac.compare_digest(tok, TRIGGER_TOKEN)


async def _date_from(request: Request) -> str:
    try:
        body = await request.json()
    except Exception:
        body = {}
    # HA may pass an explicit date; default to today (America/Denver).
    date_str = (body.get("date") or "").strip() or datetime.now(TZ).strftime("%Y-%m-%d")
    datetime.strptime(date_str, "%Y-%m-%d")  # raises ValueError on bad input
    return date_str


@app.post("/run")
async def run(request: Request, x_trigger_token: str = Header(default="")):
    """Build the full Captain's Log for a day (transcribe if needed, summarize,
    push). Reuses an existing transcript, so re-running a day is cheap."""
    if not _authed(x_trigger_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        date_str = await _date_from(request)
    except ValueError:
        return JSONResponse({"error": "bad date, want YYYY-MM-DD"}, status_code=400)
    if _run_lock.locked():
        return JSONResponse({"error": "busy", **_current}, status_code=409)
    threading.Thread(target=_run_job, args=("log", PIPELINE, date_str), daemon=True).start()
    return JSONResponse({"status": "started", "job": "log", "date": date_str}, status_code=202)


@app.post("/transcribe")
async def transcribe(request: Request, x_trigger_token: str = Header(default="")):
    """Transcribe a day's audio only and leave the transcript on disk. Lets you
    stage the slow (~30 min) audio step ahead of time, then build the log later
    (which reuses the transcript) in seconds."""
    if not _authed(x_trigger_token):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    try:
        date_str = await _date_from(request)
    except ValueError:
        return JSONResponse({"error": "bad date, want YYYY-MM-DD"}, status_code=400)
    if _run_lock.locked():
        return JSONResponse({"error": "busy", **_current}, status_code=409)
    threading.Thread(target=_run_job, args=("transcribe", TRANSCRIBE_SCRIPT, date_str), daemon=True).start()
    return JSONResponse({"status": "started", "job": "transcribe", "date": date_str}, status_code=202)


@app.get("/status")
async def status():
    """LAN-only, read-only: what has a transcript on disk and what the worker is
    doing. Drives the HA Captain's Log control panel."""
    return {
        "running": _run_lock.locked(),
        "job": _current["job"],
        "date": _current["date"],
        "transcripts": _transcript_dates(),
    }


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
