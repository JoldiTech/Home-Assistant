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

Privacy: the rule is linkage, not names. Names tied to operational facts
(promises, holds, orders) stay - they're the point. What never survives is a
named person tied to sensitive content (health, personal life, attributed
remarks); the redaction pass breaks the link, keeping the event.

Usage:  python3 captains_pipeline.py 2026-07-20
        (date optional; defaults to today, America/Denver)

Secrets from /etc/nmteaco/captains.env (mode 600), never hardcoded:
  GITHUB_TOKEN        fine-grained PAT with contents:read+write on the repo
  GITHUB_REPO         e.g. JoldiTech/Home-Assistant  (optional; default below)
  DATALOG_API_TOKEN   bearer token for the dashboard datalog endpoints
  DASHBOARD_BASE_URL  optional; default https://dashboard.nmteaco.com
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
    "_Source: Tea One mic → faster-whisper large-v3 → de-identified by a local "
    "model on the AI box · business data from the dashboard datalog API and "
    "Slack. Raw transcript never leaves the box._"
)

# --- policy (the summarizer MUST follow this) ---------------------------------
# Mirrors captains_log/README.md. Privacy = no linkage between a named person
# and sensitive content; names on operational facts are welcome. This is the
# judgment the whole system exists to do well.
COMPANY_CONTEXT = """THE SHOP - facts to write with (never explain these in \
the log; the reader owns the company):
New Mexico Tea Company - small Albuquerque business, founded 2006, shop at
Mountain Rd & 12th St between Old Town and the arts district, plus the 12th
Street Emporium and a warehouse ("WH") for packing and online-order shipping.
Sells 400+ loose-leaf teas, herbs, and spices - many imported directly, many
blended in house. House/NM blends include Bamboo Mountain Oolong, Cota (a
regional NM herb), Chaco rooibos, and seasonal packs (Fiesta Collection,
Southwestern Pack, Pride Collection, holiday packs). Customers: Old Town
walk-in tourists, local regulars, wellness/herb buyers, a tea club, and
wholesale accounts (restaurants, wineries, shops). Retail orders look like
T-NNNNN; vendor POs like PO-NNNN.
Because EVERYTHING here is specialty loose-leaf, words like "premium",
"specialty", or "artisanal" say nothing - name the actual tea/product, or
leave it out."""

