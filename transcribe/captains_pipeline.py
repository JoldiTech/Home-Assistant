#!/usr/bin/env python3
"""Self-contained Captain's Log pipeline for the AI box.

Home Assistant triggers this (via the LAN trigger service) once a day. It runs
entirely on the AI box - no Claude/cloud session in the loop:

  pull Tea One audio from UniFi Protect (for the window HA specified)
    -> transcribe with faster-whisper large-v3 (GPU)
    -> fetch the business day from the dashboard datalog API (sales, shipping,
       support, calls, texts, timeclock) + Slack staff chat
    -> weave POS orders into the transcript, then de-identify + summarize with
       a dedicated instruct model (Qwen3-8B, GPU)
    -> correlation pass: link draft bullets to specific orders/tickets/calls
    -> redaction pass, then append deterministic business sections (numbers
       never pass through the LLM)
    -> commit the Captain's Log markdown to the private repo's captains-log branch

Raw transcripts stay in ~/captains_transcripts/ on this box (never in git) and
are reused on reruns — transcription is the ~30 min stage, the LLM stages are
minutes. FORCE_RETRANSCRIBE=1 redoes the audio; DELETE_TRANSCRIPTS=1 restores
the old delete-on-success behavior.

The business day is 6pm-6pm Mountain: the log for date D covers 6pm on D-1
through 6pm on D (after-hours online orders / emails / texts are handled at
next opening, so they land on the next day's log). The audio transcript still
covers store hours of calendar day D.

Privacy: strict de-identification applies to CAPTURED AUDIO only. Structured
business records (POS orders, Slack, tickets, timeclock, call/text metadata)
keep real names - that's the point of having them.

Usage:  python3 captains_pipeline.py 2026-07-20
        (date optional; defaults to today, America/Denver)

Secrets from /etc/nmteaco/captains.env (mode 600), never hardcoded:
  GITHUB_TOKEN        fine-grained PAT with contents:read+write on the repo
  GITHUB_REPO         e.g. JoldiTech/Home-Assistant  (optional; default below)
  DATALOG_API_TOKEN   bearer token for the dashboard datalog endpoints
  DASHBOARD_BASE_URL  optional; default https://www.nmteaco.com
  SLACK_BOT_TOKEN     optional; bot token with channels:history + users:read
  SLACK_CHANNELS      optional; comma-separated channel IDs to read

The transcription half is delegated to transcribe_day.py (which loads and frees
large-v3 in its own process, so the GPU is clear before the summarizer loads).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
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

# dashboard.nmteaco.com, NOT www.nmteaco.com — www sits behind a Cloudflare
# bot challenge that blocks non-browser callers; the dashboard hostname
# serves tools/ directly (same host the GitHub deploy webhook uses).
DEFAULT_DASHBOARD = "https://dashboard.nmteaco.com"
DATALOG_ENDPOINTS = ("sales", "shipping", "support", "calls", "texts", "timeclock")

SOURCE_LINE = (
    "_Source: Tea One mic → UniFi Protect → faster-whisper large-v3 (GPU) → "
    "de-identified by a local instruct model on the AI box. Raw transcript "
    "discarded after this log was written. Business data: dashboard datalog "
    "API + Slack._"
)

# --- policy (the summarizer MUST follow this) ---------------------------------
# Mirrors captains_log/README.md. Strict de-identification applies to AUDIO;
# structured business records keep real names. This is the judgment the whole
# system exists to do well.
SYSTEM_PROMPT = """You write a store's daily "Captain's Log" - an operational \
summary of the shop's day, NOT a transcript of who said what.

Your input mixes THREE kinds of material - treat them differently:

1. AUDIO - shop-floor speech transcribed from the camera mic. Lossy, garbled,
   and PRIVATE: everything overheard must be de-identified and filtered.
2. POS lines - "[14:32] ⟦POS $23.50 — Earl Grey 2oz ×1⟧". Ground truth from
   the register, NOT audio. Amounts, items, and times are exact: never treat
   them as garble, never alter them. Use them to confirm products heard on
   audio and to connect a conversation to an actual sale (a sample offered on
   audio, then the item in a POS line minutes later, is worth noting).
3. SLACK - staff work chat ("===== SLACK #channel ====="). A written business
   record: staff names appearing there are fine to use in the log.

