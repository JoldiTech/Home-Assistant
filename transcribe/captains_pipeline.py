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
# Keep the Qwen3 hybrid "/no_think" suffix (see _gen). Set 0 for non-hybrid
# instruct models, where it is just noise on the end of every system prompt.
_NOTHINK = os.environ.get("SUMMARIZER_NOTHINK", "1") != "0"

DEFAULT_REPO = "JoldiTech/Home-Assistant"
LOG_BRANCH = "captains-log"
REPO_CLONE = Path.home() / "ha-captains-repo"

# dashboard.nmteaco.com, NOT www.nmteaco.com — www sits behind a Cloudflare
# bot challenge that blocks non-browser callers; the dashboard hostname
# serves tools/ directly (same host the GitHub deploy webhook uses).
DEFAULT_DASHBOARD = "https://dashboard.nmteaco.com"
DATALOG_ENDPOINTS = ("sales", "shipping", "support", "calls", "texts", "timeclock")

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
  The least reliable input you have - a phrase that is not a tea, herb,
  spice or piece of equipment is mis-transcription, not a discovery.
- POS lines, of the form "[HH:MM] ⟦POS $[total] — [product] [size] ×[qty]⟧":
  register ground truth; amounts/items/times are exact, never garble. A
  product on one of these SOLD - it cannot also have been unavailable.
- SLACK blocks: staff work chat.
- BUSINESS RECORDS block: the day's support tickets, customer texts, and
  call notes.
SLACK and BUSINESS RECORDS are the MOST reliable material in the input and
the most likely to be missed: unlike the audio, someone wrote each line down
deliberately. A supply ordered, a system not working, a safety or storage
problem, an open ticket, a request that arrived off the floor - each is a
finding on its own and belongs in the log even if nobody on the floor said a
word about it.

FORMAT:
1. 2-4 short narrative paragraphs - the story of the day: its rhythm woven
   into a sentence or two, then the events and their causes, noting in a
   clause when a problem got resolved.
   The FIRST sentence must contain at least one fact that could ONLY be true
   of this day - the weather, an event, a staffing change, a named product, an
   amount. NEVER open with generic traffic ("a steady flow of customers", "the
   day started busy", "customers inquired about various teas"): those sentences
   are identical every day and carry no information. If nothing distinguished
   the day, open with its first concrete event instead.
2. "## Unresolved" - ONLY things still open at close: discrepancies to
   reconcile (with amounts), unmet commitments, broken equipment, reorders.
   0-5 bullets, each a concrete object + action someone can pick up
   tomorrow ("reconcile the register shortfall", "reorder [the
   out-of-stock product]") - fill the brackets from TODAY'S input only;
   these are format examples, never content. A DISCREPANCY IS NOT A NUMBER
   YOU HEARD: money spoken at the counter ("[amount] today") is a sale total,
   the most ordinary event in a shop. Write a discrepancy ONLY when the input
   actually says money is missing, short, over, uncounted, or does not match -
   if nobody says that, there is no discrepancy, whatever amounts were said
   aloud. Never do arithmetic on two overheard numbers to produce one. A product on a ⟦POS⟧ line
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

EXAMPLES IN THESE INSTRUCTIONS ARE NOT SHOP EVENTS. Everything written inside
[SQUARE BRACKETS] below is a slot to fill from TODAY'S input, never text to
copy. No product, supply, request, situation, amount or name mentioned in
these instructions may appear in the log unless it is also in today's input.

PRIVACY - the rule is about LINKAGE, not names:
- NEVER attribute words to a named STAFF member. Staff requests, opinions,
  observations, complaints and remarks go in the log WITHOUT the name: write
  "a staff request to restock [the supply]", never "restock [the supply] per
  [name]'s request"; write "staff flagged [the equipment] failing", never
  "[name] said [the equipment] is failing". The operational fact is the entire
  point; who said it is not, and naming them turns a work log into a record of
  who said what. This holds even when the remark is harmless.
- CUSTOMER names are fine where they make a commitment actionable: a promise,
  hold, special order, or callback NEEDS the name to be picked up tomorrow.
  Only use names ACTUALLY present in the day's material - never invent one, and
  never copy an example name from these instructions into the log.
- What must NEVER appear is a named person tied to sensitive or personal
  content: health/medical details, personal-life circumstances, complaints
  about other people, or a feeling or opinion attributed to a named person. Keep the operational fact,
  break the link - "a customer asked about teas suitable for [a health
  concern]" is fine; naming her in that sentence is not. No contact info ever.
  Generalise the CONDITION as well as the name: record the need, never the
  diagnosis. Naming the need is fine; naming
  the condition, the medication, or whose it is, is not - in a shop this size a
  specific diagnosis identifies someone even with no name attached. If the whole
  exchange was staff declining to give medical advice, there is no event - drop
  it.
- Personal-life chatter that has NO operational value (school, jobs, hobbies,
  travel, family, relationships, religion, politics, feelings, small talk)
  still gets dropped entirely - not because of names, because it's noise.
- No gossip. Never quote anyone verbatim - named or not. Report what was
  wanted, praised, complained about, or promised in your own words, always
  naming the product, price, or thing at issue; a quote with no product in it
  is unsearchable and cannot be checked against the record.
- Audio is lossy: never repeat a garbled phrase as fact; if a detail looks
  like mis-transcription, drop it. Drop the TOKEN, not the event - if someone
  clearly asked for something but the product name is mangled, describe it in
  your own words from the surrounding lines, or leave the item out; never
  print the mangled string. Nonsense in a tea shop is mis-transcription, not a
  product: a word that is not a tea, herb, spice, or piece of equipment is
  garble however confident it sounds. Never put an unverified phrase in
  quotation marks - a quoted string is treated downstream as a real product
  name. When in doubt privacy-wise, keep the event and drop the identifying
  half.

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

# On a long day the transcript becomes ten slices of notes and the staff chat
# stays raw at the end of the final prompt, where it loses. 2026-07-26's Slack
# was the richest input of any day audited - a supply order placed, the phones
# not reaching the warehouse, a fridge wedged open, an explicit warning about
# heavy stock shelved unsafely - and NONE of it reached the log. It gets its own
# notes pass now so it arrives in the merge as bullets, on equal footing.
SIDE_NOTES_TEMPLATE = """Notes on today's STAFF CHAT and BUSINESS RECORDS. Unlike \
the audio these are not overheard - every line is something a person wrote down on \
purpose, so treat them as the most reliable input of the day, never as background.

Capture every one as a bullet: supplies ordered, equipment or systems not working,
safety and storage problems, stock moved or missing, open tickets, and customer
requests or complaints that arrived off the floor.

{chunk}"""

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
  "(likely order #[id], $[amount] at [time])"
  "(ticket #[id]: [customer name], '[subject]')"
Write references in that plain style - never paste raw transcript syntax like
"⟦POS ...⟧" into the log. Match on time proximity, dollar amounts, and item
names. Rules:
- Annotate ONLY a specific EVENT: a payment/order discrepancy, a problem to
  reconcile, or one notable sale. NEVER attach ids to product mentions or
  general narrative color - a product name is not an order.
- Annotate at most 3 places in the whole log. Zero is a fine answer.
- Use ONLY ids/amounts/names that appear in RECORDS - never invent one.
- A match needs corroboration (time AND amount, or amount AND item). A
  mismatched amount is a NON-match: an order's total that differs from the
  bullet's amount does not belong on that bullet, however close the time.
  If not confident, leave the bullet exactly as it is.
- Prefix inferred links with "likely" unless the amount matches exactly.
- Change NOTHING else: no rewording, no adding or removing bullets.
Output ONLY the annotated markdown log. /no_think"""


REDACT_TEMPLATE = """You are a privacy redactor for a tea shop's operational \
log. You are given a draft Captain's Log. Return it UNCHANGED except for the \
removals below, then output the cleaned log in the same format.

THE PRIVACY RULE IS ABOUT LINKAGE, NOT NAMES. Customer names attached to
operational facts stay - naming the customer on a promise, hold, or special
order is the point of the log (use ONLY names that actually appear in the day's
material; never carry an example name from these instructions into the output).
But a STAFF member's name must never sit on something they said, asked for, or
thought: rewrite "restock [the supply] per [name]'s request" as "restock [the
supply] (a staff request)", and "[name] said [the equipment] is failing" as
"staff reported [the equipment] failing". Keep the fact, drop the staff name.
What must never survive is a NAMED person
tied to sensitive content: health/medical details, personal-life
circumstances, or a feeling attributed to a named person. Fix by breaking the link - keep
the event, drop the name from THAT sentence (record the need, not the
diagnosis or whose it is) - not by deleting the whole fact.

NOTHING IN [SQUARE BRACKETS] ABOVE IS CONTENT. Those are slots to fill from
the draft in front of you. Never introduce a product, supply, request or
situation that is not already in that draft.