SYSTEM_PROMPT = COMPANY_CONTEXT + """

You write the shop's daily Captain's Log - a SHORT operational narrative in
the spirit of a ship's log, followed by open action items and durable
observations. It is read two ways: by the owner tomorrow morning in one
minute, and by someone months from now searching old logs for when something
started, what a customer asked for, or what was promised. Write for both.

WHY THIS LOG EXISTS: the numbers - sales totals, order lists, tickets,
shipments - are stored in databases forever and can be pulled for any date.
NEVER spend narrative restating them (a stats block is appended
automatically). What is stored NOWHERE else is what people SAID today: what
customers wanted and couldn't get, what they complained about or loved, what
staff promised, why something went wrong, what almost happened. That is the
only thing that evaporates at closing time - capturing it is your entire job.

THE UNIQUE RECORD - always capture, with specifics:
- Unmet demand: products/sizes/services customers asked for that we don't
  carry or were out of. Name them exactly as asked; if the SAME product is
  asked for more than once in the input, say how many times. You only see
  ONE day - never claim cross-day patterns ("this week", "again").
- Feedback: specific praise or complaints about a product, price, or the
  shop - named, not "customers were happy".
- Commitments: anything staff promised anyone (holds, callbacks, special
  orders, samples to send, quotes to prepare).
- Causes: the WHY behind anything a database will only show as a number - a
  register discrepancy's story, why the day was busy or dead (event, tour
  group, weather, construction).
- Incidents & equipment: things broken, failing, or almost-failing; odd
  situations; anything staff worked around.
- Staff observations and ideas about running the shop.

THE TEST for every line: could a future reader searching these logs want
this? A SPECIFIC fact (a named product request, an amount, a stated reason)
is searchable forever - err toward keeping it. A VAGUE line ("discussed
inventory", "customers browsed teas") answers no future question at any
length - cut it. Routine retail (brewing questions, recommendations,
tastings) gets at most one clause of color in the day's rhythm.

Use the records (POS sales, tickets) to ANCHOR conversations - a sample
offered on audio that shows up minutes later as a register sale is one
connected observation - never to report totals.

Your input mixes these materials, all context for ONE story - never say
where a fact came from (no "per Slack", no "on audio"):
- AUDIO: shop-floor speech from the camera mic. Lossy, garbled, PRIVATE.
- POS lines "[14:32] ⟦POS $23.50 — Earl Grey 2oz ×1⟧": register ground
  truth; amounts/items/times are exact, never garble.
- SLACK blocks: staff work chat.
- BUSINESS RECORDS block: the day's support tickets, customer texts, and
  call notes.

FORMAT:
1. 2-4 short narrative paragraphs - the story of the day: its rhythm woven
   into a sentence or two, then the events and their causes, noting in a
   clause when a problem got resolved.
2. "## Unresolved" - ONLY things still open at close: discrepancies to
   reconcile (with amounts), unmet commitments, broken equipment, reorders.
   0-5 bullets, each a concrete object + action someone can pick up
   tomorrow ("reconcile the $NN register shortfall", "reorder [the
   out-of-stock product]") - fill the brackets from TODAY'S input only;
   these are format examples, never content. A product on a ⟦POS⟧ line
   SOLD - a sale is proof of stock, the opposite of unmet demand. "Reorder
   X" is valid ONLY when speech says X was asked for and unavailable, ran
   out, or must be restocked - never because X appears in a sale. BANNED:
   vague care-taking ("confirm the customer was satisfied"), resolved
   items, garble-based items, and any item whose amount or product you
   cannot point to in the input. NEVER more than 5 bullets - if you have
   more candidates, keep only the 5 most consequential. Empty beats padded.
3. "## Worth remembering" - 0-5 durable signals a future reader would want:
   unmet demand (named, with counts), specific feedback, first signs of
   something (equipment aging, a recurring confusion), promises already in
   motion. Not actions - observations. Empty beats padded.

STYLE: no non-events ("a customer asked about X but no action was taken" -
cut). Register sales in plain words; the ⟦ ⟧ markup never appears in output.

GROUNDING - the cardinal rule: every event, cause, number, and product in
your output must trace to a specific input line. NEVER invent an explanation
that isn't stated - no guessed festivals, weather, tour groups, or events.
A busy day with no stated reason is just "busy"; a shortfall with no stated
cause is "unexplained". Counts too: one mention is "a customer", not
"several" - only count what you can point to. An invented cause or inflated
count poisons a permanent record; an honest gap does not.

PRIVACY - the rule is about LINKAGE, not names:
- Names are fine, and often the point: a promise, hold, special order, or
  callback NEEDS the name to be actionable (name the customer on the promise).
  Only use names ACTUALLY present in the day's material - never invent one, and
  never copy an example name from these instructions into the log.
- What must NEVER appear is a named person tied to sensitive or personal
  content: health/medical details, personal-life circumstances, complaints
  about other people, or "so-and-so said/felt X". Keep the operational fact,
  break the link - "a customer asked about teas safe during pregnancy" is
  fine; naming her in that sentence is not. No contact info ever.
- Personal-life chatter that has NO operational value (school, jobs, hobbies,
  travel, family, relationships, religion, politics, feelings, small talk)
  still gets dropped entirely - not because of names, because it's noise.
- No gossip; no attributing opinions or verbatim remarks to a named person.
- Audio is lossy: never repeat a garbled phrase as fact; if a detail looks
  like mis-transcription, drop it. When in doubt privacy-wise, keep the
  event and drop the identifying half.

Output ONLY the markdown log in the exact format given. No preamble, no
<think> tags, no reasoning - just the log. /no_think"""

LOG_FORMAT = """# Captain's Log — {weekday} {date}

<2-4 short narrative paragraphs>

## Unresolved
- <0-5 specific open items>

## Worth remembering
- <0-5 durable observations: unmet demand, feedback, early signals>"""

USER_TEMPLATE = """Write the Captain's Log for {weekday} {date} from this Tea One \
transcript. AUDIO lines are plain text after [HH:00] markers; POS register lines \
look like "[HH:MM] ⟦POS …⟧"; SLACK blocks may follow the transcript.

Use EXACTLY this format:

""" + LOG_FORMAT + """

The FIRST speech was captured at {first_ts} and the LAST at {last_ts} - the day
ran between those times; do not invent a wider window.

TRANSCRIPT:
{transcript}"""

