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
import re
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
    "SUMMARIZER_MODEL", os.path.expanduser("~/transcribe/models/Qwen3-8B-Q4_K_M.gguf")
)
# Summarizer runs AFTER transcription (whose subprocess has exited and freed the
# GPU), so it can use the GPU - ~10x faster than CPU. Needs a CUDA-enabled
# llama-cpp-python build. On the 6GB card, Qwen-7B Q4 (~4.7GB) fits only with a
# modest context (KV cache grows with n_ctx), so cap at 8k and let the
# hierarchical chunker feed it smaller slices. Both are env-overridable; set
# SUMMARIZER_GPU_LAYERS=0 to fall back to CPU (slower but no CUDA build needed).
# 30 fits Qwen3-8B on the 6GB card (full offload OOMs); it also fully offloads
# the smaller 7B (which has fewer layers). Env-overridable; 0 forces CPU.
SUMMARIZER_GPU_LAYERS = int(os.environ.get("SUMMARIZER_GPU_LAYERS", "30"))
SUMMARIZER_CTX = int(os.environ.get("SUMMARIZER_CTX", "8192"))

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

NEVER write (this is the whole point - be strict):
- Names of customers or staff, or anything identifying a specific person.
- Contact info (phone, email, address).
- Health/medical details tied to an individual. You MAY note "a wellness-tea
  consultation occurred" in the aggregate - never the person or their specifics.
- ANY personal-life content overheard on the floor that is not about running the
  tea shop. Drop it entirely - do NOT summarize it. This includes: someone's
  schooling or college, jobs/side-businesses (e.g. solar sales), hobbies, art
  fairs, museum or travel mentions, family, relationships, religion, politics,
  people's personal struggles or feelings, and small talk. If a line is a person
  talking about their own life rather than a shop transaction, it does not belong
  in the log.
- Verbatim quotes that could identify someone. Gossip or interpersonal conflict.

EXAMPLES of what to DROP (never include lines like these):
- "Personal struggles with college shared by a staff member"
- "Museum visit / art fair / travel conversation by a customer"
- "Discussion on solar panel sales tactics"
- "Staff member's feelings about workload"

RULES:
- When in doubt, leave it out. A shorter, safer log beats an oversharing one.
- Transcription is lossy (camera mic). Only list a product if it is a plausible
  real tea / herb / ingredient / blend. If a name looks garbled or nonsensical
  (e.g. "breakfast assaulting piece", "acrimon teat", "blue oasis", a random
  word), DROP it - never guess or list it. A missing item beats a made-up one.
- In "Notable / follow-ups", lead with the SINGLE most important actionable
  item, stated specifically - especially any cash / payment / order discrepancy,
  with its amount and what to reconcile. Then the rest. Merge duplicates into
  one bullet per real event, not several.
- Keep it operational: teas, categories, orders, payment/equipment issues, traffic.
- Output ONLY the markdown log in the exact format given. No preamble, no
  <think> tags, no reasoning - just the log. /no_think"""

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

The FIRST speech was captured at {first_ts} and the LAST at {last_ts}. Use these
as the real "Hours active" (do NOT invent a wider window). Describe traffic
relative to the [HH:00] time markers in the transcript.

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


NOTES_SYSTEM = SYSTEM_PROMPT + (
    "\n\nFor THIS step you are taking rough notes on one slice of the day. Output a "
    "short de-identified bullet list of what happened (products/topics discussed, "
    "traffic feel, any operational events). No names/PII. No format headers - just "
    "bullets."
)

NOTES_TEMPLATE = "Notes for time slice {i} of {n} (times are HH:00 markers):\n\n{chunk}"

FINAL_FROM_NOTES = """Write the Captain's Log for {weekday} {date} from these \
de-identified notes taken across the day's Tea One audio. Merge and de-dupe them \
per policy into ONE log in EXACTLY this format:

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

The FIRST speech was captured at {first_ts} and the LAST at {last_ts}. Use these
as the real "Hours active" (do NOT invent a wider window).

