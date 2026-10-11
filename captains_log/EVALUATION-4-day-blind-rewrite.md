# Four-day evaluation: published Captain's Log vs. a hand-written log from the same input

**Run date:** 2026-07-27 · **Days compared:** 2026-07-19 (Sun), 07-21 (Tue),
07-23 (Thu), 07-26 (Sun)

## Method

For each day the pipeline's *exact* summarizer input was dumped without running
the summarizer — POS-woven and compacted transcript, records index, context
block, Slack, active window — using `captains_pipeline` directly. A competing
log was then written from that same input under the same `SYSTEM_PROMPT` policy,
and both versions were checked line by line against the input.

Same input, same brief, same format. The deterministic "By the numbers" block is
identical either way and is excluded from scoring.

## Result

| | published | hand-written |
| --- | --- | --- |
| fabricated or unsupported claims | **15** | 0 |
| garbled strings printed as quoted product names | **9** | 0 |
| completed sales reported as unmet demand | **3** | 0 |
| high-value items captured (~45 present in the inputs) | ~9 | ~40 |
| openers carrying a day-unique fact | 0 / 4 | 4 / 4 |
| staff name attached to a remark | 0 | 0 |
| sensitive personal content leaked | 0 | 0 |

The privacy machinery works. The grounding does not. Across four days the
pipeline invented three register discrepancies that never happened, missed the
one that did, and on two days reported products as unavailable that the woven
POS line in its own input shows were sold minutes earlier.

---

## 1. The prompt's own examples are being emitted as content

`"Reorder XL gloves"` appears in the published log for **all four days**. The
request is real — but it exists in exactly one place, a Slack message inside the
07-23 window. On 07-19 and 07-21 the day's Slack block is `(none)` and one
unrelated line respectively, and both days *precede* the request. On 07-26 the
Slack is entirely about tins, phones and the fridge.

`"teas safe during pregnancy"` appears in the published 07-21 and 07-23 logs.
The string `pregnan` appears in **none** of the four days' inputs.

Both phrases are worked examples in the prompts:

- `captains_pipeline.py:211` — `"a staff request to restock XL gloves", never "restock XL gloves per George's request"`
- `captains_pipeline.py:223` — `"a customer asked about teas safe during pregnancy" is fine`
- `captains_pipeline.py:326`, `:333` — the same two, repeated in `REDACT_TEMPLATE`

`NOTES_SYSTEM` inherits `SYSTEM_PROMPT`, so on a long day the model sees them
once per slice. The prompt forbids copying an example *name* into the log; it
says nothing about copying the example *event*, and an 8B model does not
generalise the rule on its own.

**Fix:** replace both worked examples with placeholders that cannot be mistaken
for shop content — `"restock [the out-of-stock supply]"`, `"a customer asked
about teas for [a health concern]"` — and add the sentence "never copy an
example product, item, or situation from these instructions into the log."
A cheap belt-and-braces check: reject any Unresolved bullet whose distinctive
noun phrase appears in the prompt but not in the day's input.

## 2. The discrepancy gate scrubs bullets but not narrative

2026-07-26's run log shows the gate working:

```
dropped discrepancy bullet - no one reported one: - Reconcile the register shortfall of $23.50.
dropped discrepancy bullet - no one reported one: - Investigate the unaccounted sale totaling $34.98.
dropped discrepancy bullet - no one reported one: - Address the discrepancy where $16.14 was recorded...
```

The published 07-26 log nevertheless contains, in paragraph one:

> A discrepancy in register: $23.50 unaccounted for.

`_reject_unsupported_discrepancies` only inspects lines beginning with `-`. The
identical fabricated claim shipped in prose. `23.50` appears nowhere in the
day's input — not in speech, not in any POS line, not in the records.

**Fix:** run the same test over narrative sentences, not just bullets.

## 3. "The amount was spoken aloud" is not a usable test

On 07-19 the published log reports **"an unexplained shortfall of $37.66"**
three times. $37.66 is a POS order (Voyager Journal + two Sushi Plate Cups) and
is spoken once — `It's going to be $37.66.` — as a total read to a customer.
On 07-23 the same shape: **"a register shortfall of $6.46"**, where $6.46 is POS
order #331634 and the one spoken instance is `Down to 6.46.` at the counter.

The gate requires (a) day-level discrepancy language somewhere and (b) the
amount spoken. In a shop, *every* sale total is spoken aloud, so (b) is
satisfied by definition and the gate is close to a no-op for the commonest
failure.

**Fix:** require the amount to appear within N lines of the discrepancy
language, not merely somewhere in the same day; and reject outright any amount
that exactly matches a POS order total for the day unless the speech within a
minute of it says short/over/missing/doesn't match.

## 4. Completed sales reported as unmet demand

2026-07-26, published:

> A customer asked for "the 16 ounces of the Nahili mill jury and cleaner" but
> it was unavailable.