NOTES_TEMPLATE = "Notes for time slice {i} of {n} (times are HH:00 markers):\n\n{chunk}"

FINAL_FROM_NOTES = """Write the Captain's Log for {weekday} {date} from these \
notes taken across the day's Tea One audio + POS lines (SLACK blocks may follow). \
Merge them per policy into ONE log in EXACTLY this format:

""" + LOG_FORMAT + """

The FIRST speech was captured at {first_ts} and the LAST at {last_ts} - the day
ran between those times; do not invent a wider window.

NOTES:
{notes}"""

CORRELATE_SYSTEM = """You connect a tea shop's draft Captain's Log to the day's \
business records. The draft was written reading chronologically, so a bullet \
about (say) an end-of-day payment discrepancy could not reference the earlier \
order it concerns. You see the whole day at once - fix that.

You are given RECORDS (orders, support tickets, calls - each with id, time,
amount, items) and the DRAFT log. Where a narrative sentence or Unresolved
bullet clearly refers to one of the records, append a parenthetical
reference, e.g.:
  "(likely order #58212, $43.50 at 2:14pm)"
  "(ticket #91: Jane Miller, 'Missing tin from order')"
Write references in that plain style - never paste raw transcript syntax like
"⟦POS ...⟧" into the log. Match on time proximity, dollar amounts, and item
names. Rules:
- Annotate ONLY a specific EVENT: a payment/order discrepancy, a problem to
  reconcile, or one notable sale. NEVER attach ids to product mentions or
  general narrative color - a product name is not an order.
- Annotate at most 3 places in the whole log. Zero is a fine answer.
- Use ONLY ids/amounts/names that appear in RECORDS - never invent one.
- A match needs corroboration (time AND amount, or amount AND item). A
  mismatched amount is a NON-match: never attach a $175.97 order to a $70
  discrepancy. If not confident, leave the bullet exactly as it is.
- Prefix inferred links with "likely" unless the amount matches exactly.
- Change NOTHING else: no rewording, no adding or removing bullets.
Output ONLY the annotated markdown log. /no_think"""


REDACT_TEMPLATE = """You are a privacy redactor for a tea shop's operational \
log. You are given a draft Captain's Log. Return it UNCHANGED except for the \
removals below, then output the cleaned log in the same format.

THE PRIVACY RULE IS ABOUT LINKAGE, NOT NAMES. Names attached to operational
facts stay - naming the customer on a promise, hold, or special order is the
point of the log (use ONLY names that actually appear in the day's material;
never carry an example name from these instructions into the output). What
must never survive is a NAMED person
tied to sensitive content: health/medical details, personal-life
circumstances, or "so-and-so said/felt X". Fix by breaking the link - keep
the event, drop the name from THAT sentence ("a customer asked about teas
safe during pregnancy") - not by deleting the whole fact.

REMOVE entirely any sentence or bullet that contains:
- personal-life content with no operational value (schooling/college, jobs or
  side-businesses, hobbies, art fairs, museums, travel, family, relationships,
  religion, politics, someone's feelings/struggles, small talk);
- contact info (phone, email, address) for any individual;
- a verbatim quote attributed to a person, gossip, or something that reads
  like garbled audio rather than a real shop event.

KEEP every id / dollar amount / time in parenthetical record references like
"(likely order #58212, $43.50 at 2:14pm)" - those are business records.
Fix garbled product names from the lossy mic: if a "product" is not a
plausible real tea/herb/ingredient/flavor, drop just that mention - register
(POS/order-reference) product names are never garble.

If a CATALOG NOTES section precedes the draft, each listed quoted name was
checked against the shop's real product catalog and does not exist. For each:
if it is clearly a misheard version of one of its "closest real products",
replace it with that exact catalog name everywhere; if it is a plausible real
tea/product we simply don't carry, keep it and append " (not in our catalog)"
at its first mention - that is valuable unmet demand, never delete it; if it
is not a plausible product at all, remove just that mention. Never copy the
CATALOG NOTES section itself into the output.

Do not add commentary. Output ONLY the cleaned markdown log. /no_think"""