REMOVE entirely any sentence or bullet that contains:
- personal-life content with no operational value (schooling/college, jobs or
  side-businesses, hobbies, art fairs, museums, travel, family, relationships,
  religion, politics, someone's feelings/struggles, small talk);
- contact info (phone, email, address) for any individual;
- any verbatim quote (attributed or not), gossip, or something that reads
  like garbled audio rather than a real shop event. Report what was said in
  plain words naming the product or thing at issue, instead of quoting.

KEEP every id / dollar amount / time in parenthetical record references like
"(likely order #[id], $[amount] at [time])" - those are business records.
Fix garbled product names from the lossy mic: if a "product" is not a
plausible real tea/herb/ingredient/flavor, drop just that mention - register
(POS/order-reference) product names are never garble.

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
    "references them. A CUSTOMER name is fine when it carries an operational fact "
    "(a promise, hold, or order); never pair a name with personal or health "
    "content. NEVER put a STAFF member's name on something they said, asked for, "
    "or thought - write 'a staff request to reorder [the item]', not '[name] "
    "asked for it'. Nothing in [square brackets] is content; fill those slots "
    "from this slice only. No format headers - just bullets. At most 8 bullets "
    "per slice.\n\nStart EVERY bullet with one tag in square brackets, chosen "
    "from exactly this list:\n"
    "[UNMET] a product/size/service asked for and not available\n"
    "[PROBLEM] something broken, failing, miscounted, mis-priced or unsafe\n"
    "[PROMISE] something staff committed to - a hold, callback, special order\n"
    "[FEEDBACK] specific praise or complaint about a named product or the shop\n"
    "[CAUSE] the stated reason behind something - weather, an event, staffing\n"
    "[TRAFFIC] the one bullet on how busy the slice felt\n"
    "The tag is how these survive being merged with a dozen other slices; a "
    "bullet without one is discarded."
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


# Mic noise produces confident-looking word salad ("Brunckel, steak, ham, wheat,
# rye, brunckel, Strawberry Black"). Left alone the catalog machinery below will
# happily fuzzy-match that onto a real product and stock-flag it, turning noise
# into an authoritative reorder item. This gate is deliberately CONSERVATIVE:
# a real-but-unstocked product a customer actually asked for is the log's most
# valuable content, so only unmistakable noise is rejected.
_NON_LATIN = re.compile(r"[Ͱ-ϿЀ-ӿ֐-ۿ぀-ヿ一-鿿]")


def _is_garble(q: str) -> bool:
    s = " ".join(q.split())
    if not s:
        return True
    if _NON_LATIN.search(s):          # mixed script - always mis-transcription
        return True
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", s)
    if len(words) >= 7:               # real product names are short
        return True
    if len(words) >= 4 and s.count(",") >= 2:   # comma-salad list
        return True
    return False


def _drop_garble_claims(markdown: str) -> str:
    """Remove any claim whose subject is mis-transcription.

    A garbled 'Reorder "<noise>"' bullet is worse than no bullet: it sends
    someone looking for a product that was never asked for. Three changes from
    the bullets-only version this replaces, each from an observed failure:

    - runs over narrative sentences too. 2026-07-26 shipped `A customer asked
      for "the 16 ounces of the Nahili mill jury and cleaner"` in paragraph one
      (it was a Nilgiri iced tea blend, and it sold).
    - ANY garbled quote sinks the claim, not only an all-garble one. A bullet
      naming one real product and one piece of noise is still unactionable.
    - drops the whole claim rather than the quote. The policy says drop the
      token not the event, but "A customer asked for but it was unavailable"
      is not an event - without the name there is nothing left to act on, and
      printing the noise is what the policy bans outright.
    """
    def reject(text, is_bullet):
        bad = [q for q in re.findall(r'"([^"]{3,})"', text) if _is_garble(q)]
        return f'garbled quoted name "{bad[0][:40]}"' if bad else None

    return _edit_claims(markdown, reject)


# Register training is speech ABOUT orders, not orders. On 2026-07-21 a trainer
# said "I think what I want to do is give you some fake orders" and read out
# three inventions; all three became the log's opening sentence, quoted, as
# genuine unmet demand. Kept deliberately narrow - a bare "training" also
# matches product names ("Training | Chop Sticks") and ordinary shop talk.
_TRAINING_RE = re.compile(
    r"fake order|practice order|example order|for practice|let'?s pretend|"
    r"pretend (?:to be|you'?re)|some fake|practice (?:ringing|transaction|sale)|"
    r"i'?ll be the customer|say i'?m a customer", re.I)
TRAINING_WINDOW_LINES = int(os.environ.get("TRAINING_WINDOW_LINES", "40"))


def _reject_training_artifacts(markdown: str, transcript: str) -> str:
    """Drop claims whose quoted product name occurs ONLY inside a training
    exercise. If the same name also appears elsewhere in the day it survives -
    a real request does not become unreal because someone practised nearby."""
    if not transcript:
        return markdown
    lines = transcript.splitlines()
    training = [False] * len(lines)
    for i, ln in enumerate(lines):
        if _TRAINING_RE.search(ln):
            for j in range(i, min(len(lines), i + TRAINING_WINDOW_LINES + 1)):
                training[j] = True
    if not any(training):
        return markdown
    lowered = [l.lower() for l in lines]

    def reject(text, is_bullet):
        for q in re.findall(r'"([^"]{3,})"', text):
            hits = [i for i, l in enumerate(lowered) if q.lower() in l]
            if hits and all(training[i] for i in hits):
                return f'"{q[:40]}" only occurs inside register training'
        return None

    return _edit_claims(markdown, reject)


_STOPWORDS = frozenset("""a an the of for to in on at with and or per is was be from by
that this it as but during when never write say said not no any all its their his her
you your we our they them there here into onto about over under than then so if""".split())

# Vocabulary every log is made of. A bigram built only from these carries no
# identity, so it can never be evidence that the model copied an example -
# blocking on "customer asked" or "register shortfall" would delete real
# findings on every future day. Only a pair where BOTH words are outside this
# set is distinctive enough to accuse ("xl gloves", "safe pregnancy").
_COMMON_LOG_WORDS = frozenset("""customer customers staff shop store day today morning
afternoon evening tea teas product products item items order orders sale sales sold
register drawer till price prices priced amount amounts total totals discrepancy
shortfall short over unaccounted reconcile request requested requests ask asked asking
reorder restock stock stocked unavailable available promise promised sample samples
blend blends log note noted flag flagged report reported name names steady flow stream
busy slow quiet action taken note bullet unresolved remembering worth demand unmet
inquired browsed came entered visit visits traffic""".split())


def _content_bigrams(text: str) -> set:
    """Adjacent content-word pairs, lowercased. Comparing these rather than
    whole strings is what lets the backstop below catch a re-worded echo:
    the instructions say "restock XL gloves", the log said "Reorder XL gloves",
    and only the bigram "xl gloves" survives both."""
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9'\-]*", text.lower())
             if w not in _STOPWORDS]
    out = set()
    for a, b in zip(words, words[1:]):
        pair = f"{a} {b}"
        if len(pair) >= 7 and (len(a) >= 4 or len(b) >= 4):
            out.add(pair)
    return out


# A quote introduced by one of these is an example of what NOT to write. Those
# must never seed the blocklist: "a steady flow of customers" and "a customer
# asked about X but no action was taken" are quoted precisely because they are
# banned, and blocking their wording would delete real sentences every day.
# Newlines are allowed between cue and quote: these prompts are wrapped prose,
# so "never" routinely sits on the line above the example it forbids.
# Over-matching here is the safe direction - it shrinks a backstop, whereas
# under-matching deletes real findings from every future log.
_NEGATIVE_CUE_RE = re.compile(
    r"\b(never|not|no|banned|avoid|cut|drop|rather than|instead of|"
    r"wrong|bad|forbidden)\b[^.]{0,90}$", re.I)


def _prompt_example_phrases() -> set:
    """Quoted POSITIVE examples in the prompts, which the model treats as
    material rather than instruction."""
    phrases = set()
    for src in (SYSTEM_PROMPT, REDACT_TEMPLATE, CORRELATE_SYSTEM, NOTES_SYSTEM,
                USER_TEMPLATE, FINAL_FROM_NOTES):
        for m in re.finditer(r'"([^"\n]{8,80})"', src):
            s = " ".join(m.group(1).split())
            # Quotes pair off left to right, so on a line with several the
            # regex also captures the prose BETWEEN them ("; write", "a
            # customer"). Real examples are phrases: three or more words,
            # opening on a letter.
            if not s[:1].isalpha() or len(s.split()) < 3:
                continue
            if s.startswith("#") or "⟦" in s or "<" in s or "[" in s:
                continue                      # format markers and filled slots
            if _NEGATIVE_CUE_RE.search(src[max(0, m.start() - 130):m.start()]):
                continue                      # an example of what not to write
            phrases.add(s)
    return phrases


def _prompt_echo_blocklist(grounding: str) -> dict:
    """{distinctive bigram -> the prompt example it came from}, minus anything
    the day actually discussed. Empty is the healthy state: it means no prompt
    carries a concrete example the model could mistake for a shop event."""
    ground = _content_bigrams(grounding)
    blocked = {}
    for phrase in _prompt_example_phrases():
        for bg in _content_bigrams(phrase) - ground:
            a, b = bg.split(" ", 1)
            if a in _COMMON_LOG_WORDS or b in _COMMON_LOG_WORDS:
                continue                      # generic English, not an identity
            blocked[bg] = phrase
    return blocked


def _reject_prompt_echoes(markdown: str, grounding: str) -> str:
    """Drop claims that echo an EXAMPLE from the instructions rather than the
    day.

    The 8B model treats its own instructions as source material. "Reorder XL
    gloves" shipped on all four days audited; the request exists in exactly one
    Slack message, inside the 07-23 window, and on two of those days it had not
    been made yet. "Teas safe during pregnancy" shipped twice and the string
    appears in none of the four inputs. Both were worked examples in
    SYSTEM_PROMPT and REDACT_TEMPLATE.

    The examples are placeholders now, so this is the backstop that stops a
    future concrete one doing the same thing. Self-limiting by construction: a
    phrase the shop genuinely discussed today is in `grounding`, so its bigrams
    are never blocked.
    """
    if not grounding:
        return markdown
    blocked = _prompt_echo_blocklist(grounding)
    if not blocked:
        return markdown
    _warn(f"prompt-echo guard armed on {len(blocked)} phrase(s)")

    def reject(text, is_bullet):
        for bg in _content_bigrams(text):
            if bg in blocked:
                return (f'"{bg}" echoes a prompt example '
                        f'("{blocked[bg][:40]}") absent from today\'s input')
        return None

    return _edit_claims(markdown, reject)