NAME RULE: a person's name may appear in the log ONLY if it comes from a
structured source (POS / SLACK). Anyone merely overheard on AUDIO stays
anonymous - no customer or staff names from audio, ever.

INCLUDE:
- Store rhythm: active hours, busy vs quiet stretches, overall traffic feel.
- Product/topic interest: which teas, categories, and questions came up.
- Operational events worth remembering: order/payment issues, stock/supply
  mentions, equipment problems, notable large/curbside/wholesale orders.
- Staff-surfaced business observations (from audio de-identified, from Slack
  with names). Anything actionable for running the shop.

NEVER write (applies to AUDIO content - be strict):
- Names or anything identifying a specific person overheard on audio.
- Contact info (phone, email, address).
- Health/medical details tied to an individual. You MAY note "a wellness-tea
  consultation occurred" in the aggregate - never the person or their specifics.
- ANY personal-life content overheard on the floor that is not about running the
  tea shop. Drop it entirely - do NOT summarize it. This includes: someone's
  schooling or college, jobs/side-businesses (e.g. solar sales), hobbies, art
  fairs, museum or travel mentions, family, relationships, religion, politics,
  people's personal struggles or feelings, and small talk.
- Verbatim quotes that could identify someone. Gossip or interpersonal conflict.

RULES:
- When in doubt, leave it out. A shorter, safer log beats an oversharing one.
- Audio transcription is lossy. Only list a product from AUDIO if it is a
  plausible real tea / herb / ingredient / blend - or if a POS line confirms
  it. If an audio product name looks garbled ("breakfast assaulting piece",
  "acrimon teat"), DROP it - never guess. POS product names are always real.
- In "Notable / follow-ups", lead with the SINGLE most important actionable
  item, stated specifically - especially any cash / payment / order discrepancy,
  with its amount and what to reconcile. Then the rest. Merge duplicates into
  one bullet per real event, not several.
- Keep it operational: teas, categories, orders, payment/equipment issues, traffic.
- Output ONLY the markdown log in the exact format given. No preamble, no
  <think> tags, no reasoning - just the log. /no_think"""

LOG_FORMAT = """# Captain's Log — {weekday} {date}

**Hours active:** …
**Traffic:** …

## Product & topics
- …

## Notable / follow-ups
- …

## Staff & ops notes
- …

""" + SOURCE_LINE

USER_TEMPLATE = """Write the Captain's Log for {weekday} {date} from this Tea One \
transcript. AUDIO lines are plain text after [HH:00] markers; POS register lines \
look like "[HH:MM] ⟦POS …⟧"; SLACK blocks may follow the transcript. Combine per \
policy.

Use EXACTLY this format:

""" + LOG_FORMAT + """

The FIRST speech was captured at {first_ts} and the LAST at {last_ts}. Use these
as the real "Hours active" (do NOT invent a wider window). Describe traffic
relative to the [HH:00] time markers in the transcript.

TRANSCRIPT:
{transcript}"""

NOTES_TEMPLATE = "Notes for time slice {i} of {n} (times are HH:00 markers):\n\n{chunk}"

FINAL_FROM_NOTES = """Write the Captain's Log for {weekday} {date} from these \
notes taken across the day's Tea One audio + POS lines (SLACK blocks may follow). \
Merge and de-dupe them per policy into ONE log in EXACTLY this format:

""" + LOG_FORMAT + """

The FIRST speech was captured at {first_ts} and the LAST at {last_ts}. Use these
as the real "Hours active" (do NOT invent a wider window).

NOTES:
{notes}"""

CORRELATE_SYSTEM = """You connect a tea shop's draft Captain's Log to the day's \
business records. The draft was written reading chronologically, so a bullet \
about (say) an end-of-day payment discrepancy could not reference the earlier \
order it concerns. You see the whole day at once - fix that.

You are given RECORDS (orders, support tickets, calls - each with id, time,
amount, items) and the DRAFT log. Where a draft bullet clearly refers to one
of the records, append a parenthetical reference to that bullet, e.g.:
  "(likely order #58212, $43.50 at 2:14pm)"
  "(ticket #91: Jane Miller, 'Missing tin from order')"