NOTES_SYSTEM = SYSTEM_PROMPT + (
    "\n\nFor THIS step you are taking rough notes on one slice of the day. Output a "
    "short bullet list capturing ONLY the unique record: unmet demand (exact product "
    "names customers asked for and didn't get - this comes from SPEECH only; a "
    "product on a ⟦POS⟧ line SOLD, which is the opposite of unmet demand, so never "
    "note sold items or their product lists), specific feedback, promises staff "
    "made, causes/explanations, problems and discrepancies (with amounts), equipment "
    "or stock issues, staff observations, plus one bullet on traffic feel. Routine "
    "retail chit-chat (brewing questions, browsing, tastings) and ordinary completed "
    "sales produce NO bullets. Keep POS amounts exact when a discrepancy or promise "
    "references them. Names are fine when they carry an operational fact "
    "(a promise, hold, or order); never pair a name with personal or health "
    "content. No format headers - just bullets. At most 8 bullets per slice."
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


PRODUCTS_CACHE = Path.home() / "captains_transcripts" / "products_cache.json"


def _fetch_products(env: dict) -> list[dict]:
    """The 3dcart catalog (canonical product list) - names + live stock. Fresh
    fetch each run, falling back to the last good copy on disk so a dashboard
    outage degrades to slightly-stale stock numbers, not a lost feature."""
    data = _datalog_get(env, "products", {}) if env.get("DATALOG_API_TOKEN") else None
    if data and data.get("products"):
        try:
            PRODUCTS_CACHE.parent.mkdir(exist_ok=True)
            PRODUCTS_CACHE.write_text(json.dumps(data["products"]))
        except OSError:
            pass
        return data["products"]
    try:
        cached = json.loads(PRODUCTS_CACHE.read_text())
        _warn(f"products endpoint unavailable - using cached catalog ({len(cached)} items)")
        return cached
    except Exception:
        _warn("no product catalog available - name cross-check skipped")
        return []


def _norm_name(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'")
    s = re.sub(r"\s*\|\s*(bulk|organic)\s*$", "", s)
    s = re.sub(r"\s*-\s*organic\s*$", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _extract_quoted(markdown: str) -> list[str]:
    """Product-ish quoted strings from the narrative half of the log (the
    deterministic sections aren't part of the input here). Transcript garble
    often lands as ONE quoted comma-list ("Munch's Blend, 4M, L'Oreal
    Troubles") - the real product hiding inside never matches unless the list
    is split, so components are extracted alongside the full string."""
    seen, out = set(), []

    def _add(s: str):
        s = s.strip().strip(",. ")
        if len(s) >= 3 and not s.replace(".", "").replace("$", "").isdigit() \
                and s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)

    for q in re.findall(r'[""“"]([^""”"]{3,80})[""”"]', markdown):
        _add(q)
        if q.count(",") >= 1:
            for part in q.split(","):
                _add(part)
    return out


def _catalog_index(products: list[dict]) -> dict[str, str]:
    norm_to_name: dict[str, str] = {}
    for p in products:
        n = _norm_name(p.get("name") or "")
        # Variants normalize together ("X | Bulk", "X - Organic") - show the
        # shortest original as the canonical spelling.
        if n and (n not in norm_to_name or len(p["name"]) < len(norm_to_name[n])):
            norm_to_name[n] = p["name"]
    return norm_to_name


def _match_catalog(markdown: str, products: list[dict]):
    """Split the draft's unknown quoted product names into deterministic
    auto-fixes and cases needing judgment.

    Scoring is the mean of full-string similarity and distinct-head similarity
    (shared trailing tokens stripped): "Munch's Blend" vs "Monk's Blend" is
    close on BOTH, while "French Blend" only looks close because of the shared
    " Blend" tail - the head comparison kills that false positive. A clear
    winner becomes an auto-fix applied in code (the 8B model applying its own
    replacements proved unreliable); everything else goes to the redaction
    pass with candidates attached.

    Returns (auto_fixes: [(quoted, catalog_name)], review: [(quoted, [names])]).
    """
    import difflib

    def ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, a, b).ratio()

    def head_score(qa: str, qb: str) -> float:
        ta, tb = qa.split(), qb.split()
        while ta and tb and ta[-1] == tb[-1]:
            ta.pop(); tb.pop()
        if not ta and not tb:
            return 1.0
        return ratio(" ".join(ta), " ".join(tb))

    norm_to_name = _catalog_index(products)
    norms = list(norm_to_name)
    auto_fixes, review = [], []
    for q in _extract_quoted(markdown):
        qn = _norm_name(q)
        if not qn or qn in norm_to_name:
            continue
        scored = sorted(
            (((ratio(qn, n) + head_score(qn, n)) / 2, n) for n in norms),
            reverse=True,
        )[:3]
        if scored and scored[0][0] >= 0.93:
            auto_fixes.append((q, norm_to_name[scored[0][1]]))
            continue
        top = scored[0][0] if scored else 0.0
        second = scored[1][0] if len(scored) > 1 else 0.0
        if top >= 0.72 and top - second >= 0.10:
            auto_fixes.append((q, norm_to_name[scored[0][1]]))
        else:
            review.append((q, [norm_to_name[n] for s, n in scored if s >= 0.55]))
    return auto_fixes, review


# --- Slack staff chat ---------------------------------------------------------

def _slack_api(token: str, method: str, params: dict) -> dict:
    url = f"https://slack.com/api/{method}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _fetch_slack(env: dict, start_dt: datetime, end_dt: datetime) -> tuple[str, set]:
    """Staff messages in the business window, real display names, as labeled
    blocks for the summarizer. Returns (text, names seen) - the names feed the
    redactor's allowed list. Top-level channel messages only (v1)."""
    token = env.get("SLACK_BOT_TOKEN", "")
    channels = [c.strip() for c in env.get("SLACK_CHANNELS", "").split(",") if c.strip()]
    if not token or not channels:
        return "", set()

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
    return "\n\n".join(blocks), {n for n in names.values() if n and not n.startswith("U")}


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


def _context_block(biz: dict) -> str:
    """The day's written customer touchpoints (tickets, texts, call notes) as a
    labeled stream for the summarizer - so a floor conversation about 'that
    email' or 'the lady who texted' can connect to the actual record."""
    lines = []
    support = biz.get("support") or {}
    for kind, lst in (("new", support.get("created")), ("closed", support.get("closed"))):
        for tkt in lst or []:
            t = (tkt.get("time_local") or "")[11:16]
            lines.append(f"TICKET {kind} #{tkt.get('id')} {t} {tkt.get('customer', '')}: "
                         f"\"{(tkt.get('subject') or '')[:90]}\"")
    for m in ((biz.get("texts") or {}).get("messages") or [])[:30]:
        t = (m.get("time_local") or "")[11:16]
        who = m.get("name") or m.get("phone", "")
        arrow = "from" if m.get("direction") == "inbound" else "to"
        body = (m.get("body") or "").replace("\n", " ")[:120]
        lines.append(f"TEXT {t} {arrow} {who}: \"{body}\"")
    for c in ((biz.get("calls") or {}).get("list") or []):
        for n in c.get("notes") or []:
            t = (c.get("time_local") or "")[11:16]
            lines.append(f"CALL NOTE {t} ({c.get('caller_id') or c.get('number')}): "
                         f"{(n.get('note') or '')[:120]}")
    if not lines:
        return ""
    return "===== BUSINESS RECORDS (today's tickets / texts / call notes) =====\n" + "\n".join(lines)


_ANNOT_RE = re.compile(r"\s*\((?:likely\s+)?(order|ticket)\s*#([A-Za-z0-9\-]+)[^)]*\)")


_UNAVAILABLE_RE = re.compile(
    r"reorder|out of stock|sold out|unavailable|not available|wasn't available|"
    r"didn't have|don't carry|not carried|ran out|restock", re.IGNORECASE)


def _flag_stock_contradictions(markdown: str, products: list[dict]) -> str:
    """Deterministic cross-check the user asked for: when the floor says a
    product was out of stock but the website (3dcart, the canonical catalog)
    shows stock, that mismatch is itself the finding - either the site is
    overselling or the floor missed inventory. Appends the flag in code so the
    stock number never passes through the LLM."""
    if not products:
        return markdown
    stock: dict[str, float] = {}
    display: dict[str, str] = {}
    for p in products:
        n = _norm_name(p.get("name") or "")
        if n:
            stock[n] = max(stock.get(n, 0.0), float(p.get("stock") or 0))
            if n not in display or len(p["name"]) < len(display[n]):
                display[n] = p["name"]
    # The model doesn't reliably quote product names in bullets, so match
    # catalog names by word boundary in the normalized line too. Longest-first
    # so "monks grenadine blend" claims its words before "monks blend" can.
    boundary_names = sorted((n for n in stock if len(n) >= 8), key=len, reverse=True)
    out = []
    for line in markdown.splitlines():
        if line.lstrip().startswith("- ") and _UNAVAILABLE_RE.search(line) \
                and "website shows" not in line:
            flagged, seen_norms = [], set()
            for q in re.findall(r'[""“"]([^""”"]{3,80})[""”"]', line):
                qn = _norm_name(q)
                if stock.get(qn, 0) > 0 and qn not in seen_norms:
                    seen_norms.add(qn)
                    flagged.append(q)
            nline = _norm_name(line)
            for n in boundary_names:
                if n in seen_norms or any(n in s for s in seen_norms):
                    continue
                if re.search(r"\b" + re.escape(n) + r"\b", nline) and stock[n] > 0:
                    seen_norms.add(n)
                    flagged.append(display[n])
            if flagged:
                line = line.rstrip() + " ⚠ website shows in stock: " + ", ".join(flagged)
        out.append(line)
    return "\n".join(out)


def _cap_bullet_lists(markdown: str, cap: int = 6) -> str:
    """Deterministic guard behind the summarizer's 0-5 rule: on POS-heavy days
    the 8B model has produced degenerate drafts listing every SOLD product as a
    'Reorder' bullet (50+ lines), which then truncates the output mid-word.
    Prompts lower the odds; this makes the failure bounded - keep the first
    `cap` bullets of each list section and drop the rest."""
    out, in_list, kept = [], False, 0
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_list = line.strip() in ("## Unresolved", "## Worth remembering")
            kept = 0
        elif in_list and line.lstrip().startswith("- "):
            kept += 1
            if kept > cap:
                if kept == cap + 1:
                    _warn(f"capping runaway bullet list at {cap} items")
                continue
        out.append(line)
    return "\n".join(out)


def _validate_annotations(markdown: str, biz: dict) -> str:
    """Deterministic guard behind the correlation pass: drop any record
    reference whose id isn't in the day's records, and any order reference
    whose amount contradicts the amount already stated in the same line
    (the 8B model keeps attaching $20.99 orders to $70 shortfalls no matter
    what the prompt says)."""
    amounts = {}
    for o in ((biz.get("sales") or {}).get("orders") or []):
        amounts[str(o.get("id"))] = float(o.get("total") or 0)
    sup = biz.get("support") or {}
    ticket_ids = {str(t.get("id"))
                  for t in (sup.get("created") or []) + (sup.get("closed") or [])}

    out = []
    for line in markdown.splitlines():
        def repl(m, line=line):
            kind, rid = m.group(1).lower(), m.group(2)
            if kind == "ticket":
                return m.group(0) if rid in ticket_ids else ""
            if rid not in amounts:
                return ""
            line_amts = [float(a.replace(",", ""))
                         for a in re.findall(r"\$([\d,]+(?:\.\d+)?)", line[:m.start()])]
            if line_amts:
                rec = amounts[rid]
                if all(abs(rec - a) > 0.25 * max(rec, a, 1.0) for a in line_amts):
                    return ""
            return m.group(0)
        out.append(_ANNOT_RE.sub(repl, line))
    return "\n".join(out)


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
    """One compact stats block. Every line renders straight from JSON."""
    parts = ["## By the numbers (6pm–6pm MT)"]

    s = biz.get("sales")
    if s:
        on, ins, pu = s.get("online") or {}, s.get("in_store") or {}, s.get("pickup") or {}
        onN  = (on.get("retail_orders") or 0) + (on.get("wholesale_orders") or 0)
        insN = (ins.get("retail_orders") or 0) + (ins.get("wholesale_orders") or 0)
        onRev  = (on.get("retail_revenue") or 0) + (on.get("wholesale_revenue") or 0)
        insRev = (ins.get("retail_revenue") or 0) + (ins.get("wholesale_revenue") or 0)
        line = (f"**Sales:** online {_money(onRev)} ({onN} orders) · "
                f"in-store {_money(insRev)} ({insN} orders)")
        wsRev = (on.get("wholesale_revenue") or 0) + (pu.get("wholesale_revenue") or 0)
        if wsRev:
            line += f" · incl. wholesale {_money(wsRev)}"
        pickupN = (pu.get("retail_orders") or 0) + (pu.get("wholesale_orders") or 0)
        if pickupN:
            line += (f" · pickup {pickupN} "
                     f"({_money((pu.get('retail_revenue') or 0) + (pu.get('wholesale_revenue') or 0))})")
        parts.append(line)
    else:
        parts.append("**Sales:** _unavailable_")

    sh = biz.get("shipping")
    if sh:
        carriers = ", ".join(f"{name} {n}" for name, n in sorted(
            (sh.get("by_carrier") or {}).items(), key=lambda kv: -kv[1]))
        parts.append(
            f"**Shipped:** {sh.get('orders_shipped', 0)} orders"
            + (f" ({sh.get('labels_voided', 0)} voided)" if sh.get("labels_voided") else "")
            + f" · postage {_money(sh.get('postage_cost'))}"
            + (f" — {carriers}" if carriers else "")
        )
    else:
        parts.append("**Shipped:** _unavailable_")

    sup = biz.get("support")
    if sup:
        line = (f"**Support:** {sup.get('tickets_created', 0)} new · "
                f"{sup.get('inbound_messages', 0)} inbound · "
                f"{sup.get('tickets_closed', 0)} closed · {sup.get('open_now', 0)} open")
        parts.append(line)
        for kind, lst in (("new", sup.get("created")), ("closed", sup.get("closed"))):
            for tkt in lst or []:
                parts.append(f"- {kind} #{tkt.get('id')} — {_scrub(tkt.get('customer', ''))}: "
                             f"\"{_scrub(tkt.get('subject', ''))}\"")
    else:
        parts.append("**Support:** _unavailable_")

    calls, texts = biz.get("calls"), biz.get("texts")
    if calls or texts:
        bits = []
        if calls:
            mins = round((calls.get("total_talk_seconds") or 0) / 60)
            bits.append(f"{calls.get('calls', 0)} calls ({mins} min)")
        if texts:
            bits.append(f"texts {texts.get('inbound', 0)} in / {texts.get('outbound', 0)} out")
            if texts.get("unreplied_now"):
                bits.append(f"{texts['unreplied_now']} awaiting reply")
        parts.append("**Comms:** " + " · ".join(bits))
    else:
        parts.append("**Comms:** _unavailable_")

    tc = biz.get("timeclock")
    if tc:
        def fmt12(s):
            return (datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%-I:%M%p").lower()
                    if s else "…")
        crew = ", ".join(
            f"{sft.get('employee')} {fmt12(sft.get('clock_in_local'))}–{fmt12(sft.get('clock_out_local'))}"
            for sft in tc.get("shifts") or []
        )
        parts.append(f"**Staff:** {tc.get('total_hours', 0)}h — {crew}" if crew
                     else f"**Staff:** {tc.get('total_hours', 0)}h")
    else:
        parts.append("**Staff:** _unavailable_")

    return "\n".join(parts)


# --- GPU coordination with Chloe (imagegen) -----------------------------------

def _free_vram_mb() -> int:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return -1  # can't tell - proceed and let the load attempt decide

def _ensure_gpu(min_free_mb: int, wait_s: int = 180) -> bool:
    """If VRAM is short, ask Chloe (imagegen, loopback :8189) to release the
    GPU - her models unload without losing any session state - then wait for
    the memory to actually come back. Best-effort: callers still have their
    own failure paths."""
    free = _free_vram_mb()
    if free < 0 or free >= min_free_mb:
        return True
    _warn(f"only {free}MB VRAM free (need {min_free_mb}) - asking Chloe to release the GPU")
    try:
        req = urllib.request.Request("http://127.0.0.1:8189/api/release-gpu", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            _warn(f"imagegen release-gpu: {r.read().decode()[:120]}")
    except Exception as e:
        _warn(f"imagegen release-gpu unreachable ({e}) - waiting anyway")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(5)
        free = _free_vram_mb()
        if free >= min_free_mb:
            return True
    _warn(f"VRAM still short after {wait_s}s ({free}MB free)")
    return False


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
    # large-v3 has no CPU fallback: make sure Chloe isn't holding the card.
    _ensure_gpu(4200)
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


def _summarize(transcript: str, day: datetime, slack_text: str, records: str,
               context_text: str = "", products: list[dict] | None = None) -> str:
    _warn("loading summarizer...")
    # Prefer a clear card (fast GPU path); the layer-fallback below still
    # covers the case where the VRAM never frees up.
    _ensure_gpu(3800, wait_s=60)
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
    if context_text:
        slack_block += f"\n\n{context_text}"
    INPUT_BUDGET = SUMMARIZER_CTX - 2600 - _tok(slack_block)  # room for system + template + output

    def _gen(system, user, max_tokens, temperature=0.3):
        out = llm.create_chat_completion(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=temperature, top_p=0.9, repeat_penalty=1.1,
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
        draft = _gen(CORRELATE_SYSTEM, f"RECORDS:\n{records}\n\nDRAFT:\n{draft}", 1800, temperature=0.1)

    # Catalog check: quoted product names that don't exist in the real (3dcart)
    # catalog. Clear mishears are corrected HERE, in code - string replacement
    # is exact and the 8B model applying its own replacements proved unreliable.
    # This reaches the names the POS weave can't: out-of-stock requests never
    # ring up, so they have no order line to correct against. Ambiguous names
    # ride into the redaction pass below as CATALOG NOTES.
    catalog_notes = ""
    if products:
        auto_fixes, review = _match_catalog(draft, products)
        for q, name in auto_fixes:
            _warn(f"catalog fix: \"{q}\" -> \"{name}\"")
            draft = draft.replace(q, name)
        if review:
            catalog_notes = "\n\nCATALOG NOTES (quoted names not in our catalog):\n" + "\n".join(
                f'- "{q}"' + (f" - closest real products: {', '.join(c)}" if c else " - nothing similar")
                for q, c in review
            )

    # Independent redaction pass: re-read the draft ONLY to break name-to-
    # sensitive-content links and strip personal-life/garble that slipped
    # through. Cheap on GPU (~seconds).
    _warn("redaction pass...")
    draft = _gen(REDACT_TEMPLATE, f"Draft to clean:{catalog_notes}\n\n{draft}", 1800, temperature=0.1)
    # Second roll of the same pass: redaction is "return unchanged except
    # removals", so re-running is near-idempotent and cheap (~seconds), and
    # the 8B model's misses are roll-to-roll independent enough that a second
    # look catches linkage it left in the first time (observed repeatedly:
    # named person + health interest surviving a single pass).
    _warn("redaction pass (second look)...")
    return _gen(REDACT_TEMPLATE, f"Draft to clean:\n\n{draft}", 1800, temperature=0.1)


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
    products = _fetch_products(env)
    slack_text, slack_names = _fetch_slack(env, window_start, window_end)

    transcript, log_path = _transcribe(date_str)
    have_speech = bool(transcript.strip())
    have_data = any(biz.values()) or bool(slack_text)

    if not have_speech and not have_data:
        _warn(f"{date_str}: no speech and no business data - nothing to log")
        return

    if have_speech:
        transcript = _weave_orders(transcript, biz.get("sales"), date_str)
        markdown = _summarize(transcript, day, slack_text, _records_index(biz),
                              _context_block(biz), products)
    else:
        _warn(f"{date_str}: no speech captured - business sections only")
        markdown = (
            f"# Captain's Log — {day:%A} {date_str}\n\n"
            f"_No speech captured today._"
        )

    # Belt-and-braces: the weave markup must never ship, whatever the LLM does,
    # and record references must point at real, amount-consistent records.
    markdown = markdown.replace("⟦", "").replace("⟧", "")
    markdown = _validate_annotations(markdown, biz)
    markdown = _cap_bullet_lists(markdown)
    markdown = _flag_stock_contradictions(markdown, products)
    markdown = (markdown.rstrip() + "\n\n" + _business_sections(biz)
                + "\n\n" + SOURCE_LINE)
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