# The prompt bans opening on generic traffic and the model does it anyway -
# 2026-07-26 opened "A steady flow of customers entered the shop", which is the
# exact shape named as banned. Such a sentence is identical every day and
# answers no future question, so cutting it costs nothing.
_GENERIC_OPENER_RE = re.compile(
    r"^(?:a|the)\s+(?:steady|slow|busy|brisk|constant|light)\s+"
    r"(?:flow|stream|trickle|pace|day|morning)\b|"
    r"^the\s+day\s+(?:started|began|opened|was)\b|"
    r"^(?:traffic|the\s+shop|the\s+store|business)\s+"
    r"(?:started|was|felt|remained|stayed|experienced|saw|had)\b|"
    r"^the\s+(?:shop|store|day)\s+(?:experienced|saw|had)\b|"
    r"^customers?\s+(?:inquired|browsed|came|came\s+in|trickled)\b|"
    r"^it\s+was\s+a\s+(?:busy|slow|steady|quiet)\b", re.I)


def _fix_generic_opener(markdown: str, biz: dict | None = None) -> str:
    """Cut a banned opener when the paragraph has something better behind it.

    Two shapes get cut. The first is generic traffic, which the prompt names as
    forbidden and the model writes anyway. The second only became visible once
    the first was fixed: cut the generic sentence and the model's next move is
    to open on a sale - "The day saw several high-value purchases, including a
    $55.86 order featuring Jasmine Pearls". Every one of those numbers is in a
    database forever and the stats block below already carries it; the prompt's
    own reason for existing is that the narrative must spend itself on what is
    stored NOWHERE else. An opener whose amount is just a register total is the
    same non-information as "a steady flow of customers", one layer down.

    Never empties the paragraph - if the banned sentence is all there is, it
    stays, because an empty first paragraph is worse.
    """
    totals = set()
    for o in ((biz or {}).get("sales") or {}).get("orders") or []:
        t = str(o.get("total") or "").replace(",", "").strip().lstrip("$")
        if t:
            totals.add(t)

    def banned(sentence: str) -> str | None:
        s = sentence.strip()
        if _GENERIC_OPENER_RE.search(s):
            return "generic traffic"
        for a in _MONEY_RE.findall(s):
            if a.replace(",", "") in totals:
                return f"restates a register total (${a})"
        return None

    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith(("-", "*")):
            break                              # no narrative paragraph at all
        sentences = _split_sentences(s)
        while len(sentences) >= 2 and banned(sentences[0]):
            _warn(f"dropped opener ({banned(sentences[0])}): {sentences[0][:70]}")
            sentences.pop(0)
        lines[i] = " ".join(sentences)
        break
    return "\n".join(lines)


# A work log pushed to GitHub must never carry a credential, however
# incidentally. The 2026-07-19 audio contains the shop's spoken password
# practice - said out loud during register training - and a regenerated log
# turned it into "Address the issue with the store's audio system and password
# access". That bullet disclosed nothing on its own, which is exactly why it
# would survive review: the rule has to be categorical.
_CREDENTIAL_RE = re.compile(
    r"\bpass ?words?\b|\bpass ?codes?\b|\bpassphrase|\bpin (?:number|code)\b|"
    r"\bcredential|\blogin details\b|\bapi keys?\b|\baccess codes?\b", re.I)


def _drop_credentials(markdown: str) -> str:
    """Remove any claim that touches credentials at all."""
    def reject(text, is_bullet):
        return "mentions credentials" if _CREDENTIAL_RE.search(text) else None

    return _edit_claims(markdown, reject)


# The policy bans verbatim quotes outright, and the tell that one got through is
# usually profanity: 2026-07-23 regenerated with "asking if it was fucking
# receipt". Stripping the quotation marks is not enough - the sentence is still
# somebody's overheard words in a permanent record.
_PROFANITY_RE = re.compile(
    r"\b(?:fuck\w*|shit\w*|bitch\w*|cunt\w*|assholes?|bastards?|dick ?head)\b", re.I)


def _drop_overheard_quotes(markdown: str) -> str:
    def reject(text, is_bullet):
        return "verbatim overheard speech" if _PROFANITY_RE.search(text) else None

    return _edit_claims(markdown, reject)


def _drop_record_name_lists(markdown: str, biz: dict) -> str:
    """Drop claims that recite customer names straight out of the records.

    2026-07-23 regenerated with "The afternoon brought several pickup
    notifications sent to [six first names]" - every one lifted from the texts
    table. It is a privacy problem and a restatement at the same time: those
    names are in the records forever, and naming a customer is only ever
    justified when it makes a commitment actionable. One name on a promise is
    the point of the log; a list of six is the records with the useful part
    removed.
    """
    names = set()
    for m in re.finditer(r'"Hi ([A-Z][a-z]{2,})[,!]', _context_block(biz) or ""):
        names.add(m.group(1))
    if len(names) < 2:
        return markdown

    def reject(text, is_bullet):
        hit = [n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text)]
        if len(hit) >= 2:
            return f"recites {len(hit)} customer names from the records"
        return None

    return _edit_claims(markdown, reject)


# Placeholder language: the bullet's own subject is a blank. "a product",
# "the exact item is unclear", "expressed dissatisfaction with a product".
# These are the shapes the policy means by "a VAGUE line answers no future
# question at any length - cut it".
_PLACEHOLDER_RE = re.compile(
    # NOT "tea" or "blend": in this shop those are the product, not a blank.
    # "Chase the unpaid invoice from the tea supplier" was being scored as
    # placeholder language because "the tea" matched.
    r"\b(?:a|an|the|some|another) (?:product|item|thing)\b|"
    r"exact (?:item|product|name)[^.]{0,25}(?:unclear|unspecified|not specified)|"
    r"did ?n[o']?t specify|was ?n[o']?t specified|not specified\b|"
    r"asked for clarification|expressed (?:dis)?satisfaction with", re.I)


def _drop_vague_bullets(markdown: str, products: list) -> str:
    """Drop bullets whose subject is a placeholder.

    Tests for the presence of blank language, NOT for the absence of
    specificity. The first version of this gate did the latter - no digit, no
    catalog match, no capitalised word - and on a live run it deleted "A
    customer asked about saffron, which we don't carry", "Staff flagged that
    the label printer wasn't working correctly", and the exposed-wiring
    incident. Every one of those is exactly what the log exists for, and every
    one fails an absence test for the same reason: equipment, supplies and
    products we DON'T stock are ordinary lowercase nouns with no catalog entry.
    An unstocked product having no catalog match is the definition of unmet
    demand, so keying on it inverted the gate's purpose.

    A bullet that names a real product or carries a number is kept regardless -
    "Feedback on Immune Support: a comment about its texture" is thin, but it
    is thin about something real, and deleting real product feedback is the
    more expensive mistake.

    Bullets only. Connective tissue is legitimate in a narrative paragraph; an
    action item or a durable observation has to stand on its own.
    """
    known = [k for k in ({_norm_name(str(p.get("name") or "")) for p in (products or [])}
                         - {""}) if len(k) >= 6]

    def reject(text, is_bullet):
        if not is_bullet or not _PLACEHOLDER_RE.search(text):
            return None
        body = re.sub(r"^\s*[-*]\s*", "", text)
        if re.search(r"\d", body):
            return None                       # a number is a fact
        nbody = _norm_name(body)
        if any(k in nbody for k in known):
            return None                       # thin, but about something real
        return "its subject is a placeholder"

    return _edit_claims(markdown, reject)


# Health talk is only loggable when it is a SHOPPING question - "a customer
# asked about anti-inflammatory blends" is the shop's business; somebody's
# dental anxiety is not. The redaction pass is supposed to drop the latter and
# on 2026-07-26 it shipped "another customer expressed concern about dental
# issues and tooth anxiety" instead.
_HEALTH_RE = re.compile(
    r"\b(?:anxiet\w+|depress\w+|diagnos\w+|dental|teeth|tooth|migraine|"
    r"pregnan\w+|medication|prescription|surgery|cancer|diabet\w+|arthrit\w+|"
    r"symptoms?|illness|sick|injur\w+|therapy|disorder)\b", re.I)
# ...unless the same claim is anchored to something the shop sells or does.
_SHOP_CONTEXT_RE = re.compile(
    r"\b(?:tea|teas|blend|blends|tisane|herb|herbal|rooibos|matcha|infusion|"
    r"sample|samples|recommend\w*|asked for|requested|bought|ordered|stock\w*|"
    r"caffeine|decaf)\b", re.I)


# "No contact info ever" is in the policy and was never enforced in code. The
# notes pass for 2026-07-26's records produced "[PROMISE] Ticket #3259 from Joy
# Mack and #3267 from sidhe7@juno.com pending accuracy check" - which the
# carry-through would have inserted into Unresolved verbatim, publishing a
# customer's email address to a GitHub branch. Found by probing the notes
# rather than by reading a log, because no log had shown it yet.
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)")


def _scrub_contact_info(markdown: str) -> str:
    """Strip emails and phone numbers wherever they appear.

    Strips rather than drops: "ticket #3259 is pending an accuracy check" is a
    real open item, and the address is the only part that must not survive.
    A claim left with nothing but connectives afterwards is dropped by the
    vagueness pass downstream.
    """
    def scrub(m):
        _warn(f"scrubbed contact info: {m.group(0)[:6]}…")
        return "[contact removed]"

    markdown = _EMAIL_RE.sub(scrub, markdown)
    return _PHONE_RE.sub(scrub, markdown)


def _drop_personal_health(markdown: str) -> str:
    """Drop health talk that is not a shopping question.

    Deliberately keyed on the ABSENCE of shop context rather than on the health
    word alone: this shop sells wellness blends, so "a customer asked about
    anti-inflammatory blends" and "wanted something caffeine-free for
    migraines" are its actual trade and must survive. What must not survive is
    a person's condition recorded for its own sake - it has no operational
    value, and in a shop this size a specific complaint identifies someone even
    with no name on it.
    """
    def reject(text, is_bullet):
        if _HEALTH_RE.search(text) and not _SHOP_CONTEXT_RE.search(text):
            return "personal health content with no shop context"
        return None

    return _edit_claims(markdown, reject)


