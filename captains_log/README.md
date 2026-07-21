# Captain's Log

A daily operational summary of the shop — shop-floor audio distilled into a
de-identified narrative, plus the day's hard business numbers (sales, shipping,
support, calls/texts, staff hours) pulled from the dashboard's datalog API.

## The business day is 6pm–6pm Mountain

The log for date **D** covers **6:00pm on D-1 through 6:00pm on D**. After-hours
online orders, support emails, and texts are handled at next opening, so they
belong on the *next* day's log — a ticket that arrives 8pm July 20 shows up in
the July 21 log. Downtown has no after-hours in-store sales by definition.

The **audio** transcript still covers store hours of calendar day D (the mic
window, 08:00–20:00). The one place the two windows meet is order-weaving, which
uses only calendar-day-D register orders so the audio timeline stays coherent.

## How it works

```
HA fires the AI-box trigger at 7pm MT (one hour after the business day closes)
        │
        ▼  on the AI box (captains_pipeline.py)
UniFi Protect audio → faster-whisper large-v3 (GPU) → day transcript
dashboard datalog API → sales / shipping / support / calls / texts / timeclock
Slack API → staff channel messages (real names)
        │
        ▼
POS orders woven into the transcript by timestamp
  "[14:14] ⟦POS $43.50 (sample given) — Earl Grey, Honey Sticks ×3⟧"
        │
        ▼  local Qwen3-8B (GPU)
summary draft → CORRELATION pass → REDACTION pass
        │
        ▼  deterministic (no LLM)
+ Business day / Support / Comms / Staff sections rendered straight from JSON
        │
        ▼
committed to captains_log/YYYY-MM-DD.md on the captains-log branch,
raw transcript deleted
```

- **Weaving** gives the summarizer ground truth: a sample offered on audio and
  the matching sale minutes later become one connected observation, and garbled
  product names get corrected against what the register actually rang up.
- **Correlation pass**: the summarizer reads chronologically, so an end-of-day
  conversation about an order discrepancy can't cite the morning order it
  concerns. A second pass re-reads the finished draft against a one-line-per-
  record digest of the whole day (orders, tickets, calls) and appends references
  like *"(likely order #58212, $43.50 at 2:14pm)"* — only ever using ids that
  exist in the records, marking inferred links "likely".
- **Deterministic sections**: dollar figures, counts, names, and hours never
  pass through the LLM — they're rendered directly from the datalog JSON after
  redaction, so they can't be mangled or hallucinated.
- Every business fetch is **fail-soft**: an unreachable endpoint becomes a
  "_data unavailable_" line, never a failed run. A day with no captured speech
  still gets a log with the business sections.

## Privacy policy (the summarizer MUST follow this)

**The rule is linkage, not names.** A name attached to an operational fact is
welcome — "send samples to Nancy Brown", "hold two tins for the Duran
account" are exactly what makes the log actionable. What must never appear is
a **named person tied to sensitive content**: health/medical details,
personal-life circumstances, or attributed remarks ("so-and-so said/felt X").
The fix is breaking the link, not deleting the fact — "a customer asked about
teas safe during pregnancy" stays; naming her in that sentence doesn't.

Still dropped entirely, name or no name:

- Personal-life chatter with no operational value (school, jobs, hobbies,
  travel, family, relationships, religion, politics, feelings, small talk).
- Contact info (phone, email, address) for any individual.
- Verbatim quotes attributed to a person; gossip.
- Garbled audio presented as fact — a detail that looks mis-transcribed is
  dropped, never guessed at. Register (POS) product names are never garble.

## Format

The narrative half (`Hours active` / `Traffic` / `Product & topics` /
`Notable / follow-ups` / `Staff & ops notes`) is written by the summarizer.
Then the deterministic half is appended:

```markdown
## Business day (6pm–6pm MT)
**Online:** $512.40 retail (9 orders)
**In-store:** $1,041.77 retail (33 orders) + $210.00 wholesale (1) · 2 pickup orders ($45.50)
**Shipped:** 21 orders · 24 labels (1 voided) · postage $187.33 — USPS 18, UPS 3

## Support
3 new · 7 inbound messages · 2 closed · 5 open now
- New #91 08:11 — Jane Miller: "Missing tin from order" (Orders)

## Comms
**6 calls (25 min) · texts 4 in / 6 out · 1 text awaiting reply**

## Staff
**15.9 labor hours**
- Dawn S: 8:58am–5:02pm (7.54h, 32m break) — Downtown
```

## Data sources & credentials

All on the AI box in `/etc/nmteaco/captains.env` (mode 600, never committed):

| Key | Purpose |
| --- | --- |
| `GITHUB_TOKEN` | push the finished log to the `captains-log` branch |
| `DATALOG_API_TOKEN` | bearer token for `https://dashboard.nmteaco.com/tools/datalog/*.php` (same value lives in the dashboard's `/home/nmteaco/.env`) |
| `DASHBOARD_BASE_URL` | optional override, default `https://dashboard.nmteaco.com` (www is bot-challenged) |
| `SLACK_BOT_TOKEN` | optional; scopes `channels:history` (+`groups:history` for private channels), `users:read` — bot must be invited to the channels |
| `SLACK_CHANNELS` | optional; comma-separated channel IDs to read |

## Retention

Raw transcripts live only on the AI box (`~/captains_transcripts/`, never in
git) and are **kept** so test reruns skip the ~30 min re-transcription — the
pipeline reuses an existing day-file and only pays for the LLM stages
(`FORCE_RETRANSCRIBE=1` redoes the audio; `DELETE_TRANSCRIPTS=1` restores
delete-on-success). GitHub still only ever holds the sanitized dated
summaries.