"Nahili mill jury and cleaner" is **Nilgiri Iced Tea Blend** (the customer says
"the ice one" on the next line), and it sold: POS #331905, $30.78, *Doke Black
Fusion | Organic, Nilgiri Iced Tea Blend* — the same ticket as the black-fusion
sample requested in that exchange.

> A customer asked for two ounces of Sandia spice and one immune support blend,
> but neither were available.

Both sold: POS #331908, $36.59, *Amber | 12oz Taos Honey, Sandia Spice, Immune
Support - Organic*.

`_reject_sold_reorders` implements "a sale is proof of stock" — but only for
Unresolved bullets. A narrative sentence asserting unavailability is untouched.

**Fix:** extend the sold-today check to narrative sentences containing
"unavailable / out of / didn't have / not available" alongside a catalog name.

## 5. Garble is still being printed inside quotation marks

The policy is explicit: *"Never put an unverified phrase in quotation marks — a
quoted string is treated downstream as a real product name."* Nine survived:

| day | printed | what it actually was |
| --- | --- | --- |
| 07-19 | `"Cork option paper"` | ticket #3078: *"Question about paper canister: material and reusability"* |
| 07-19 | `"boochoo," ... "mixed with royalts"` | buchu blended with rooibos |
| 07-19 | `four ounces of Fauna` | Vana tulsi — correctly said as "vana" elsewhere in the same transcript |
| 07-21 | `"muxbond four M's"`, `"bamboo camp two ounces"`, `"go to my post account and four ounces"` | **fake practice orders invented by a trainer** |
| 07-23 | `"the dealership"` | a discontinued double-sided pot/spoon measuring scoop a customer wants back |
| 07-26 | `"the 16 ounces of the Nahili mill jury and cleaner"` | Nilgiri Iced Tea Blend (sold) |

The 07-21 case is the worst of the four days. Line 175 of that day's input:

> "I think what I want to do is give you some fake orders. Okay, so first I want
> a handful of teas. I want muxbond four M's…"

A register-training role-play for a new hire became the log's opening sentence,
reported as three items of genuine unmet demand.

**Fix:** a code-level rule — drop any quoted string that `_is_garble` flags,
rather than only rejecting catalog *renames* of it (the catalog stage already
refused to rename this exact 07-26 string, then let it print verbatim). And
teach the notes stage that speech containing "fake order", "practice", "let's
pretend", "I want to give you" near a product list is training, not demand.

## 6. What the pipeline missed

The four published logs together carry roughly nine real observations, several
of them routine retail the policy says to compress to a clause. The same inputs
contain around forty-five. The largest omissions, day by day:

**07-19** — the drawer came up **$70 OVER** at close, with a live theory that a
card sale which appeared to decline actually went through and the customer was
then rung up a second time ("maybe we charge someone an extra 700 bucks… we have
to talk to her about it tomorrow"), plus "yesterday we were off by seven". Also
absent: an electrical switch left with its faceplate off and wiring exposed,
hidden behind a wall calendar and called a fire and shock risk; a new hire's
first shift; a pickup order dissolved and returned to the warehouse while
someone was coming to collect it; saffron asked for and not carried, with a
local grower as a possible source; the store remote's dying batteries; a missed
clock-out.

So on 07-19 the pipeline invented a shortfall that did not happen and dropped a
possible double-charge of a real customer.

**07-21** — the POS refund control is broken in a specific repeatable way with a
known workaround; new phones going in and a call that did not ring through;
Vietnam black tea out indefinitely with a regular asking; ticket #3128, a 2026
sponsorship request from a youth soccer club; ticket #3139, a customer wanting
notification when unsweetened Insta Masala Chai restocks; a package mailed
June 13 showing refused/uncollected; enamel blowing off the cast-iron teapots;
old gift cards mis-linking sales to a previous owner's account.

**07-23** — the day's stated cause (the first real rain, which staff tied
directly to the strong opening stretch); Extra Sleepy Bear tasting noticeably
stronger than usual; a Japanese cultural festival asking the shop to vend or run
a demonstration in early September; a twig green tea that can no longer be
sourced; the matcha kits' new supplier and the feedback staff are collecting;
newsletter opt-out silently removing customers from the order-ready email; and
the real cash item — the drawer short of ones and fives, tips broken to make
change, an IOU left in the drawer, a bank run needed.

**07-26** — the entire Slack contents. Phones not transferring to the warehouse;
buchu sold but absent from the POS system and rung up as Earl Grey, corrupting
two products' sales history on every sale; Grand Keemun still on its pre-tariff
price; the fridge wedged open by a basket and close to full, with an unexplained
change in what gets refrigerated and heavy stock shelved unsafely high; chai
split across three shelves so a large bag of masala chai went missing; the new
Witch's Broom Puerh batch tasting materially different; cinnamon sticks asked
for twice and not carried; a fruit tisane with chamomile asked for and not in
the range; a roasted yaupon from a Texas supplier that tasted excellent and then
went silent.

## 7. Openers

All four published logs open with a sentence that could be true of any day —
including 07-26's *"A steady flow of customers entered the shop"*, which is the
exact phrase the system prompt names as banned. The rule is stated once, near
the end of a long FORMAT section, and is not enforced anywhere in code.