def _mark_empty_sections(markdown: str) -> str:
    """A heading with nothing under it reads as a broken render, not as "there
    was nothing". Empty beats padded, but say so."""
    lines = markdown.splitlines()
    out = []
    for i, line in enumerate(lines):
        out.append(line)
        if not line.strip().startswith("##"):
            continue
        rest = lines[i + 1:]
        nxt = next((l for l in rest if l.strip()), "")
        if not nxt or nxt.strip().startswith("#"):
            out.append("")
            out.append("_None._")
    return "\n".join(out)


def _shares_substantive_word(qn: str, n: str) -> bool:
    """A rename needs a substantive word in common, not just a good
    character-level score.

    Without this the matcher renamed the phrase "so cool" into "Ray's Cooler |
    Organic" and shipped it as customer praise for a product nobody mentioned -
    the same noise-promoted-to-product failure the garble gate exists to stop,
    arriving through the correction path instead. Both correction tiers call
    this: guarding one of two paths into the same edit is not guarding it.
    """
    import difflib
    a = {w for w in qn.split() if len(w) >= 5}
    b = {w for w in n.split() if len(w) >= 5}
    if a & b:
        return True
    # Allow a near-miss on a long word ("stainless" vs "stainles").
    return any(difflib.SequenceMatcher(None, x, y).ratio() >= 0.85
               for x in a for y in b)


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
        top = scored[0][0] if scored else 0.0
        second = scored[1][0] if len(scored) > 1 else 0.0
        confident = top >= 0.93 or (top >= 0.72 and top - second >= 0.10)
        if confident and not _shares_substantive_word(qn, scored[0][1]):
            _warn(f'catalog: refused to rename "{q[:40]}" -> '
                  f'"{norm_to_name[scored[0][1]]}" (no substantive word in common)')
            confident = False
        if confident:
            auto_fixes.append((q, norm_to_name[scored[0][1]]))
        else:
            review.append((q, [(norm_to_name[n], round(s, 2)) for s, n in scored if s >= 0.55]))
    return auto_fixes, review


def _annotate_catalog(draft: str, review) -> tuple[str, int]:
    """Deterministic follow-through for the review tier: the 8B model ignored
    'replace X with Y' instructions across repeated runs, so code swaps the
    misheard name for the real catalog name directly - the log just reads
    "Monk's Blend", no correction markup. Done in code, no LLM roll can drop
    it."""
    n = 0
    for q, cands in review:
        # len >= 5 keeps quoted ordinary words ("trap") from being treated as
        # product names; 0.65 is calibrated so "Munch's Blend"->Monk's (0.66)
        # corrects but supplier-name coincidences like "Star West"->Star
        # Anise (0.63) don't.
        if _is_garble(q):
            _warn(f"catalog: refused to rename garble \"{q[:50]}\"")
            continue
        # Same substantive-word requirement as the auto-fix tier. Without it
        # this path re-applies exactly what _match_catalog just refused: on
        # 2026-07-26 "so cool" was pushed to review by the guard there and
        # then renamed to "Ray's Cooler | Organic" here, shipping as praise
        # for a product nobody mentioned. A guard on one of two paths into the
        # same edit is not a guard.
        if cands and not _shares_substantive_word(_norm_name(q),
                                                  _norm_name(cands[0][0])):
            _warn(f"catalog: refused to rename \"{q[:40]}\" -> "
                  f"\"{cands[0][0]}\" (no substantive word in common)")
            continue
        if len(q) >= 5 and cands and cands[0][1] >= 0.65:
            _warn(f"catalog correction: \"{q}\" -> \"{cands[0][0]}\"")
            draft = draft.replace(q, cands[0][0])
            n += 1
    return draft, n


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

# Lines about money handled at the counter - these can only concern an order
# rung up during store hours, never an overnight online one.
_REGISTERISH = re.compile(r"register|drawer|till|cash|shortfall|discrepan", re.I)
STORE_OPEN_HOUR = int(os.environ.get("TRANSCRIBE_START_HOUR", "8"))
STORE_CLOSE_HOUR = int(os.environ.get("TRANSCRIBE_END_HOUR", "20"))


_UNAVAILABLE_RE = re.compile(
    r"reorder|out of stock|sold out|unavailable|not available|wasn'?t available|"
    r"didn'?t have|don'?t carry|not carried|ran out|restock|"
    # 2026-07-26 asserted "but neither were available" of two products the
    # register shows sold, and slipped through: the phrasing carries no "not".
    # The copula is optional: 2026-07-26 regenerated with "neither available"
    # and slipped past a pattern that insisted on "neither WERE available".
    r"(?:neither|none|nothing|not one)\s+(?:of\s+\w+\s+)?(?:were\s+|was\s+)?available|"
    r"weren'?t available|were not available|not in stock|no longer (?:carry|stocked)|"
    r"could ?n[o']?t find|we(?:'re| are|'ve been| have been) out",
    re.IGNORECASE)

# The prompt bans non-events outright ("a customer asked about X but no action
# was taken - cut") and the model writes them anyway; 2026-07-21 shipped "The
# customer also requested a refund for a $100 purchase but no action was taken"
# off a training aside where no refund was ever requested. A sentence whose own
# payload is that nothing happened cannot be worth a permanent record.
_NON_EVENT_RE = re.compile(
    r"\bno (?:specific |further |additional )?(?:feedback|details?|content|"
    r"information|notes?|issues?) (?:noted|provided|recorded|available|given)\b|"
    r"\bnone (?:noted|provided|recorded|reported)\b|"
    r"but (?:no|nothing) (?:action|further action|steps?|was) [\w ]*?"
    r"(?:taken|done|required|needed)|no action was taken|nothing was done|"
    r"but no action|but nothing came of|but (?:it|this) was not (?:pursued|acted)",
    re.IGNORECASE)


def _unquote_unverified(markdown: str, products: list) -> str:
    """Strip the quotation marks from any product name that is not in the
    catalog.

    The policy says it outright - "never put an unverified phrase in quotation
    marks; a quoted string is treated downstream as a real product name" - and
    the model does it constantly with short mis-hears that `_is_garble` is
    deliberately too conservative to catch: "Cork option paper", "boochoo",
    "royalts", "Fauna", "the dealership". Deleting those claims would throw
    away real events (a customer really did ask for something). Removing the
    quotes keeps the event, drops the false authority, and leaves a reader in
    no doubt that the wording came off a lossy mic.

    Runs AFTER the catalog stage, so anything it could correct or annotate
    already has been; what is left is genuinely unverified.
    """
    if not products:
        return markdown
    known = {_norm_name(str(p.get("name") or "")) for p in products}
    known |= {n.rsplit(" ", 1)[0] for n in known if " " in n}
    known.discard("")

    def repl(m):
        q = m.group(1)
        if _norm_name(q) in known:
            return m.group(0)
        _warn(f'unquoted unverified name: "{q[:50]}"')
        return q

    return re.sub(r'"([^"\n]{3,80})"', repl, markdown)


def _drop_non_events(markdown: str) -> str:
    """Remove claims that report that nothing happened."""
    def reject(text, is_bullet):
        return "reports a non-event" if _NON_EVENT_RE.search(text) else None

    return _edit_claims(markdown, reject)


_REORDER_RE = re.compile(
    r"reorder|re-order|restock|re-stock|out of stock|unmet demand|confirm availability", re.I)

# Register line items that are not products and so prove nothing about stock.
_GENERIC_SKUS = frozenset({"gift card", "gift cards", "gift certificate",
                           "general item", "custom item", "shipping", "postage"})
# How near the unavailability claim the sold product has to sit, in characters.
_SOLD_NEAR_CHARS = 60

# --- claim-level editing ------------------------------------------------------
# Every gate below used to inspect ONLY lines starting with "-", which left the
# narrative paragraphs completely ungated. On 2026-07-26 the discrepancy gate
# correctly dropped "- Reconcile the register shortfall of $23.50" and the
# identical invented figure shipped in paragraph one anyway - roughly half the
# log's surface area was unprotected. A CLAIM is now a bullet line OR one
# sentence of a narrative paragraph, and every gate runs over both.

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
# Parenthetical record references carry ids and amounts that came from the
# RECORDS block, not from speech. They must never be fed to the money tests -
# a correctly annotated real discrepancy would fail its own gate.
_ANNOTATION_RE = re.compile(r"\([^)]*#\d[^)]*\)")


def _split_sentences(text: str) -> list[str]:
    """Sentence split that survives money and ids: a boundary needs whitespace
    after the stop, so "$23.50" and "#T-96892" stay intact."""
    return [s for s in _SENTENCE_SPLIT.split(text.strip()) if s]


def _edit_claims(markdown: str, reject) -> str:
    """Drop every claim `reject(text, is_bullet) -> reason|None` rejects.

    Bullets go whole. A rejected narrative sentence is removed from its
    paragraph and the paragraph survives on its remaining sentences; a
    paragraph left with nothing is dropped. Headings and blank lines pass
    through untouched so the document structure is preserved.
    """
    out, para = [], []

    def flush():
        if not para:
            return
        joined = " ".join(l.strip() for l in para)
        del para[:]
        kept = []
        for s in _split_sentences(joined):
            why = reject(s, False)
            if why:
                _warn(f"dropped narrative sentence - {why}: {s[:70]}")
            else:
                kept.append(s)
        if kept:
            out.append(" ".join(kept))

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            flush()
            out.append(line)
        elif stripped.startswith(("-", "*")):
            flush()
            why = reject(line, True)
            if why:
                _warn(f"dropped bullet - {why}: {stripped[:70]}")
            else:
                out.append(line)
        else:
            para.append(line)
    flush()
    return "\n".join(out)


# A claim that says money went missing...
_DISCREPANCY_CLAIM = re.compile(
    r"discrepan|shortfall|short(?:fall)? of|came up short|over-?ring|unaccounted|"
    r"does ?n[o']?t match|reconcile the", re.I)
