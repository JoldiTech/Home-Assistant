# Captain's Log

A daily, **de-identified** operational summary of the Tea One store audio — a
"captain's log of the day," not a transcript of everything said in the store.

## How it works

```
Local whisper (tiny.en) transcribes Tea One audio  →  /share/tea_one_transcript.log   (transient, on the HA box)
        │  once a day at 7pm MT
        ▼
Opus reads the day's raw transcript, writes a sanitized summary here  →  captains_log/YYYY-MM-DD.md   (durable, in git)
        │  only after the summary is committed + pushed
        ▼
Raw transcript is deleted.
```

**The split is deliberate:** the *local model* only transcribes (mechanical). The
*judgment* — what's worth keeping and what must never be written down — is done by
Opus at summary time. The raw, word-for-word transcript is never kept; only the
scrubbed daily log survives.

## Multi-camera fusion (planned, once all cameras transcribe)

Today only Tea One transcribes. Once every camera does (needs the GPU box), the
summarizer is fed **all cameras' transcripts, each line tagged with its camera and
timestamp** — and does something no local model can:

- **De-duplicate across cameras.** Two cameras that overhear the *same* counter
  conversation (both catch the "instant chai" chat within the same minute) get
  merged into **one** event, not counted twice — using timestamp proximity plus
  content similarity.
- **Cross-reconstruct.** Each camera catches slightly different fragments of the
  same speech; the summarizer stitches them into a more complete, higher-confidence
  version than any single camera's transcript.
- **Locate.** Which camera(s) heard it says roughly *where in the store* it
  happened — useful context for the log.

This is why the raw lines must keep their **camera label + timestamp**: those are
what make fusion possible.

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

The raw transcript is **ephemeral** — deleted each night once the day's log is
safely in git. These dated summaries are the durable record.