Match on time proximity, dollar amounts, and item names. Rules:
- Use ONLY ids/amounts/names that appear in RECORDS - never invent one.
- If no confident match exists, leave the bullet exactly as it is.
- Prefix inferred links with "likely" unless the amount matches exactly.
- Change NOTHING else: no rewording, no adding or removing bullets.
Output ONLY the annotated markdown log. /no_think"""

REDACT_SYSTEM = """You are a privacy redactor for a tea shop's operational log. \
You are given a draft Captain's Log. Return it UNCHANGED except remove any line \
that violates the policy, then output the cleaned log in the same format.

The log mixes content from shop-floor AUDIO (must stay de-identified) with
structured business records - POS lines, Slack staff chat, and record
references like "(likely order #58212, $43.50 at 2:14pm)" or "(ticket #91:
Jane Miller, …)". KEEP the structured material: names inside order/ticket
references or attributed to Slack, and every id / dollar amount / time in a
parenthetical reference, are business records - do not strip them.

REMOVE any bullet that contains:
- a name of someone merely overheard on audio (no record/Slack attribution);
- personal-life content not about running the shop (schooling/college, jobs or
  side-businesses like solar sales, hobbies, art fairs, museums, travel, family,
  relationships, religion, politics, someone's feelings/struggles, small talk);
- health/medical details tied to a person;
- something that reads like garbled audio rather than a real shop event.

Also FIX garbled product names from the lossy mic: if a "product" is not a
plausible real tea/herb/ingredient/flavor (e.g. "cold bread cookies", "mullet
tea", "banana teas", a random phrase), delete just that item from its bullet -
UNLESS it appears inside a ⟦POS …⟧ line or an order reference (register data
is never garble). Keep genuine but unusual products (honey bush, rooibos,
Russian Caravan, Tulsi, cactus nectar, rainbow splint). When unsure whether a
tea is real, drop it - unless it is register-confirmed or named above.

Keep everything operational (teas, orders, payment/equipment issues, traffic).
Do not add commentary. Output ONLY the cleaned markdown log. /no_think"""

NOTES_SYSTEM = SYSTEM_PROMPT + (
    "\n\nFor THIS step you are taking rough notes on one slice of the day. Output a "
    "short bullet list of what happened (products/topics discussed, traffic feel, "
    "operational events, POS sales with amounts). De-identify audio content; keep "
    "POS amounts/items exact. No format headers - just bullets."
)


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


def _warn(msg: str):
    print(f"[pipeline] {msg}", file=sys.stderr, flush=True)


# --- business data (dashboard datalog API) ------------------------------------

def _datalog_get(env: dict, endpoint: str, params: dict) -> dict | None:
    token = env.get("DATALOG_API_TOKEN", "")
    if not token:
        return None
    base = env.get("DASHBOARD_BASE_URL", DEFAULT_DASHBOARD).rstrip("/")
    url = f"{base}/tools/datalog/{endpoint}.php?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            if isinstance(data, dict) and data.get("ok"):
                return data
            _warn(f"datalog {endpoint}: {str(data)[:200]}")
            return None
        except Exception as e:
            _warn(f"datalog {endpoint} attempt {attempt} failed: {e}")
            if attempt == 1:
                time.sleep(5)
    return None


def _fetch_business(env: dict, date_str: str) -> dict:
    """One dict per endpoint (None where unavailable). sales includes per-order
    detail for weaving + the correlation index."""
    if not env.get("DATALOG_API_TOKEN"):
        _warn("DATALOG_API_TOKEN not set - skipping business data")
        return {k: None for k in DATALOG_ENDPOINTS}
    biz = {}
    for ep in DATALOG_ENDPOINTS:
        params = {"date": date_str}
        if ep == "sales":
            params["detail"] = "1"
        biz[ep] = _datalog_get(env, ep, params)
    return biz


# --- Slack staff chat ---------------------------------------------------------

def _slack_api(token: str, method: str, params: dict) -> dict:
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fetch_slack(env: dict, start_dt: datetime, end_dt: datetime) -> str:
    """Staff messages in the business window, real display names, as labeled
    blocks for the summarizer. Top-level channel messages only (v1)."""
    token = env.get("SLACK_BOT_TOKEN", "")
    channels = [c.strip() for c in env.get("SLACK_CHANNELS", "").split(",") if c.strip()]
    if not token or not channels:
        return ""

    names: dict[str, str] = {}

    def display_name(uid: str) -> str:
        if uid not in names:
            try:
                resp = _slack_api(token, "users.info", {"user": uid})
                prof = (resp.get("user") or {}).get("profile") or {}
                names[uid] = prof.get("display_name") or prof.get("real_name") \
                    or (resp.get("user") or {}).get("real_name") or uid
            except Exception:
                names[uid] = uid
        return names[uid]

    blocks = []
    for ch in channels:
        lines = []
        cursor = None
        ch_name = ch
        try:
            info = _slack_api(token, "conversations.info", {"channel": ch})
            ch_name = (info.get("channel") or {}).get("name") or ch
        except Exception:
            pass
        try:
            for _page in range(5):
                params = {
                    "channel": ch,
                    "oldest": f"{start_dt.timestamp():.6f}",
                    "latest": f"{end_dt.timestamp():.6f}",
                    "limit": "200",
                    "inclusive": "true",
                }
                if cursor:
                    params["cursor"] = cursor
                resp = _slack_api(token, "conversations.history", params)
                if not resp.get("ok"):
                    _warn(f"slack #{ch_name}: {resp.get('error')}")
                    break
                for msg in resp.get("messages", []):
                    text = (msg.get("text") or "").strip()
                    if not text or msg.get("subtype") in ("channel_join", "channel_leave"):
                        continue
                    who = display_name(msg["user"]) if msg.get("user") else (msg.get("username") or "bot")
                    text = re.sub(r"<@(U[A-Z0-9]+)>", lambda m: "@" + display_name(m.group(1)), text)
                    ts = datetime.fromtimestamp(float(msg.get("ts", 0)), TZ)
                    lines.append((float(msg.get("ts", 0)), f"[{ts:%H:%M}] {who}: {text}"))
                cursor = (resp.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        except Exception as e:
            _warn(f"slack #{ch_name} fetch failed: {e}")
        if lines:
            lines.sort(key=lambda t: t[0])
            blocks.append(f"===== SLACK #{ch_name} =====\n" + "\n".join(l for _, l in lines))
    return "\n\n".join(blocks)


# --- weaving POS orders into the transcript -----------------------------------

def _order_pos_line(o: dict) -> str | None:
    """Render one order as a transcript-format line so it merges by timestamp."""
    t = o.get("time_local") or ""
    if len(t) < 19:
        return None
    items = ", ".join(
        f"{i['name']} ×{i['qty']:g}" if isinstance(i.get("qty"), (int, float)) and i["qty"] != 1
        else str(i.get("name", ""))
        for i in (o.get("items") or [])[:8]
    ) or "no line items"
    tags = ""
    if o.get("wholesale"):
        tags += " WHOLESALE"
    if o.get("has_sample"):
        tags += " (sample given)"
    return f"{t} MT | ⟦POS ${o.get('total', 0):.2f}{tags} — {items}⟧"


def _weave_orders(transcript: str, sales: dict | None, date_str: str) -> str:
    """Merge in-store register orders into the transcript by timestamp. Only
    orders on the transcript's calendar day are woven (the 6pm-6pm business
    window also reaches into the previous evening - those belong in the
    sections, not this day's audio timeline)."""
    if not sales:
        return transcript
    pos_lines = []
    for o in sales.get("orders") or []:
        if o.get("source") != "revel":
            continue
        if not str(o.get("time_local", "")).startswith(date_str):
            continue
        line = _order_pos_line(o)
        if line:
            pos_lines.append(line)
    if not pos_lines:
        return transcript
    merged = [l for l in transcript.splitlines() if l.strip()] + pos_lines
    # Lines share the 'YYYY-MM-DD HH:MM:SS' prefix, so a lexicographic sort on
    # the first 19 chars is chronological.
    merged.sort(key=lambda l: l[:19])
    _warn(f"wove {len(pos_lines)} POS orders into the transcript")
    return "\n".join(merged)


# --- correlation index (for the second pass) ----------------------------------

_SCRUB_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SCRUB_PHONE = re.compile(r"\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")


def _scrub(s: str) -> str:
    return _SCRUB_PHONE.sub("[phone]", _SCRUB_EMAIL.sub("[email]", s or "")).strip()


def _records_index(biz: dict) -> str:
    """Compact one-line-per-record digest of the day for the correlation pass."""
    lines = []
    sales = biz.get("sales") or {}
    for o in (sales.get("orders") or [])[:80]:
        t = (o.get("time_local") or "")[11:16]
        items = ", ".join(str(i.get("name", "")) for i in (o.get("items") or [])[:4])
        kind = "POS" if o.get("source") == "revel" else "online"
        lines.append(f"order #{o.get('id')} ({kind}) {t} ${o.get('total', 0):.2f} — {items}")
    support = biz.get("support") or {}
    for tkt in (support.get("created") or []) + (support.get("closed") or []):
        t = (tkt.get("time_local") or "")[11:16]
        lines.append(
            f"ticket #{tkt.get('id')} {t} {_scrub(tkt.get('customer', ''))}: "
            f"\"{_scrub(tkt.get('subject', ''))[:70]}\""
        )
    calls = biz.get("calls") or {}
    for c in (calls.get("list") or [])[:40]:
        t = (c.get("time_local") or "")[11:16]
        what = "; ".join(c.get("actions") or []) or c.get("status", "")
        lines.append(f"call {t} {c.get('caller_id') or c.get('number')} — {what}"[:110])
    return "\n".join(lines)


# --- deterministic business sections ------------------------------------------

def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _business_sections(biz: dict) -> str:
    parts = ["## Business day (6pm–6pm MT)"]

    s = biz.get("sales")
    if s:
        on, ins, pu = s.get("online") or {}, s.get("in_store") or {}, s.get("pickup") or {}
        parts.append(
            f"**Online:** {_money(on.get('retail_revenue'))} retail ({on.get('retail_orders', 0)} orders)"
            + (f" + {_money(on.get('wholesale_revenue'))} wholesale ({on.get('wholesale_orders', 0)})"
               if on.get("wholesale_orders") else "")
        )
        pickupN = (pu.get("retail_orders") or 0) + (pu.get("wholesale_orders") or 0)
        parts.append(
            f"**In-store:** {_money(ins.get('retail_revenue'))} retail ({ins.get('retail_orders', 0)} orders)"
            + (f" + {_money(ins.get('wholesale_revenue'))} wholesale ({ins.get('wholesale_orders', 0)})"
               if ins.get("wholesale_orders") else "")
            + (f" · {pickupN} pickup order{'s' if pickupN != 1 else ''} "
               f"({_money((pu.get('retail_revenue') or 0) + (pu.get('wholesale_revenue') or 0))})"
               if pickupN else "")
        )
    else:
        parts.append("_Sales data unavailable._")

    sh = biz.get("shipping")
    if sh:
        carriers = ", ".join(f"{name} {n}" for name, n in sorted(
            (sh.get("by_carrier") or {}).items(), key=lambda kv: -kv[1]))
        parts.append(
            f"**Shipped:** {sh.get('orders_shipped', 0)} orders · "
            f"{sh.get('labels_created', 0)} labels"
            + (f" ({sh.get('labels_voided', 0)} voided)" if sh.get("labels_voided") else "")
            + f" · postage {_money(sh.get('postage_cost'))}"
            + (f" — {carriers}" if carriers else "")
        )
    else:
        parts.append("_Shipping data unavailable._")

    sup = biz.get("support")
    parts.append("\n## Support")
    if sup:
        parts.append(
            f"{sup.get('tickets_created', 0)} new · {sup.get('inbound_messages', 0)} inbound messages · "
            f"{sup.get('tickets_closed', 0)} closed · {sup.get('open_now', 0)} open now"
        )
        for tkt in sup.get("created") or []:
            when = (tkt.get("time_local") or "")[11:16]
            parts.append(f"- New #{tkt.get('id')} {when} — {_scrub(tkt.get('customer', ''))}: "
                         f"\"{_scrub(tkt.get('subject', ''))}\" ({tkt.get('category', '')})")
        for tkt in sup.get("closed") or []:
            by = f" by {tkt.get('closed_by')}" if tkt.get("closed_by") else ""
            parts.append(f"- Closed #{tkt.get('id')} — {_scrub(tkt.get('customer', ''))}: "
                         f"\"{_scrub(tkt.get('subject', ''))}\"{by}")
    else:
        parts.append("_Support data unavailable._")

    calls, texts = biz.get("calls"), biz.get("texts")
    parts.append("\n## Comms")
    if calls or texts:
        bits = []
        if calls:
            mins = round((calls.get("total_talk_seconds") or 0) / 60)
            bits.append(f"{calls.get('calls', 0)} calls ({mins} min)")
        if texts:
            bits.append(f"texts {texts.get('inbound', 0)} in / {texts.get('outbound', 0)} out")
            if texts.get("unreplied_now"):
                bits.append(f"{texts['unreplied_now']} text{'s' if texts['unreplied_now'] != 1 else ''} awaiting reply")
        parts.append("**" + " · ".join(bits) + "**")
        if calls:
            for action, n in sorted((calls.get("by_action") or {}).items(), key=lambda kv: -kv[1]):
                parts.append(f"- {action}: {n}")
    else:
        parts.append("_Call/text data unavailable._")

    tc = biz.get("timeclock")
    parts.append("\n## Staff")
    if tc:
        parts.append(f"**{tc.get('total_hours', 0)} labor hours**")
        for sft in tc.get("shifts") or []:
            fmt12 = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%-I:%M%p").lower() if s else "…"
            brk = f", {sft['break_minutes']}m break" if sft.get("break_minutes") else ""
            hrs = f" ({sft['hours']}h{brk})" if sft.get("hours") is not None else " (still clocked in)"
            loc = f" — {sft['location']}" if sft.get("location") else ""
            parts.append(f"- {sft.get('employee')}: {fmt12(sft.get('clock_in_local'))}–"
                         f"{fmt12(sft.get('clock_out_local'))}{hrs}{loc}")
        if tc.get("clocked_in_now"):
            parts.append("Clocked in at log time: " + ", ".join(tc["clocked_in_now"]))
    else:
        parts.append("_Timeclock data unavailable._")

    return "\n".join(parts)


# --- transcription ------------------------------------------------------------

def _transcribe(date_str: str) -> tuple[str, Path]:
    """Run transcribe_day.py in its own process; return (transcript_text, log_path).

    Transcription is by far the longest stage (~30 min for a full day), so an
    existing transcript for the date is reused — reruns for prompt/pipeline
    testing only pay for the LLM stages. Set FORCE_RETRANSCRIBE=1 to redo the
    audio (e.g. after a transcription-quality change)."""
    log_path = Path.home() / "captains_transcripts" / f"tea_one_{date_str}.log"
    if (log_path.exists() and log_path.stat().st_size > 0
            and not os.environ.get("FORCE_RETRANSCRIBE")):
        _warn(f"reusing existing transcript {log_path}")
        return log_path.read_text(), log_path
    _warn(f"transcribing {date_str}...")
    proc = subprocess.run(
        [sys.executable, str(TRANSCRIBE_SCRIPT), date_str],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"transcription failed: {proc.stderr[-2000:]}")
    return proc.stdout, log_path


def _active_window(transcript: str) -> tuple[str, str]:
    """First and last speech timestamps as 12h am/pm, so the summary reports the
    REAL active hours instead of inventing a window from the [HH:00] markers.
    POS lines are excluded - they are register events, not speech."""
    lines = [l for l in transcript.splitlines() if "|" in l and "⟦POS" not in l]
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
    hour changes, keeping time-of-day context at a fraction of the token cost.
    POS register lines keep a minute-precision '[HH:MM]' prefix - correlating a
    conversation with a sale needs minutes; ambient audio doesn't."""
    out, last_hour = [], None
    for ln in transcript.splitlines():
        if "|" in ln:
            ts, _, text = ln.partition("|")
            text = text.strip()
            parts = ts.split()
            hhmm = parts[1][:5] if len(parts) >= 2 and ":" in parts[1] else None
            if "⟦POS" in text:
                out.append(f"[{hhmm}] {text}" if hhmm else text)
                continue
            hh = hhmm[:2] if hhmm else None
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


def _summarize(transcript: str, day: datetime, slack_text: str, records: str) -> str:
    _warn("loading summarizer...")
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
            _warn(f"load with {layers} GPU layers failed ({e}); trying fewer")
    if llm is None:
        raise RuntimeError("summarizer failed to load on GPU and CPU")
    wk, ds = day.strftime("%A"), day.strftime("%Y-%m-%d")
    first_ts, last_ts = _active_window(transcript)
    compact = _compact_transcript(transcript)

    def _tok(s: str) -> int:
        return len(llm.tokenize(s.encode(), add_bos=False))

    slack_block = f"\n\nSLACK (staff work chat, real names OK per policy):\n{slack_text}" if slack_text else ""
    INPUT_BUDGET = SUMMARIZER_CTX - 2600 - _tok(slack_block)  # room for system + template + output

    def _gen(system, user, max_tokens):
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=0.3, top_p=0.9, repeat_penalty=1.1,
        )
        return _strip_think(out["choices"][0]["message"]["content"])

    def _final(body_field, body):
        tmpl = USER_TEMPLATE if body_field == "transcript" else FINAL_FROM_NOTES
        return _gen(SYSTEM_PROMPT, tmpl.format(
            weekday=wk, date=ds, first_ts=first_ts, last_ts=last_ts,
            **{body_field: body + slack_block}), 1200)

    if _tok(compact) <= INPUT_BUDGET:
        draft = _final("transcript", compact)
    else:
        # Long day: chunk -> notes per slice -> merge into the log.
        chunks = _chunk_by_tokens(llm, compact, INPUT_BUDGET)
        _warn(f"long day: {len(chunks)} slices -> notes -> final")
        notes = [
            _gen(NOTES_SYSTEM, NOTES_TEMPLATE.format(i=i, n=len(chunks), chunk=ch), 500)
            for i, ch in enumerate(chunks, 1)
        ]
        draft = _final("notes", "\n".join(notes))

    # Correlation pass: the draft was written reading chronologically, so a
    # late-day conversation can't cite the earlier order it concerns. Re-read
    # the finished draft against the whole day's records and attach ids.
    if records:
        # Keep the records digest inside the context window alongside the draft.
        while _tok(records) > SUMMARIZER_CTX - 3000 and "\n" in records:
            records = records.rsplit("\n", max(1, records.count("\n") // 4))[0]
        _warn("correlation pass...")
        draft = _gen(CORRELATE_SYSTEM, f"RECORDS:\n{records}\n\nDRAFT:\n{draft}", 1400)

    # Independent redaction pass: re-read the draft ONLY to strip anything
    # personal/identifying/garbled that slipped through. Cheap on GPU (~seconds).
    _warn("redaction pass...")
    return _gen(REDACT_SYSTEM, f"Draft to clean:\n\n{draft}", 1400)


# --- git ----------------------------------------------------------------------

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
        _warn("cloning repo...")
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
        _warn("no change to commit")
        return
    _git("commit", "-m", f"Captain's log {date_str}")
    for attempt in range(4):
        r = _git("push", "origin", LOG_BRANCH, check=False)
        if r.returncode == 0:
            _warn("pushed")
            return
        _warn(f"push failed (try {attempt + 1}): {r.stderr[-300:]}")
    raise RuntimeError("git push failed after retries")


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(TZ).strftime("%Y-%m-%d")
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ)
    env = _load_env()

    # Business window for log date D: 6pm MT on D-1 through 6pm MT on D.
    window_end = day.replace(hour=18, minute=0, second=0, microsecond=0)
    window_start = window_end - timedelta(days=1)

    biz = _fetch_business(env, date_str)
    slack_text = _fetch_slack(env, window_start, window_end)

    transcript, log_path = _transcribe(date_str)
    have_speech = bool(transcript.strip())
    have_data = any(biz.values()) or bool(slack_text)

    if not have_speech and not have_data:
        _warn(f"{date_str}: no speech and no business data - nothing to log")
        return

    if have_speech:
        transcript = _weave_orders(transcript, biz.get("sales"), date_str)
        markdown = _summarize(transcript, day, slack_text, _records_index(biz))
    else:
        _warn(f"{date_str}: no speech captured - business sections only")
        markdown = (
            f"# Captain's Log — {day:%A} {date_str}\n\n"
            f"_No speech captured today._\n\n{SOURCE_LINE}"
        )

    markdown = markdown.rstrip() + "\n\n" + _business_sections(biz)
    _commit_and_push(date_str, markdown, env)

    # Transcripts stay on this box (never in git) so test reruns skip the
    # ~30 min re-transcription. DELETE_TRANSCRIPTS=1 restores delete-on-success.
    if os.environ.get("DELETE_TRANSCRIPTS"):
        log_path.unlink(missing_ok=True)
        _warn(f"{date_str}: done, raw transcript deleted")
    else:
        _warn(f"{date_str}: done, transcript kept at {log_path}")


if __name__ == "__main__":
    main()