# ...must correspond to somebody on the floor actually SAYING so. Deliberately
# specific phrases: a bare "short" ("short on time", "short walk") is not a
# till problem, and a false positive here re-opens the hole this closes.
_DISCREPANCY_SPEECH = re.compile(
    r"discrepan|shortfall|came up short|we'?re short|is short|short by|"
    r"does ?n[o']?t match|does not match|did ?n[o']?t balance|out of balance|"
    r"off by|unaccounted|over by|missing money|drawer is off|"
    # A drawer that is OVER is a discrepancy too, and staff say it in plain
    # words rather than in till vocabulary. 2026-07-19's real event - the
    # drawer $70 up, with a customer possibly charged twice - reads "we're
    # over today we charged people $70 more ... the drawer is $70 more than
    # this". None of the patterns above touch any of that.
    r"we'?re over|we were over|came up over|drawer is \$?[\d,.]+ more|"
    r"charged (?:people|someone|her|him|them|the customer)(?: an extra| more)|"
    r"charged (?:someone |them |her |him )?twice|double.?charg", re.I)


# The comma group must be a real thousands separator. "[\d,]+" swallowed the
# comma in "$70, possibly from an order", capturing "70," - which then made the
# gate search the transcript for "70," and never find it, so a genuine spoken
# amount read as unsupported.
_MONEY_RE = re.compile(r"\$ ?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)")
# How close a spoken amount has to sit to somebody reporting money wrong.
DISCREPANCY_NEAR_LINES = int(os.environ.get("DISCREPANCY_NEAR_LINES", "12"))


def _spoken_near_report(amount: str, lines: list[str], flags: list[bool]) -> bool:
    """True when SOME spoken instance of `amount` sits within
    DISCREPANCY_NEAR_LINES of somebody saying money is wrong.

    "Was this amount spoken aloud?" is not a usable test on its own: in a shop
    EVERY sale total is read out loud, so the check is satisfied by definition
    and the gate is close to a no-op. Both invented shortfalls found in the
    four-day audit - $37.66 on 07-19, $6.46 on 07-23 - were POS order totals
    read to a customer hours away from any till talk. Proximity is what
    separates a counter readout from a drawer that did not balance.
    """
    bare = amount.replace(",", "")
    variants = {bare}
    if "." not in bare:
        variants.add(bare + ".00")
    elif bare.endswith(".00"):
        variants.add(bare[:-3])
    pat = re.compile(r"(?<![\d.])(?:" +
                     "|".join(re.escape(v) for v in sorted(variants)) + r")(?![\d])")
    # A whole-dollar figure is weak evidence - "47" is a time, a quantity, a
    # street number and half a price. 2026-07-21 regenerated with "an
    # unresolved discrepancy of $47 in the register" and cleared the window
    # test on an unrelated 47 nearby. Cents are specific enough to trust at a
    # distance; a bare number has to be on the very line where somebody says
    # money is wrong.
    # Cents are specific enough to trust at a distance; a bare figure gets a
    # tight window rather than none. Zero was too strict: on 2026-07-19 "off by
    # seven" sits three lines above "the drawer is $70 more than this", and
    # requiring them on ONE line deleted the day's real discrepancy.
    window = DISCREPANCY_NEAR_LINES if "." in bare else 3
    for i, ln in enumerate(lines):
        if pat.search(ln):
            lo, hi = max(0, i - window), i + window + 1
            if any(flags[lo:hi]):
                return True
    return False


_OVER_SPEECH = re.compile(
    r"we'?re over|we were over|came up over|over by|"
    r"drawer is \$?[\d,.]+ more|charged (?:people|someone|her|him|them|the customer)"
    r"(?: an extra| more)|charged (?:someone |them |her |him )?twice|double.?charg", re.I)
_SHORT_SPEECH = re.compile(
    r"came up short|we'?re short|is short|short by|shortfall|missing money", re.I)
_SHORT_WORDING = re.compile(r"\bshort(?:fall)?\b|\bcame up short\b|\bmissing\b", re.I)


def _fix_discrepancy_direction(markdown: str, transcript: str) -> str:
    """Say which way the drawer was wrong.

    2026-07-19's drawer was $70 OVER, with a card sale that looked declined
    possibly charging a customer twice. The log called it "an unexplained
    register shortfall of $70" - the opposite, and not a wording quibble:
    short means money is missing, over means somebody was overcharged, and
    tomorrow's investigation starts in a different place.

    Direction comes from the speech near the amount, never from the model.
    """
    if not transcript:
        return markdown
    lines = transcript.splitlines()
    over = [bool(_OVER_SPEECH.search(l)) for l in lines]
    short = [bool(_SHORT_SPEECH.search(l)) for l in lines]

    def direction(amount: str) -> str | None:
        bare = amount.replace(",", "")
        pat = re.compile(r"(?<![\d.])" + re.escape(bare) + r"(?![\d])")
        near_over = near_short = False
        for i, ln in enumerate(lines):
            if not pat.search(ln):
                continue
            lo, hi = max(0, i - DISCREPANCY_NEAR_LINES), i + DISCREPANCY_NEAR_LINES + 1
            near_over = near_over or any(over[lo:hi])
            near_short = near_short or any(short[lo:hi])
        if near_over and not near_short:
            return "over"
        return None

    out = []
    for line in markdown.splitlines():
        if _DISCREPANCY_CLAIM.search(line) and _SHORT_WORDING.search(line):
            amounts = _MONEY_RE.findall(_ANNOTATION_RE.sub("", line))
            if any(direction(a) == "over" for a in amounts):
                fixed = re.sub(r"\bshortfall\b", "overage", line, flags=re.I)
                fixed = re.sub(r"\bcame up short\b", "came up over", fixed, flags=re.I)
                fixed = re.sub(r"\bshort\b", "over", fixed, flags=re.I)
                if fixed != line:
                    _warn(f"discrepancy direction corrected to OVER: {fixed.strip()[:60]}")
                line = fixed
        out.append(line)
    return "\n".join(out)


def _drop_stray_sections(markdown: str) -> str:
    """The format has exactly two bullet sections. The model invents others -
    2026-07-19 emitted a third, "## Annotated", duplicating a bullet already in
    Unresolved. An unknown heading and everything under it is dropped."""
    keep = ("## unresolved", "## worth remembering")
    out, skipping = [], False
    for line in markdown.splitlines():
        if line.startswith("## "):
            skipping = line.strip().lower() not in keep
            if skipping:
                _warn(f"dropped stray section: {line.strip()[:40]}")
        if not skipping:
            out.append(line)
    return "\n".join(out)


def _reject_unsupported_discrepancies(markdown: str, transcript: str) -> str:
    """A register discrepancy has to be one someone noticed out loud.

    The summarizer invents these from ordinary money talk: "62.15 today" (a
    cashier reading a total) became "confirm the $62.15 register discrepancy",
    and a $50.31 sale became "reconcile the register shortfall of $50.31".
    Rewording the prompt did not stop it - the model is pattern-matching the
    Unresolved format, not reading the day. So the claim is checked against the
    day's speech: no one reporting money wrong means there is no discrepancy,
    whatever amounts were said aloud.

    Runs over narrative sentences as well as bullets. That is not theoretical:
    2026-07-26 shipped "A discrepancy in register: $23.50 unaccounted for" in
    its first paragraph on a day where the string 23.50 appears nowhere in the
    input, while the bullet carrying the same claim was correctly dropped.
    """
    if not transcript:
        return markdown
    lines = transcript.splitlines()
    flags = [bool(_DISCREPANCY_SPEECH.search(l)) for l in lines]
    reported = any(flags)

    def reject(text, is_bullet):
        if not _DISCREPANCY_CLAIM.search(text):
            return None
        if not reported:
            return "no one reported money wrong today"
        amounts = _MONEY_RE.findall(_ANNOTATION_RE.sub("", text))
        if not amounts:
            # "A register shortfall was noted during the shift, though no
            # specific cause was identified" - passes the day-level test on a
            # day that did have a real till problem, names no figure, and is
            # unactionable. Tomorrow cannot start from it.
            return "a discrepancy with no amount is not actionable"
        for a in amounts:
            if not _spoken_near_report(a, lines, flags):
                return f"${a} was never said near anyone reporting money wrong"
        return None

    return _edit_claims(markdown, reject)


def _reject_sold_reorders(markdown: str, biz: dict) -> str:
    """A sale is proof of stock - the opposite of unmet demand.

    SYSTEM_PROMPT states this outright ("a sale is proof of stock ... 'Reorder
    X' is valid ONLY when speech says X was asked for and unavailable") and the
    model does it anyway: it asked to reorder Lady Londonderry on a day a
    customer bought 8oz of it, and to reorder cactus nectar off the line "we
    have cactus nectar, which is super delicious". Checked against the register,
    not the model's judgment.

    Deliberately NOT extended to catalog stock: "floor says out, website says in
    stock" is a real finding the log is supposed to surface (see
    _flag_stock_contradictions). Only an actual sale settles it.

    Covers narrative sentences and unavailability claims, not just reorder
    bullets. 2026-07-26 asserted in prose that a Nilgiri iced tea blend and a
    Sandia Spice / immune-support pair were unavailable; the woven POS lines
    proving all three sold were in the model's own input.
    """
    sold = set()
    for o in ((biz.get("sales") or {}).get("orders") or []):
        for it in (o.get("items") or []):
            n = _norm_name(str(it.get("name") or ""))
            if len(n) >= 6 and n not in _GENERIC_SKUS:
                sold.add(n)
    if not sold:
        return markdown

    def reject(text, is_bullet):
        nline = _norm_name(text)
        for m in re.finditer(_REORDER_RE.pattern + "|" + _UNAVAILABLE_RE.pattern,
                             nline, re.I):
            # The sold product has to be what the claim is ABOUT. Without this
            # window any sentence that merely contains the word "restocking"
            # somewhere loses to an unrelated SKU elsewhere in it - a real
            # finding about gift-card processing was deleted because "Gift
            # Card" is also a line item on the day's register.
            lo, hi = max(0, m.start() - _SOLD_NEAR_CHARS), m.end() + _SOLD_NEAR_CHARS
            window = nline[lo:hi]
            hit = next((s for s in sold
                        if re.search(r"\b" + re.escape(s) + r"\b", window)), None)
            if hit:
                return f'"{hit}" sold today, so it was in stock'
        return None

    return _edit_claims(markdown, reject)


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