NOTES:
{notes}"""


def _active_window(transcript: str) -> tuple[str, str]:
    """First and last speech timestamps as 12h am/pm, so the summary reports the
    REAL active hours instead of inventing a window from the [HH:00] markers."""
    lines = [l for l in transcript.splitlines() if "|" in l]
    if not lines:
        return "unknown", "unknown"

    def ampm(line):
        ts = line.split("|", 1)[0].strip().split()  # ["2026-07-19","09:39:54","MDT"]
        if len(ts) < 2 or ":" not in ts[1]:
            return "unknown"
        h, m, *_ = ts[1].split(":")
        h = int(h)
        ap = "am" if h < 12 else "pm"
        h12 = h % 12 or 12
        return f"{h12}:{m} {ap}"

    return ampm(lines[0]), ampm(lines[-1])


def _compact_transcript(transcript: str) -> str:
    """Per-line 'YYYY-MM-DD HH:MM:SS TZ | text' timestamps dominate the token
    count (~15 tokens each). Replace them with a single '[HH:00]' marker when the
    hour changes, keeping time-of-day context at a fraction of the token cost."""
    out, last_hour = [], None
    for ln in transcript.splitlines():
        if "|" in ln:
            ts, _, text = ln.partition("|")
            text = text.strip()
            parts = ts.split()
            hh = parts[1][:2] if len(parts) >= 2 and ":" in parts[1] else None
            if hh and hh != last_hour:
                out.append(f"[{hh}:00]")
                last_hour = hh
            if text:
                out.append(text)
    return "\n".join(out)


def _chunk_by_tokens(llm, text: str, budget: int) -> list[str]:
    """Split text into line-blocks each under `budget` tokens."""
    chunks, cur, cur_tok = [], [], 0
    for ln in text.splitlines():
        t = len(llm.tokenize(("\n" + ln).encode(), add_bos=False))
        if cur and cur_tok + t > budget:
            chunks.append("\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(ln)
        cur_tok += t
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _strip_think(text: str) -> str:
    """Qwen3 emits <think>...</think> reasoning by default. Even with /no_think we
    strip it defensively so raw reasoning tokens never reach the log."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.replace("<think>", "").replace("</think>", "").strip()


REDACT_SYSTEM = """You are a privacy redactor for a tea shop's operational log. \
You are given a draft Captain's Log. Return it UNCHANGED except remove any line \
that violates the policy, then output the cleaned log in the same format.

REMOVE any bullet that contains:
- a person's name, or anything identifying an individual;
- personal-life content not about running the shop (schooling/college, jobs or
  side-businesses like solar sales, hobbies, art fairs, museums, travel, family,
  relationships, religion, politics, someone's feelings/struggles, small talk);
- health/medical details tied to a person;
- something that reads like garbled audio rather than a real shop event.

Also FIX garbled product names from the lossy mic: if a "product" is not a
plausible real tea/herb/ingredient/flavor (e.g. "cold bread cookies", "mullet
tea", "banana teas", a random phrase), delete just that item
from its bullet - do not list it. Keep genuine but unusual products (honey bush,
rooibos, Russian Caravan, Tulsi, cactus nectar, rainbow splint). When unsure
whether a tea is real, drop it - unless it is one of the named genuine products
above.

Keep everything operational (teas, orders, payment/equipment issues, traffic).
Do not add commentary. Output ONLY the cleaned markdown log. /no_think"""


def _summarize(transcript: str, day: datetime) -> str:
    print("[pipeline] loading summarizer...", file=sys.stderr, flush=True)
    # Load with a fallback chain: the configured layer count, then fewer, then
    # CPU. Covers the edge case where Chloe's image tool is holding VRAM at run
    # time - the summary still completes (slower) instead of failing the job.
    llm = None
    for layers in [SUMMARIZER_GPU_LAYERS, 20, 0]:
        try:
            llm = Llama(model_path=SUMMARIZER_MODEL, n_ctx=SUMMARIZER_CTX, n_threads=8,
                        n_gpu_layers=layers, verbose=False)
            break
        except Exception as e:
            print(f"[pipeline] load with {layers} GPU layers failed ({e}); trying fewer",
                  file=sys.stderr, flush=True)
    if llm is None:
        raise RuntimeError("summarizer failed to load on GPU and CPU")
    wk, ds = day.strftime("%A"), day.strftime("%Y-%m-%d")
    first_ts, last_ts = _active_window(transcript)
    compact = _compact_transcript(transcript)
    INPUT_BUDGET = SUMMARIZER_CTX - 2600  # leave room for system + template + output

    def _gen(system, user, max_tokens):
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=0.3, top_p=0.9, repeat_penalty=1.1,
        )
        return _strip_think(out["choices"][0]["message"]["content"])

    def _final(body_field, body):
        tmpl = USER_TEMPLATE if body_field == "transcript" else FINAL_FROM_NOTES
        return _gen(SYSTEM_PROMPT, tmpl.format(
            weekday=wk, date=ds, first_ts=first_ts, last_ts=last_ts, **{body_field: body}), 1200)

    if len(llm.tokenize(compact.encode(), add_bos=False)) <= INPUT_BUDGET:
        draft = _final("transcript", compact)
    else:
        # Long day: chunk -> de-identified notes per slice -> merge into the log.
        chunks = _chunk_by_tokens(llm, compact, INPUT_BUDGET)
        print(f"[pipeline] long day: {len(chunks)} slices -> notes -> final", file=sys.stderr, flush=True)
        notes = [
            _gen(NOTES_SYSTEM, NOTES_TEMPLATE.format(i=i, n=len(chunks), chunk=ch), 500)
            for i, ch in enumerate(chunks, 1)
        ]
        draft = _final("notes", "\n".join(notes))

    # Second, independent redaction pass: re-read the draft ONLY to strip anything
    # personal/identifying/garbled that slipped through. Cheap on GPU (~seconds).
    print("[pipeline] redaction pass...", file=sys.stderr, flush=True)
    return _gen(REDACT_SYSTEM, f"Draft to clean:\n\n{draft}", 1200)


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
