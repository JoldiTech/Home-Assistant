# Captain's Log

A daily, **de-identified** operational summary of the Tea One store audio — a
"captain's log of the day," not a transcript of everything said in the store.

## How it works

```
Local whisper transcribes each camera's audio  →  /share/<camera>_transcript.log   (transient, on the HA box)
        │  once a day at 7pm MT
        ▼
Opus reads EVERY camera's transcript at once, writes ONE combined summary  →  captains_log/YYYY-MM-DD.md   (durable, in git)
        │  only after the summary is committed + pushed
        ▼
ALL raw transcripts are deleted.
```

Cameras are **auto-discovered**: the job globs `/share/*_transcript.log`, so
pointing a new transcribe add-on at another camera is all it takes — the next
nightly run includes it with zero code changes.

**The split is deliberate:** the *local model* only transcribes (mechanical). The
*judgment* — what's worth keeping and what must never be written down — is done by
Opus at summary time. The raw, word-for-word transcript is never kept; only the
scrubbed daily log survives.

## Multi-camera: combining is the summary step

There is **no separate "combine" stage**. Every camera's transcript is just more
input to the one summary pass — Opus reads them all together (each under a
`===== CAMERA: <name> =====` header, with timestamps) and, while writing the
single day's log, naturally:

- **De-duplicates** — two cameras that overhear the *same* counter conversation
  (both catch the "instant chai" chat in the same minute) become **one** event in
  the log, not two.
- **Cross-reconstructs** — each camera catches slightly different fragments; using
  them together yields a more complete, higher-confidence read than any one alone
  (the transcription is lossy, so this genuinely helps).
- **Locates** — which cameras heard it says roughly *where in the store* it was.

That's it — no clustering pipeline, no per-camera code. Adjacent cameras (e.g.
Tea One + Emporium Hall) overlap the most and help each other the most.

## Sanitization policy (the summarizer MUST follow this)

The goal is an operational log for running the shop — **not** a record of who said
what.

**Include (de-identified, aggregate):**
- Store rhythm — active hours, busy vs. quiet stretches, overall traffic feel.
- Product / topic interest — which teas, categories, and questions came up (as
  themes/counts, never tied to a person).
- Operational events worth remembering — possible order/payment issues (e.g. a
  chargeback), stock/supply mentions, equipment problems, notable large/wholesale
  or curbside orders (amounts fine, names not).
- Staff-surfaced business observations (e.g. "staff noted Sundays outperform
  Mondays").
- Anything actionable for running the shop.

**Never write:**
- Names of customers or staff, or anything that identifies a specific person.
- Contact info (phone, email, address).
- Health/medical details tied to an individual. (You may note *"a wellness-tea
  consultation occurred"* in the aggregate — never the person or their specifics.)
- Personal-life specifics (relationships, travel, family, religion, …).
- Verbatim quotes that could identify someone.
- Gossip or staff interpersonal conflict — include only if operationally relevant,
  and then neutral + de-identified.

**Rules of thumb:**
- When in doubt, leave it out. A shorter, safer log beats an oversharing one.
- Transcription is lossy (small model) — flag uncertain items as *"possible"* and
  never assert shaky specifics as fact.

## Format

```markdown
# Captain's Log — <Weekday> <YYYY-MM-DD>

**Hours active:** …
**Traffic:** …

## Product & topics
- …

## Notable / follow-ups
- …

## Staff & ops notes
- …

_Source: Tea One mic → local whisper (tiny.en) → summarized by Opus. Raw transcript discarded after this log was written._
```

## Retention

The raw transcripts are **ephemeral** — every camera's log is deleted each night
once the day's combined summary is safely in git. These dated summaries are the
only durable record.