LINKAGE_SCAN_SYSTEM = """You check a tea shop's daily log for privacy LINKAGE \
violations: a personal name tied IN THE SAME SENTENCE to health/medical topics \
(pregnancy, allergies, cleansing, detox interests, conditions), personal-life \
circumstances, or attributed feelings/opinions. A name on a purely operational \
fact (a promise, hold, special order, sample request, staff hours) is FINE and \
must NOT be listed.

Output one line per violation, formatted exactly:
NAME | the sentence fragment that pairs the name with sensitive content
If there are none, output exactly: NONE
Output nothing else - no commentary, no markdown. /no_think"""


def _strip_linkage_names(markdown: str, findings: list[str]) -> str:
    """Deterministic surgery for scanned linkage violations: drop the NAME from
    the sentences that pair it with sensitive content, keeping the fact. The
    LLM only detects (which it does reliably); code does the editing (which the
    LLM repeatedly failed to apply itself)."""
    for raw in findings:
        if "|" not in raw:
            continue
        name, fragment = (s.strip().strip('"') for s in raw.split("|", 1))
        if not name or len(name) > 40 or not name[0].isupper():
            continue
        pat_poss = re.compile(r"\b" + re.escape(name) + r"(?:'s|’s)\b")
        pat = re.compile(r"\b" + re.escape(name) + r"\b")
        # Scope the strip to the flagged sentence: an operational mention of
        # the same name elsewhere ("asked for a hold on two tins") keeps its
        # name per policy. Loose fragment match (first words, normalized);
        # if the fragment can't be located, strip everywhere - privacy wins
        # over precision when in doubt.
        frag_key = _norm_name(" ".join(fragment.split()[:5]))
        lines = markdown.splitlines()
        hits = [i for i, l in enumerate(lines)
                if pat.search(l) and frag_key and frag_key in _norm_name(l)]
        targets = set(hits) if hits else {i for i, l in enumerate(lines) if pat.search(l)}
        for i in targets:
            line = lines[i]
            if line.startswith(("#", "**")):
                continue
            new = pat_poss.sub("a customer's", line)
            new = pat.sub("a customer", new)
            new = re.sub(r"(^|[.!?]\s+|^- )a customer", lambda m: m.group(1) + "A customer", new)
            if new != line:
                _warn(f"linkage break: removed name '{name}' from a sentence")
            lines[i] = new
        markdown = "\n".join(lines)
    return markdown


# Staff names are KNOWN (timeclock roster + Slack display names), so keeping a
# staff name off something they said needs no model judgment at all. Same
# detect-then-surgery split as the linkage guard above - except here we don't
# need the detect half, which is why this one is reliable.
_SAID_VERBS = (
    "said|says|asked|asks|noted|notes|mentioned|mentions|requested|requests|"
    "wants|wanted|suggested|suggests|flagged|flags|reported|reports|"
    "complained|complains|observed|observes|confirmed|confirms|explained|"
    "explains|raised|raises|thinks|thought|believes|needs|needed"
)
_SAID_NOUNS = (
    "request|suggestion|note|comment|observation|complaint|idea|report|"
    "concern|feedback|question|reminder"
)


def _staff_names(biz: dict, slack_names: set | None) -> set:
    """The day's roster: timeclock employees + Slack display names, plus bare
    first names (which is what the log actually tends to write)."""
    names = set(slack_names or ())
    for sft in (biz.get("timeclock") or {}).get("shifts") or []:
        if sft.get("employee"):
            names.add(str(sft["employee"]))
    out = set()
    for n in names:
        n = " ".join(str(n).split())
        if not n or not n[0].isalpha():
            continue
        out.add(n)
        first = n.split()[0]
        # 3+ chars so an initial can't wildcard-match ordinary prose
        if len(first) >= 3:
            out.add(first)
    return out


def _strip_staff_attribution(markdown: str, staff: set) -> str:
    """Never let a staff member's name sit on something they said or asked for.

    Targets attribution specifically - a possessive-of-a-remark, or a name
    followed by a speech verb - rather than every mention, so an operational
    line that merely carries a name is left alone. The appended stats block
    (whose staff names are timeclock data, not speech) is never touched: it is
    added after this runs.
    """
    if not staff:
        return markdown
    ordered = sorted(staff, key=len, reverse=True)  # "George Quintero" before "George"
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(("#", "**")):
            continue
        new = line
        for name in ordered:
            nm = re.escape(name)
            new = re.sub(rf"\bper {nm}(?:'s|’s)\s+({_SAID_NOUNS})\b", r"per a staff \1", new)
            new = re.sub(rf"\b{nm}(?:'s|’s)\s+({_SAID_NOUNS})\b", r"a staff member's \1", new)
            new = re.sub(rf"\b{nm}\s+({_SAID_VERBS})\b", r"a staff member \1", new)
            new = re.sub(rf"\bper {nm}\b", "per a staff member", new)
            # "Reorder XL gloves as requested by George" - the carried Slack
            # notes are full of this shape, and none of the patterns above
            # touch it. Staff chat is the one source where real names are
            # allowed to arrive, so it is also the one that most needs this.
            new = re.sub(
                rf"\b({_SAID_VERBS}|requested|asked)\s+by\s+{nm}\b",
                r"\1 by a staff member", new)
            # A back office named after whoever sits in it is still a staff
            # name in a permanent record.
            new = re.sub(rf"\b{nm}(?:'s|’s|s)?\s+office\b", "a staff office", new)
        # The staff set comes from the timeclock and Slack display names, so a
        # colleague merely MENTIONED in a message body is not in it - "Shawns
        # office" survived every named pattern on 2026-07-23 because Shawn did
        # not work that day. A room named after whoever sits in it is a staff
        # name whether or not we can enumerate them.
        new = re.sub(r"\b[A-Z][a-z]{2,}(?:'s|’s|s)?\s+office\b", "a staff office", new)
        if new != line:
            new = re.sub(r"(^|[.!?]\s+|^- )a staff", lambda m: m.group(1) + "A staff", new)
            _warn("staff attribution: dropped a staff name from a remark")
            lines[i] = new
    return "\n".join(lines)


# The merge is where findings die. On a long day the final call is handed a
# dozen slices of notes and asked for 2-4 paragraphs plus ten bullets, and an 8B
# model simply drops most of it - 2026-07-26's staff chat contained a supply
# order, the phones not reaching the warehouse, a fridge wedged open and a
# safety warning, and NONE of it survived to the log. Giving Slack its own notes
# pass did not fix that, because the loss happens downstream of the notes.
#
# So the operational items stop depending on the merge. The notes pass tags each
# bullet by kind; the model still writes the prose, but anything tagged as a
# problem, an unmet request or a promise is carried into the bullet sections in
# code if the draft failed to mention it. Every carried line then goes through
# the same gates as everything else - nothing skips grounding, garble or privacy
# checks by arriving this way.
# Accept ANY [TAG], not just the six the prompt lists. Given a closed list the
# model extends it: asked for six tags on 2026-07-26's staff chat it also
# emitted [SUPPLY] (2 cases of gift tins ordered), [STORAGE] (matcha newly kept
# in the fridge) and [SAFETY] (heavy stock shelved unsafely high) - the exact
# findings this mechanism exists to rescue, thrown away by a parser that only
# recognised its own vocabulary. An unknown tag is a classification problem,
# never a reason to discard the finding.
_NOTE_TAG_RE = re.compile(r"^\s*[-*]?\s*\[([A-Z][A-Z /&-]{2,20})\]\s*")
_NOTE_TAG_ANY_RE = re.compile(r"\[[A-Z][A-Z /&-]{2,20}\]\s*")
# Tags that mean "somebody has to do something" land in Unresolved; every other
# tag is an observation. Substring match, so SUPPLY-ORDER and STOCK/SUPPLY work.
_ACTIONABLE_TAGS = ("PROBLEM", "PROMISE", "SAFETY", "SUPPLY", "STORAGE", "EQUIPMENT",
                    "STOCK", "ISSUE", "BROKEN", "REORDER", "ACTION", "REPAIR")
# ...except these, which shape the narrative and are not bullets at all.
_NARRATIVE_TAGS = ("CAUSE", "TRAFFIC", "RHYTHM", "WEATHER", "MOOD")
# Where each kind belongs, and which are worth rescuing at all. CAUSE and
# TRAFFIC shape the narrative rather than standing alone as bullets.
CARRY_SECTION_TARGET = int(os.environ.get("CARRY_SECTION_TARGET", "5"))
# Below this a candidate does not ship at all, even into an empty section.
# "Empty beats padded" is the policy's own rule and the ranking cannot honour
# it by ordering alone: with nothing better available, a pickup-notification
# restatement scoring -5 was still taking the last slot.
CLAIM_SCORE_FLOOR = int(os.environ.get("CLAIM_SCORE_FLOOR", "0"))


def _carry_section(tag: str) -> str | None:
    """Which bullet list a tagged note belongs in, or None to leave it to the
    narrative. Unknown tags are classified, never dropped."""
    t = tag.upper()
    if any(k in t for k in _NARRATIVE_TAGS):
        return None
    if any(k in t for k in _ACTIONABLE_TAGS):
        return "## Unresolved"
    return "## Worth remembering"


def _parse_tagged_notes(notes: list) -> list:
    """[(tag, text)] for every tagged bullet across all slices, in order."""
    out = []
    for block in notes:
        for line in (block or "").splitlines():
            m = _NOTE_TAG_RE.match(line)
            if not m:
                continue
            text = line[m.end():].strip(" -*").strip()
            if len(text.split()) >= 4:
                out.append((m.group(1).upper(), text))
    return out