**Fix:** a deterministic check. If the first sentence contains no number, no
proper noun, and no catalog product, regenerate the paragraph once.

## 8. What the pipeline gets right

Worth stating plainly, because it is the part that was hardest to build:

- **No staff name is attached to a remark on any of the four days.** The 07-23
  XL-gloves item is correctly rendered as a staff request with no name.
- **No sensitive personal content survived**, and 07-19 in particular is full of
  it — therapy and childhood-memory talk, a customer buying mullein because her
  son smokes hash, extended political argument, staff gossip about who talks to
  whom. None of it reached the log.
- The catalog stage correctly **refused** to rename two garbled strings on
  07-26 rather than guessing.
- Real items were captured: the greeting-card price conflict on 07-21, the label
  printer settings on 07-19, the custom blend for a regular, the Oregon-bound
  customer's praise for Scottish Breakfast.

## 9. One hazard found while reading

The 07-19 transcript contains the shop's **actual password practice**, spoken
aloud during POS training. It did not reach the log — but it is sitting in
`~/captains_transcripts/tea_one_2026-07-19.log` on the AI box in plain text, and
transcripts are currently retained rather than deleted. Worth knowing before
transcripts are copied anywhere.

## Priority

1. Neutralise the prompt examples (§1) — one edit, removes an error present in
   every log.
2. Apply the discrepancy and sold-today gates to narrative, not just bullets
   (§2, §4).
3. Tighten the discrepancy test beyond "the amount was spoken" (§3).
4. Drop garble-flagged quoted strings in code (§5).
5. Enforce the opener rule (§7).

None of these require a larger model. The Qwen3-30B experiment already showed
more capacity buys more convincing confabulation, not less; every failure above
is a missing deterministic check around an 8B model that is behaving exactly as
an 8B model does.

---

# After: what the fixes actually changed

Re-measured 2026-07-27 by regenerating all four days through the real
pipeline (`DRY_RUN=1`) and re-scoring. Reproduce with
`evaluation/score_coverage.py`.

| | published | after fixes | hand-written |
| --- | --- | --- | --- |
| fabricated or unsupported claims | **15** | **0** | 0 |
| garble printed as a quoted product | 9 | 0 | 0 |
| completed sales called unavailable | 3 | 0 | 0 |
| coverage of the 40 hand-identified findings | ~9 | **14 (35%)** | 40 |

**Truthfulness is solved. Recall is not.** The log no longer invents money
problems, no longer prints mic noise as product names, and no longer copies
its own instructions into the record. It reaches about a third of what a
careful reader finds in the same input.

## The three sequencing bugs

Every one of these was the right mechanism in the wrong place, and none was
visible in the code - only in the output:

1. **Gates ran on bullets only.** Half the log - the narrative - was
   unguarded for months.
2. **Carry-through ran before the gates.** The draft still had full bullet
   lists, so the top-up declined; the gates then pruned those bullets and
   nothing filled the gap. It fired zero times across three runs while
   appearing to work.
3. **Ranking ran before filtering.** A fabricated "$2.50 register shortfall"
   scores higher than "ordered 2 cases of tins" (money + the word register),
   so phantoms took every slot and were deleted immediately afterwards.
   Coverage went 30% -> 20% before this was found.

## What the notes probe established

Dumping the tagged notes - rather than reading logs - settled where recall is
lost. For 2026-07-19 the notes pass had already found the day's real event:

```
[PROBLEM] The register had a $70 discrepancy, likely from an order that
          didn't go through but was charged twice, needing reconciliation
```

It was then deleted downstream by the discrepancy gate, because
`_DISCREPANCY_SPEECH` knew only till vocabulary and the staff had said "we're
over today we charged people $70 more". **The gate built to stop invented
shortfalls was deleting the only real one in four days.**

The same probe caught a `[PROMISE]` note carrying a customer's email address,
headed for Unresolved verbatim. No log had shown it, because no log had got
that far.

## Two mistakes worth keeping on the record

- The first vagueness gate tested for the ABSENCE of specificity and deleted
  the saffron request, the label-printer fault and the exposed-wiring hazard.
  Equipment, supplies and products we do NOT stock are ordinary lowercase
  nouns with no catalog entry - so an unstocked product having no catalog
  match, the definition of unmet demand, read as vagueness.
- `_MONEY_RE` used `[\d,]+`, which captured `"70,"` from `"$70, possibly"`.
  The gate then searched the transcript for `70,` and never found it, marking
  genuinely spoken amounts unsupported. Silent for the whole session; found
  while chasing an unrelated duplicate.

## Where the remaining gap is

The notes contain the findings. The merge drops them, the ranking recovers
some, and the rest are lost to slot competition against a 5-bullet cap. The
next honest step is not another gate: it is that a day with fifteen real
findings cannot report them in ten bullets, and either the format grows or
the selection has to get much better at ranking importance rather than
searchability.
