#!/usr/bin/env python3
"""Self-contained Captain's Log pipeline for the AI box.

Home Assistant triggers this (via the LAN trigger service) once a day. It runs
entirely on the AI box - no Claude/cloud session in the loop:

  pull Tea One audio from UniFi Protect (for the window HA specified)
    -> transcribe with faster-whisper large-v3 (GPU)
    -> de-identify + summarize with a dedicated instruct model (Qwen2.5-7B, CPU,
       kept separate from the abliterated Chloe chat model)
    -> commit the Captain's Log markdown to the private repo's captains-log branch
    -> delete the raw transcript

Usage:  python3 captains_pipeline.py 2026-07-20
        (date optional; defaults to today, America/Denver)

Secrets from /etc/nmteaco/captains.env (mode 600), never hardcoded:
  GITHUB_TOKEN   fine-grained PAT with contents:read+write on the repo
  GITHUB_REPO    e.g. JoldiTech/Home-Assistant  (optional; default below)

The transcription half is delegated to transcribe_day.py (which loads and frees
large-v3 in its own process, so the GPU is clear before the summarizer loads).
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from llama_cpp import Llama

TZ = ZoneInfo("America/Denver")
HERE = Path(__file__).resolve().parent
TRANSCRIBE_SCRIPT = HERE / "transcribe_day.py"

SUMMARIZER_MODEL = os.environ.get(
    "SUMMARIZER_MODEL", os.path.expanduser("~/transcribe/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf")
)
LLM_CTX = 32768
MAX_TRANSCRIPT_CHARS = 90000  # safety cap so a runaway day can't overflow context

DEFAULT_REPO = "JoldiTech/Home-Assistant"
LOG_BRANCH = "captains-log"
REPO_CLONE = Path.home() / "ha-captains-repo"

# --- de-identification policy (the summarizer MUST follow this) ---------------
# Mirrors captains_log/README.md. This is the judgment the whole system exists
# to do well; keep it strict.
SYSTEM_PROMPT = """You write a store's daily "Captain's Log" - a de-identified, \
aggregate operational summary of shop-floor audio, NOT a transcript of who said what.

INCLUDE (de-identified, aggregate):
- Store rhythm: active hours, busy vs quiet stretches, overall traffic feel.
- Product/topic interest: which teas, categories, and questions came up, as themes.
- Operational events worth remembering: possible order/payment issues, stock/supply
  mentions, equipment problems, notable large/curbside/wholesale orders (amounts ok).
- Staff-surfaced business observations. Anything actionable for running the shop.

NEVER write:
- Names of customers or staff, or anything identifying a specific person.
- Contact info (phone, email, address).
- Health/medical details tied to an individual. You MAY note "a wellness-tea
  consultation occurred" in the aggregate - never the person or their specifics.
- Personal-life specifics (relationships, travel, family, religion, politics).
- Verbatim quotes that could identify someone. Gossip or interpersonal conflict.

RULES:
- When in doubt, leave it out. A shorter, safer log beats an oversharing one.
- Transcription is lossy (camera mic) - flag uncertain items as "possible" and
  never assert shaky specifics as fact.
- Output ONLY the markdown log in the exact format given. No preamble."""

USER_TEMPLATE = """Write the Captain's Log for {weekday} {date} from this Tea One \
transcript (timestamp | text lines). Combine and de-identify per policy.

Use EXACTLY this format:

# Captain's Log — {weekday} {date}

**Hours active:** …
**Traffic:** …

## Product & topics
- …

## Notable / follow-ups
- …

## Staff & ops notes
- …

_Source: Tea One mic → UniFi Protect → faster-whisper large-v3 (GPU) → de-identified by a local instruct model on the AI box. Raw transcript discarded after this log was written._

TRANSCRIPT:
{transcript}"""


def _load_env(path="/etc/nmteaco/captains.env"):
    env = {}
    p = Path(path)
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _transcribe(date_str: str) -> tuple[str, Path]:
    """Run transcribe_day.py in its own process; return (transcript_text, log_path)."""
    print(f"[pipeline] transcribing {date_str}...", file=sys.stderr, flush=True)
    proc = subprocess.run(
        [sys.executable, str(TRANSCRIBE_SCRIPT), date_str],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"transcription failed: {proc.stderr[-2000:]}")
    log_path = Path.home() / "captains_transcripts" / f"tea_one_{date_str}.log"
    return proc.stdout, log_path


def _summarize(transcript: str, day: datetime) -> str:
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        transcript = transcript[:MAX_TRANSCRIPT_CHARS] + "\n[...transcript truncated...]"
    print("[pipeline] loading summarizer...", file=sys.stderr, flush=True)
    llm = Llama(model_path=SUMMARIZER_MODEL, n_ctx=LLM_CTX, n_threads=8, n_gpu_layers=0, verbose=False)
    user = USER_TEMPLATE.format(
        weekday=day.strftime("%A"), date=day.strftime("%Y-%m-%d"), transcript=transcript
    )
    out = llm.create_chat_completion(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        max_tokens=1200, temperature=0.3, top_p=0.9, repeat_penalty=1.1,
    )
    return out["choices"][0]["message"]["content"].strip()


def _git(*args, check=True):
    return subprocess.run(["git", "-C", str(REPO_CLONE), *args], check=check,
                          capture_output=True, text=True)


def _commit_and_push(date_str: str, markdown: str, env: dict):
    token = env.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN not set (in /etc/nmteaco/captains.env)")
    repo = env.get("GITHUB_REPO", DEFAULT_REPO)
    remote = f"https://x-access-token:{token}@github.com/{repo}.git"

    if not (REPO_CLONE / ".git").exists():
        print("[pipeline] cloning repo...", file=sys.stderr, flush=True)
        subprocess.run(["git", "clone", "--branch", LOG_BRANCH, "--single-branch", remote, str(REPO_CLONE)],
                       check=True, capture_output=True, text=True)
    _git("remote", "set-url", "origin", remote)
    _git("fetch", "origin", LOG_BRANCH)
    _git("checkout", LOG_BRANCH)
    _git("reset", "--hard", f"origin/{LOG_BRANCH}")
    # identity (local to this clone; no global config needed)
    _git("config", "user.email", "aibox@nmteaco.com")
    _git("config", "user.name", "NMTea AI Box")

    out_file = REPO_CLONE / "captains_log" / f"{date_str}.md"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(markdown.rstrip() + "\n")
    _git("add", str(out_file.relative_to(REPO_CLONE)))
    status = _git("status", "--porcelain").stdout.strip()
    if not status:
        print("[pipeline] no change to commit", file=sys.stderr, flush=True)
        return
    _git("commit", "-m", f"Captain's log {date_str}")
    for attempt in range(4):
        r = _git("push", "origin", LOG_BRANCH, check=False)
        if r.returncode == 0:
            print("[pipeline] pushed", file=sys.stderr, flush=True)
            return
        print(f"[pipeline] push failed (try {attempt+1}): {r.stderr[-300:]}", file=sys.stderr, flush=True)
    raise RuntimeError("git push failed after retries")


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime("%Y-%m-%d")
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    env = _load_env()

    transcript, log_path = _transcribe(date_str)
    if not transcript.strip():
        print(f"[pipeline] {date_str}: no speech captured - nothing to log", file=sys.stderr, flush=True)
        log_path.unlink(missing_ok=True)
        return

    markdown = _summarize(transcript, day)
    _commit_and_push(date_str, markdown, env)

    # Only after the summary is safely committed: delete the raw transcript.
    log_path.unlink(missing_ok=True)
    print(f"[pipeline] {date_str}: done, raw transcript deleted", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