def _already_covered(text: str, markdown: str) -> bool:
    """True when the draft already says this. Compared on content bigrams so a
    reworded version still counts - the point is to avoid saying it twice, not
    to demand the model matched our wording."""
    a = _content_bigrams(text)
    if not a:
        return True
    b = _content_bigrams(markdown)
    if len(a & b) / len(a) >= 0.5:
        return True
    # A shared content TRIGRAM is a restatement however differently the rest is
    # worded: "Reorder XL gloves as requested by George" and "A staff member
    # requested to reorder XL gloves as mediums no longer fit" overlap on only
    # 22% of bigrams and shipped as two bullets about one request.
    def trigrams(t):
        w = [x for x in re.findall(r"[a-z0-9][a-z0-9'\-]*", t.lower())
             if x not in _STOPWORDS]
        return {" ".join(w[i:i + 3]) for i in range(len(w) - 2)}

    return bool(trigrams(text) & trigrams(markdown))


def _same_claim(a: str, b: str) -> bool:
    """Two bullets about the same thing, however differently worded."""
    # A shared distinctive figure settles it before any wording comparison.
    # 2026-07-19 shipped "Investigate unexplained register overage of $70" and
    # "The register had an unexplained overage of $70, possibly from an order
    # that didn't go through" as two bullets: word order differs enough that
    # neither the bigram nor the trigram test fired, but a shop does not have
    # two separate $70 drawer problems in one day.
    fa, fb = set(_MONEY_RE.findall(a)), set(_MONEY_RE.findall(b))
    if fa & fb:
        return True
    ia = set(re.findall(r"#\s?([A-Za-z0-9-]+)", a))
    ib = set(re.findall(r"#\s?([A-Za-z0-9-]+)", b))
    if ia & ib:
        return True
    return _already_covered(a, b)


# What the log is FOR, in the form these bullets actually take.
_UNMET_SHAPE_RE = re.compile(
    r"do ?n[o']?t carry|does ?n[o']?t carry|not carried|out of stock|unavailable|"
    r"not available|ran out|we are out|we're out|we only (?:stock|carry|have)|"
    r"only .{0,25}(?:are|is) (?:stocked|carried|available)|"
    r"asked for .{0,40}(?:but|which) (?:we|it|they)|unmet", re.I)
_EQUIPMENT_RE = re.compile(
    r"printer|fridge|refrigerat|phone|register|till|drawer|alarm|wiring|batter|"
    r"scale|kettle|freezer|leak|broken|not working|jam(?:med|ming)?|"
    r"out of order|unsafe|shelv|storage|POS\b|terminal|card reader", re.I)
_COMMITMENT_RE = re.compile(
    r"promised|committed|agreed to|will (?:call|send|hold|order|follow)|"
    r"call(?:ing)? back|on hold for|special order|quote for|follow up", re.I)
# Restating a record. These are stored forever and the narrative is told not to
# spend itself on them; a bullet doing it is worse, because it occupies a slot.
_RESTATEMENT_RE = re.compile(
    r"order is ready for pickup|ready for pickup|send email details|"
    r"email details (?:for|to)|notification[s]? (?:sent|to)|text (?:sent|message sent)|"
    r"voicemail (?:received|from)|check your email", re.I)


def _claim_score(text: str, known: list) -> int:
    """How searchable a candidate bullet is a year from now.

    The ranking IS the quality control. Slice notes are a mix of the day's best
    findings and its worst inventions - the 2026-07-19 notes contained the real
    $70 drawer overage alongside three phantom shortfalls, a customer's Spotify
    complaint and construction on 12th Street that never happened. The gates
    delete what is checkably false; this decides what earns one of five slots
    among everything that survives.
    """
    score = 0
    nt = _norm_name(text)
    if any(k in nt for k in known):
        score += 3                                  # names a real product
    if re.search(r"#\s?\d", text):
        score += 2                                  # cites a record
    if _MONEY_RE.search(text):
        score += 2
    elif re.search(r"\d", text):
        score += 1
    # The categories the log exists for. Without these a qualitative finding -
    # "a customer asked for saffron, which we don't carry" - scores zero,
    # because it names no catalog product (that is the whole point of it) and
    # carries no number. On 2026-07-19 that let five pickup-notification
    # restatements outrank every real finding of the day.
    if _UNMET_SHAPE_RE.search(text):
        score += 3
    if _EQUIPMENT_RE.search(text):
        score += 3
    if _COMMITMENT_RE.search(text):
        score += 2
    # ...and the category it exists to exclude. A line restating a text message
    # or a pickup notification is in the records forever and answers nothing.
    if _RESTATEMENT_RE.search(text):
        score -= 5
    if _PLACEHOLDER_RE.search(text):
        score -= 3
    if any(_is_garble(q) for q in re.findall(r'"([^"]{3,})"', text)):
        score -= 4
    words = len(text.split())
    if words < 6:
        score -= 2                                  # too thin to act on
    if words > 40:
        score -= 1                                  # a paragraph, not a bullet
    return score


def _section_bullets(lines: list, heading: str):
    """(start, end, [bullet text]) for a section, or None."""
    try:
        i = next(k for k, l in enumerate(lines) if l.strip() == heading)
    except StopIteration:
        return None
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    bullets = [l.strip()[2:].strip() for l in lines[i + 1:j] if l.lstrip().startswith("- ")]
    return i, j, bullets


def _rebuild_bullet_sections(markdown: str, notes: list, products: list,
                             want: int = 8) -> str:
    """Build the bullet sections FROM the slice notes, not from the merge.

    The merge is the lossiest stage in the pipeline and always will be: an 8B
    model handed a dozen slices of notes and asked for two paragraphs plus ten
    bullets keeps perhaps a third of what it was given. Measured against forty
    findings identified by hand from the same four days, the log carried 30%.

    The notes do not have that problem - they are written per slice, with the
    whole slice in context, and they found the things the merge dropped: the
    $70 drawer overage, the saffron request, the label printer. Topping the
    sections up from them afterwards was still the merge's list plus scraps.
    So the notes become the primary source: every tagged note and every bullet
    the model wrote compete on one ranked list, and the best fill the section.

    Draft bullets carry a small bonus - they have been through the correlation
    pass and may have earned a record reference the raw note lacks - but they
    no longer win by default. `want` is deliberately larger than the final cap
    so the gates downstream have slack to reject candidates without leaving a
    section thin.
    """
    tagged = _parse_tagged_notes(notes)
    if not tagged:
        return markdown
    known = [k for k in ({_norm_name(str(p.get("name") or "")) for p in (products or [])}
                         - {""}) if len(k) >= 6]
    lines = markdown.splitlines()
    rebuilt = 0

    for heading in ("## Unresolved", "## Worth remembering"):
        found = _section_bullets(lines, heading)
        if not found:
            continue
        i, j, existing = found
        # A draft bullet is only privileged when the correlation pass gave
        # it a record reference the raw note cannot have. Otherwise it
        # competes on merit: a flat +1 was enough to keep five pickup-text
        # restatements ahead of every real finding on 2026-07-19.
        cands = [(b, _claim_score(b, known) + (2 if _ANNOTATION_RE.search(b) else 0),
                  "draft") for b in existing]
        for tag, text in tagged:
            if _carry_section(tag) != heading:
                continue
            cands.append((text, _claim_score(text, known), "note"))

        picked = []
        for text, score, src in sorted(cands, key=lambda c: -c[1]):
            if score < CLAIM_SCORE_FLOOR:
                break                           # ranked list: the rest are worse
            if any(_same_claim(text, p) for p, _, _ in picked):
                continue
            picked.append((text, score, src))
            if len(picked) >= want:
                break
        if not picked:
            continue
        n_notes = sum(1 for _, _, src in picked if src == "note")
        if n_notes:
            rebuilt += n_notes
        lines[i + 1:j] = [""] + [f"- {t}" for t, _, _ in picked] + [""]

    if rebuilt:
        _warn(f"bullet sections rebuilt from notes: {rebuilt} note-sourced candidate(s)")
    return "\n".join(lines)


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
    amounts, order_hour = {}, {}
    for o in ((biz.get("sales") or {}).get("orders") or []):
        amounts[str(o.get("id"))] = float(o.get("total") or 0)
        t = str(o.get("time_local") or "")
        if len(t) >= 13 and t[11:13].isdigit():
            order_hour[str(o.get("id"))] = int(t[11:13])
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
            # CORRELATE_SYSTEM asks for time AND amount corroboration; the code
            # only ever checked amount, which is how a $62.15 order timed 05:24
            # got stapled to speech at 11:27 purely because the number matched.
            # A general proximity test isn't possible (the log line rarely
            # states when the event happened), but a register/cash/drawer line
            # is by definition about something that happened on the floor, so
            # an order placed outside store hours cannot be it.
            if _REGISTERISH.search(line):
                h = order_hour.get(rid)
                if h is not None and not (STORE_OPEN_HOUR <= h < STORE_CLOSE_HOUR):
                    return ""
            line_amts = [float(a.replace(",", ""))
                         for a in re.findall(r"\$([\d,]+(?:\.\d+)?)", line[:m.start()])]
            if line_amts:
                rec = amounts[rid]
                if all(abs(rec - a) > 0.25 * max(rec, a, 1.0) for a in line_amts):
                    return ""
            return m.group(0)
        out.append(_ANNOT_RE.sub(repl, line))
    # CORRELATE_SYSTEM caps annotations at 3 for the whole log and says to
    # annotate EVENTS only, never product mentions - but stating a rule and
    # enforcing it are different things, and the 8B routinely ships 5+, mostly
    # stapled to products. Drop by priority, not by position: an action bullet
    # or a line about money going wrong is the kind of thing a reference helps;
    # a product name in narrative prose is exactly what the prompt forbids.
    _EVENTISH = re.compile(
        r"discrepan|shortfall|reconcile|missing|refund|chargeback|void|"
        r"short\b|over\b|dispute|problem|issue|failed|error", re.I)
    located = []
    for i, line in enumerate(out):
        for m in _ANNOT_RE.finditer(line):
            is_event = line.lstrip().startswith(("-", "*")) or _EVENTISH.search(line)
            located.append((0 if is_event else 1, i, m.start(), m.end()))
    if len(located) > 3:
        _warn(f"annotations: capping {len(located)} record refs at 3")
        keep = {(p, i, s, e) for p, i, s, e in sorted(located)[:3]}
        # Delete right-to-left within each line so offsets stay valid.
        for p, i, s, e in sorted(located, key=lambda t: (t[1], t[2]), reverse=True):
            if (p, i, s, e) not in keep:
                out[i] = out[i][:s] + out[i][e:]
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
        # Counts only - the support system itself is where ticket details live.
        # Ticket content still reaches the summarizer (via the context block) so
        # genuine issues surface in the narrative/Unresolved instead of a list.
        parts.append(f"**Support:** {sup.get('tickets_created', 0)} new · "
                     f"{sup.get('inbound_messages', 0)} inbound · "
                     f"{sup.get('tickets_closed', 0)} closed · {sup.get('open_now', 0)} open")
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
    # Ask Chloe for the GPU first. _ensure_gpu has existed for this since the
    # imagegen work but was never wired in, so the job just collided with her.
    # Budget = the offload we intend plus headroom for the KV cache, which
    # grows with n_ctx (that is what broke a 32k run that was fine at 8k).
    size_mb = os.path.getsize(SUMMARIZER_MODEL) / (1024 * 1024)
    per_layer_mb = size_mb / 48.0                       # ~blocks in these models
    want_mb = int(SUMMARIZER_GPU_LAYERS * per_layer_mb + SUMMARIZER_CTX * 0.10) + 700
    _ensure_gpu(want_mb)

    # Size the offload to the VRAM that actually exists rather than discovering
    # it by failing: a failed Llama() leaves llama.cpp in a state where the NEXT
    # construction can abort() the whole process - an uncatchable SIGABRT, not a
    # Python exception - so a retry chain alone is not a safety net.
    plan = sorted({SUMMARIZER_GPU_LAYERS, SUMMARIZER_GPU_LAYERS // 2, 0}, reverse=True)
    free = _free_vram_mb()
    if free >= 0:
        cap = int(max(free - 700 - SUMMARIZER_CTX * 0.10, 0) / max(per_layer_mb, 1))
        if cap < max(plan):
            _warn(f"{free}MB VRAM free -> capping offload at {cap} layers "
                  f"(wanted {SUMMARIZER_GPU_LAYERS})")
            plan = sorted({min(l, cap) for l in plan}, reverse=True)

    # ONE attempt, then re-exec. A failed llama_context poisons the process:
    # the next construction dies on an uncatchable SIGABRT, so an in-process
    # retry chain doesn't degrade, it core-dumps (confirmed twice - the first
    # load raised a normal Python exception, the retry aborted). Dropping the
    # reference and collecting doesn't help; only a fresh process does.
    try:
        llm = Llama(model_path=SUMMARIZER_MODEL, n_ctx=SUMMARIZER_CTX, n_threads=8,
                    n_gpu_layers=plan[0], verbose=False)
    except Exception as e:
        if os.environ.get("SUMMARIZER_GPU_LAYERS") == "0":
            raise RuntimeError(f"summarizer failed to load even on CPU: {e}")
        _warn(f"load with {plan[0]} GPU layers failed ({e}); re-exec on CPU")
        os.environ["SUMMARIZER_GPU_LAYERS"] = "0"
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable] + sys.argv)  # never returns
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
        # "/no_think" is a Qwen3 HYBRID control token. Non-hybrid instruct models
        # (e.g. Qwen3-*-Instruct-2507) don't understand it and just see a stray
        # token at the end of every system prompt. SUMMARIZER_NOTHINK=0 strips it.
        if not _NOTHINK:
            system = system.replace(" /no_think", "").replace("/no_think", "")
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

    # Staff chat and business records always get their own notes pass, on short
    # days too. It is the only representation of them that survives to the
    # carry-through below - as raw text at the end of the final prompt they
    # were reliably ignored.
    notes = []
    if slack_block.strip():
        notes.append(_gen(NOTES_SYSTEM,
                          SIDE_NOTES_TEMPLATE.format(chunk=slack_block), 500))

    if _tok(compact) <= INPUT_BUDGET:
        draft = _final("transcript", compact)
    else:
        # Long day: chunk -> notes per slice -> merge into the log.
        chunks = _chunk_by_tokens(llm, compact, INPUT_BUDGET)
        _warn(f"long day: {len(chunks)} slices -> notes -> final")
        notes += [
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

    # Carry through what the merge dropped. Runs BEFORE redaction and the
    # catalog stage on purpose: a carried line is not privileged, it goes
    # through every check the model's own bullets do.
    draft = _NOTE_TAG_ANY_RE.sub("", draft)      # tags never reach the log

    # Catalog check: quoted product names that don't exist in the real (3dcart)
    # catalog. Clear mishears are corrected HERE, in code - string replacement
    # is exact and the 8B model applying its own replacements proved unreliable.
    # This reaches the names the POS weave can't: out-of-stock requests never
    # ring up, so they have no order line to correct against. Ambiguous names
    # ride into the redaction pass below as CATALOG NOTES.
    if products:
        auto_fixes, review = _match_catalog(draft, products)
        for q, name in auto_fixes:
            _warn(f"catalog fix: \"{q}\" -> \"{name}\"")
            draft = draft.replace(q, name)
        draft, n_annotated = _annotate_catalog(draft, review)
        if n_annotated:
            _warn(f"catalog: annotated {n_annotated} likely-match names")

    # Independent redaction pass: re-read the draft ONLY to break name-to-
    # sensitive-content links and strip personal-life/garble that slipped
    # through. Cheap on GPU (~seconds).
    _warn("redaction pass...")
    draft = _gen(REDACT_TEMPLATE, f"Draft to clean:\n\n{draft}", 1800, temperature=0.1)

    # Linkage guard, detect-then-surgery: whole-document editing is exactly
    # what this 8B keeps failing at (a named person's health interest survived
    # repeated redaction rolls), but LISTING violations is a small structured
    # task it handles. The model detects; code strips the name.
    _warn("linkage scan...")
    scan = _gen(LINKAGE_SCAN_SYSTEM, f"Log to check:\n\n{draft}", 400, temperature=0.1)
    findings = [l for l in scan.splitlines() if "|" in l]
    if findings:
        draft = _strip_linkage_names(draft, findings)
    return draft, notes


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

    # Keep the un-woven speech: the discrepancy gate asks "did a human say this
    # amount out loud", and _weave_orders injects every POS total into the
    # transcript. Checking the woven text would let a sale total vouch for
    # itself as a discrepancy - exactly the failure the gate exists to catch.
    raw_speech = transcript

    notes = []
    if have_speech:
        transcript = _weave_orders(transcript, biz.get("sales"), date_str)
        markdown, notes = _summarize(transcript, day, slack_text, _records_index(biz),
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
    # Everything the model was allowed to draw on. The echo gate needs all four
    # sources: an item is only a prompt echo if it is in NONE of them.
    grounding = "\n".join(filter(None, [
        raw_speech if have_speech else "", slack_text,
        _records_index(biz), _context_block(biz),
    ]))
    def gates(md):
        md = _scrub_contact_info(md)
        md = _reject_prompt_echoes(md, grounding)
        md = _reject_training_artifacts(md, raw_speech if have_speech else "")
        # Before the stock flagger: otherwise a real catalog name buried inside
        # a garbled string ("…rye, brunckel, Strawberry Black") gets
        # stock-flagged, dressing mic noise up as a verified reorder.
        md = _drop_garble_claims(md)
        md = _drop_non_events(md)
        md = _reject_sold_reorders(md, biz)
        md = _reject_unsupported_discrepancies(md, raw_speech if have_speech else "")
        md = _flag_stock_contradictions(md, products)
        # Last, so the catalog and stock stages still see the quotes they key on.
        md = _unquote_unverified(md, products)
        md = _drop_credentials(md)
        md = _drop_overheard_quotes(md)
        md = _drop_record_name_lists(md, biz)
        md = _drop_vague_bullets(md, products)
        md = _drop_personal_health(md)
        md = _fix_discrepancy_direction(md, raw_speech if have_speech else "")
        md = _drop_stray_sections(md)
        return md

    # Gate the draft, rebuild the bullets from the notes, gate again.
    #
    # The order matters and cost three attempts to get right. Rebuilding before
    # the first pass would rank fabrications against real findings; skipping
    # the second pass would let a note-sourced bullet into the log unchecked.
    # Gating either side means every bullet faces the same tests no matter
    # which stage produced it, and the ranking only ever chooses among
    # survivors.
    markdown = gates(markdown)
    rebuilt = _rebuild_bullet_sections(markdown, notes, products)
    if rebuilt != markdown:
        markdown = gates(rebuilt)
    # Cap last, at the policy's own number: the rebuild deliberately keeps more
    # candidates than will ship so the gates have slack to reject without
    # leaving a section thin.
    markdown = _cap_bullet_lists(markdown, cap=5)
    markdown = _fix_generic_opener(markdown, biz)
    # Runs BEFORE the stats block is appended: timeclock names belong there,
    # what must not survive is a staff name attached to something they said.
    markdown = _strip_staff_attribution(markdown, _staff_names(biz, slack_names))
    # Last of the editors: everything above only ever removes, so a section can
    # end up empty and a bare heading reads as a broken render.
    markdown = _mark_empty_sections(markdown)
    markdown = markdown.rstrip() + "\n\n" + _business_sections(biz)

    # DRY_RUN builds the log and prints it without touching git - the only way
    # to compare summarizer models on a real day without republishing it.
    if os.environ.get("DRY_RUN"):
        _warn(f"{date_str}: DRY_RUN - built but not pushed")
        sys.stdout.write(markdown)
        return

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
