#!/usr/bin/env python3
"""Self-contained regression tests for the Captain's Log grounding gates.

Every case here is a real failure that shipped to the `captains-log` branch,
reduced to a synthetic fixture (no customer data, so this file is committable
and runs anywhere - `python3 transcribe/test_gates.py`, no venv, no GPU, no
box). Fixtures are paraphrased; the failure shapes are exact.

Both halves matter. A gate that deletes fabrications is worthless if it also
deletes real findings, so each block asserts a KEEP alongside its DROP - the
sold-today gate in particular ate a genuine gift-card observation the first
time it was widened to narrative, because "Gift Card" is a register line item.
"""
import os
import re
import sys
import types

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captains_pipeline.py")

for _name in ("llama_cpp", "uiprotect", "transcribe_day"):        # heavy deps
    _m = types.ModuleType(_name)
    _m.Llama = object
    sys.modules.setdefault(_name, _m)

P = types.ModuleType("captains_pipeline_under_test")
P.__dict__["__file__"] = SRC
exec(compile(open(SRC).read(), SRC, "exec"), P.__dict__)

FAILURES = []


def check(name, got, want, note=""):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILURES.append(f"{name}: expected {want}, got {got}. {note}")


def has(md, needle):
    return needle.lower() in md.lower()


# --- discrepancies ------------------------------------------------------------
print("\ndiscrepancy gate")

# A sale total read to a customer, hours from any till talk. Shipped three times
# on 2026-07-19 as "an unexplained shortfall of $37.66".
SPEECH_SALE_ONLY = "\n".join(
    ["Hi, welcome in.", "Two ounces of the breakfast blend please.",
     "It's going to be $37.66.", "Did you want a receipt?", "Have a good one."]
    + ["Thanks, bye."] * 40)

md = ("Report.\n\nThe register had an unexplained shortfall of $37.66.\n\n"
      "## Unresolved\n- Reconcile the register shortfall of $37.66\n")
out = P._reject_unsupported_discrepancies(md, SPEECH_SALE_ONLY)
check("sale total is not a shortfall (narrative)", has(out, "shortfall of $37.66"), False)
check("sale total is not a shortfall (bullet)", has(out, "reconcile the register"), False)

# The same amount, this time next to somebody actually reporting the drawer off.
SPEECH_REAL = "\n".join([
    "Okay let's count the drawer.", "Adding up, yesterday we were off by seven.",
    "The drawer is $37.66 more than this.", "So we might have charged someone twice.",
    "We have to talk to her about it tomorrow."])
out = P._reject_unsupported_discrepancies(
    "## Unresolved\n- Reconcile the register discrepancy of $37.66\n", SPEECH_REAL)
check("a real reported discrepancy survives", has(out, "$37.66"), True)

# Amount invented outright - it is nowhere in the day. This is 2026-07-26's
# "$23.50", which came from the POS FORMAT EXAMPLE in the system prompt.
out = P._reject_unsupported_discrepancies(
    "A discrepancy in register: $23.50 unaccounted for.\n", SPEECH_SALE_ONLY)
check("invented amount dropped from narrative", has(out, "23.50"), False)

# A correctly annotated real discrepancy must not fail on its own annotation.
out = P._reject_unsupported_discrepancies(
    "## Unresolved\n- Reconcile the discrepancy of $37.66 (likely order #331, $41.20 at 2:14pm)\n",
    SPEECH_REAL)
check("record annotation is not treated as spoken money", has(out, "$37.66"), True)


# --- sold today ---------------------------------------------------------------
print("\nsold-today gate")

BIZ = {"sales": {"orders": [{"items": [
    {"name": "Sandia Spice"}, {"name": "Immune Support - Organic"},
    {"name": "Gift Card"}, {"name": "Mediterranean Mint | Bulk"}]}]}}

# 2026-07-26 shipped both of these in prose; the POS lines proving the sales
# were woven into the model's own input.
out = P._reject_sold_reorders(
    "A customer asked for two ounces of Sandia spice and one immune support "
    "blend, but neither were available.\n", BIZ)
check("'neither were available' of a sold product", has(out, "sandia"), False)

out = P._reject_sold_reorders("## Unresolved\n- Reorder Mediterranean Mint\n", BIZ)
check("reorder of a product that sold today", has(out, "mediterranean"), False)

# The regression that widening this gate caused: a real finding about gift-card
# PROCESSING was deleted because "Gift Card" is also a line item.
keep = ("A customer expressed confusion about gift card processing, while staff "
        "discussed restocking pots from the warehouse.\n")
check("unrelated SKU far from the trigger is kept",
      has(P._reject_sold_reorders(keep, BIZ), "gift card processing"), True)


# --- garble, quotes and non-events --------------------------------------------
print("\ngarble / quoting / non-events")

out = P._drop_garble_claims(
    'A customer asked for "the 16 ounces of the Nahili mill jury and cleaner" '
    'but it was unavailable.\n')
check("long garbled quote drops the claim", has(out, "nahili"), False)

out = P._drop_garble_claims(
    '- Reorder "Earl Grey - Organic" and "rye, brunckel, strawberry black, mott"\n')
check("one garbled quote sinks a mixed bullet", has(out, "earl grey"), False)

# Short mis-hears are NOT garble by word count - they lose their quotes instead,
# which is what the policy actually asks for: keep the event, drop the false
# authority of a quoted product name.
PRODUCTS = [{"name": "Earl Grey - Organic"}, {"name": "Sandia Spice"}]
out = P._unquote_unverified(
    'A customer inquired about a "Cork option paper" and some "Earl Grey - Organic".\n',
    PRODUCTS)
check("unverified short name is unquoted", has(out, '"cork option paper"'), False)
check("...but the event survives", has(out, "cork option paper"), True)
check("a real catalog name keeps its quotes", has(out, '"Earl Grey - Organic"'), True)

out = P._drop_non_events(
    "The customer requested a refund for a $100 purchase but no action was taken.\n")
check("non-event dropped", has(out, "$100"), False)


# --- training role-play -------------------------------------------------------
print("\ntraining-artifact gate")

TRAINING_SPEECH = "\n".join([
    "Okay, so first I want to give you some fake orders.",
    "I want muxbond four M's.", "And bamboo camp two ounces.",
    "Great, now ring that up."] + ["Right."] * 10)
out = P._reject_training_artifacts(
    'A customer requested "muxbond four M\'s" but it was not available.\n', TRAINING_SPEECH)
check("practice order is not unmet demand", has(out, "muxbond"), False)

# A real request does not become unreal because someone practised nearby.
REAL_TOO = TRAINING_SPEECH + "\n" + "\n".join(["Later that day."] * 60) + \
    "\nCan I get muxbond four M's?"
out = P._reject_training_artifacts(
    'A customer requested "muxbond four M\'s" but it was not available.\n', REAL_TOO)
check("same name asked for outside training survives", has(out, "muxbond"), True)


# --- prompt echoes ------------------------------------------------------------
print("\nprompt-echo backstop")

# The invariant that matters is not "no quoted examples" - format guidance
# legitimately quotes itself - but "no example distinctive enough to be
# mistaken for a shop event". An entry here is a concrete noun sitting in a
# prompt, and it WILL end up in a log: that is how "XL gloves" reached all four
# days audited and how "$23.50" reached 2026-07-26.
check("no distinctive example content left in the prompts",
      sorted(P._prompt_echo_blocklist("an ordinary day of tea and customers")), [],
      "a concrete noun in a prompt will be copied into a log")

md = "## Unresolved\n- Reorder XL gloves\n- Reorder the packing tape\n"
out = P._reject_prompt_echoes(md, "we are out of packing tape again")
check("echo guard is inert with no examples to echo", has(out, "xl gloves"), True)

# Simulate a future concrete example being re-added to a prompt.
saved = P.SYSTEM_PROMPT
try:
    P.SYSTEM_PROMPT = saved + '\n  write "a staff request to restock XL gloves"\n'
    out = P._reject_prompt_echoes(md, "we are out of packing tape again")
    check("re-added example is blocked when absent from the day",
          has(out, "xl gloves"), False)
    check("...and the real item beside it survives", has(out, "packing tape"), True)
    out = P._reject_prompt_echoes(md, "can we order some XL gloves, mediums do not fit")
    check("...but not when the shop really did discuss it",
          has(out, "xl gloves"), True)
finally:
    P.SYSTEM_PROMPT = saved


# --- opener -------------------------------------------------------------------
print("\ngeneric opener")

out = P._fix_generic_opener(
    "# Captain's Log — Sunday\n\nA steady flow of customers entered the shop. "
    "The phones stopped reaching the warehouse at 10:39.\n")
check("banned opener cut", has(out, "steady flow"), False)
check("...the real opener promoted", has(out, "phones stopped"), True)

only = "# Captain's Log — Sunday\n\nA steady flow of customers entered the shop.\n"
check("never empties the paragraph", has(P._fix_generic_opener(only), "steady flow"), True)

# Cut the generic opener and the model's next move is to open on a sale. Every
# one of those numbers is in the stats block below and in a database forever.
BIZ_TOTALS = {"sales": {"orders": [{"total": "55.86", "items": []}]}}
out = P._fix_generic_opener(
    "# Captain's Log — Sunday\n\nThe day saw several high-value purchases, "
    "including a $55.86 order featuring Jasmine Pearls. The label printer was "
    "on the wrong settings all morning.\n", BIZ_TOTALS)
check("opener that just restates a register total is cut", has(out, "55.86"), False)
check("...the real opener promoted again", has(out, "label printer"), True)
out = P._fix_generic_opener(
    "# Captain's Log — Sunday\n\nA steady flow of customers. A $55.86 order went "
    "out. Rain broke the heat.\n", BIZ_TOTALS)
check("both banned shapes cut in sequence", has(out, "rain broke"), True)
check("...and neither survives", has(out, "steady flow") or has(out, "55.86"), False)


# --- credentials --------------------------------------------------------------
print("\ncredential gate")

out = P._drop_credentials(
    "## Unresolved\n- Address the store audio system and password access\n"
    "- Replace the label printer\n")
check("credential mention dropped", has(out, "password"), False)
check("...neighbouring item survives", has(out, "label printer"), True)
check("narrative credential mention dropped",
      has(P._drop_credentials("The till PIN code was shared aloud at the counter.\n"),
          "pin code"), False)

out = P._fix_generic_opener(
    "# Captain's Log — Sunday\n\nRain broke the heat and the morning filled up. "
    "Two ounces of matcha went out at noon.\n")
check("a concrete opener is left alone", has(out, "rain broke"), True)


# --- structure ----------------------------------------------------------------
print("\nclaim editing")

md = ("# Captain's Log — Sunday\n\nOne. Two. Three.\n\n## Unresolved\n- a\n- b\n")
out = P._edit_claims(md, lambda t, b: "test" if t.strip().startswith("Two") else None)
check("headings preserved", has(out, "## Unresolved"), True)
check("sibling sentences preserved", has(out, "One.") and has(out, "Three."), True)
check("only the rejected sentence goes", has(out, "Two."), False)
check("bullets untouched", has(out, "- a") and has(out, "- b"), True)

out = P._edit_claims("# H\n\nOnly one.\n\n## Unresolved\n- a\n", lambda t, b: "test")
check("emptied paragraph is removed, structure kept",
      has(out, "## Unresolved") and not has(out, "Only one"), True)




# --- overheard speech, name lists, empty sections -----------------------------
print("\novertalk / name lists / empty sections")

out = P._drop_overheard_quotes(
    "The customer had difficulty with the register, asking if it was fucking receipt. "
    "The label printer jammed twice.\n")
check("verbatim overheard speech dropped", has(out, "fucking"), False)
check("...neighbouring sentence survives", has(out, "label printer"), True)

BIZ_TEXTS = {"texts": {"messages": [
    {"time_local": "2026-07-23 10:45", "phone": "1", "direction": "outbound",
     "body": "Hi Deborah, your order is ready for pickup at NM Tea Co."},
    {"time_local": "2026-07-23 10:46", "phone": "2", "direction": "outbound",
     "body": "Hi Ashley, your order is ready for pickup at NM Tea Co."},
]}}
NAMED = ("The afternoon brought several pickup notifications sent to Deborah and Ashley. "
         "Deborah asked us to hold a second tin until Friday.\n")
out = P._drop_record_name_lists(NAMED, BIZ_TEXTS)
check("record name list dropped", has(out, "notifications sent to"), False)
check("one name on a real commitment survives", has(out, "hold a second tin"), True)

out = P._mark_empty_sections("# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n- a\n")
check("empty section marked", has(out, "_None._"), True)
check("...populated section untouched", out.count("_None._"), 1)



# --- vagueness ----------------------------------------------------------------
print("\nvagueness gate")

VP = [{"name": "Immune Support - Organic"}, {"name": "Earl Grey - Organic"}]
md = ("## Worth remembering\n"
      "- Specific feedback: customer expressed dissatisfaction with a product.\n"
      "- Unmet demand: a customer asked for clarification on a product but the exact item is unclear.\n"
      "- Feedback on Immune Support: a comment about a product.\n"
      "- Staff flagged a discrepancy of 47 cents in an item.\n")
out = P._drop_vague_bullets(md, VP)
check("placeholder subject is dropped", has(out, "dissatisfaction"), False)
check("...so is 'the exact item is unclear'", has(out, "clarification"), False)
check("...but a placeholder about a real product survives", has(out, "immune support"), True)
check("...and one carrying a number survives", has(out, "47 cents"), True)

# The regression this gate caused on a live run, now a permanent test. Every
# one of these fails an absence-of-specificity test; none is vague.
REAL = ("## Worth remembering\n"
        "- A customer asked about saffron, which we don't carry\n"
        "- Staff flagged that the label printer wasn't working correctly\n"
        "- Review the exposed wiring behind the wall calendar\n"
        "- Staff want LED strips installed on the shelves\n"
        "- Cinnamon sticks were asked for; we only stock chips\n")
kept = P._drop_vague_bullets(REAL, VP)
for probe in ("saffron", "label printer", "exposed wiring", "led strips", "cinnamon sticks"):
    check(f"real finding kept: {probe}", has(kept, probe), True)

# A bare whole-dollar figure is weak evidence and must sit ON the line where
# money is reported wrong. 2026-07-21 regenerated with "$47 in the register".
SPEECH_47 = "\n".join(
    ["That will be 47 today.", "Thanks, have a good one."] + ["Chat."] * 6 +
    ["Okay the drawer does not match."] + ["Chat."] * 6)
out = P._reject_unsupported_discrepancies(
    "## Unresolved\n- Reconcile the unresolved discrepancy of $47 in the register\n",
    SPEECH_47)
check("bare dollar amount far from the report is dropped", has(out, "$47"), False)
SPEECH_47_SAME = "The drawer does not match, we are off by 47 dollars."
out = P._reject_unsupported_discrepancies(
    "## Unresolved\n- Reconcile the discrepancy of $47 in the register\n", SPEECH_47_SAME)
check("...but survives on the same line as the report", has(out, "$47"), True)



# --- catalog rename guard -----------------------------------------------------
print("\ncatalog rename guard")

CAT = [{"name": "Ray's Cooler | Organic"}, {"name": "Bombilla - Stainless Steel | Bolt"}]
fixes, _review = P._match_catalog('A customer said the shop was "so cool".\n', CAT)
check("common phrase is not renamed into a product",
      any("Ray" in n for _q, n in fixes), False)

# The auto-fix tier pushing it to review is only half the guard: the review
# tier renamed it anyway on 2026-07-26. Both paths must refuse.
_fixes2, review2 = P._match_catalog('A customer said the shop was "so cool".\n', CAT)
annotated, _n = P._annotate_catalog('A customer said the shop was "so cool".\n', review2)
check("...and the review tier refuses it too", has(annotated, "Ray's Cooler"), False)
fixes, _review = P._match_catalog(
    'A customer asked about the "Bombila - Stainles Steel | Bolt".\n', CAT)
check("a real near-miss still gets corrected",
      any("Bombilla" in n for _q, n in fixes), True)

out = P._fix_generic_opener(
    "# Log\n\nThe shop experienced steady traffic today. The phones stopped "
    "reaching the warehouse.\n")
check("'the shop experienced steady traffic' is caught too",
      has(out, "experienced steady"), False)



# --- personal health ----------------------------------------------------------
print("\npersonal-health gate")

out = P._drop_personal_health(
    "- Feedback: a customer expressed concern about dental issues and tooth anxiety.\n"
    "- A customer asked about anti-inflammatory blends for arthritis.\n"
    "- Wanted something caffeine-free because of migraines; recommended rooibos.\n"
    "- The label printer was on the wrong settings.\n")
check("condition recorded for its own sake is dropped", has(out, "tooth anxiety"), False)
check("...a wellness shopping question survives", has(out, "anti-inflammatory"), True)
check("...so does a caffeine-free request", has(out, "migraines"), True)
check("...and unrelated items are untouched", has(out, "label printer"), True)



# --- carry-through ------------------------------------------------------------
print("\ncarry-through of dropped notes")

NOTES = ["""- [PROBLEM] Phones are not transferring calls to the warehouse
- [PROBLEM] The fridge door was being held open by a red basket
- [UNMET] Cinnamon sticks were asked for; only chips are stocked
- [TRAFFIC] Quiet until the farmers market let out
- [CAUSE] The rain brought people in off the street""",
"""- [PROMISE] Staff said they would call back about the sponsorship request
- [FEEDBACK] The new Witch's Broom batch tastes lighter than the last"""]

DRAFT = ("# Log\n\nThe rain brought people in off the street.\n\n"
         "## Unresolved\n- Chase the invoice\n\n## Worth remembering\n- a real thing\n")
out = P._carry_through_notes(DRAFT, NOTES)
check("dropped PROBLEM carried into Unresolved", has(out, "not transferring calls"), True)
check("second PROBLEM carried too", has(out, "red basket"), True)
check("PROMISE carried into Unresolved", has(out, "sponsorship request"), True)
check("UNMET carried into Worth remembering", has(out, "cinnamon sticks"), True)
check("FEEDBACK carried into Worth remembering", has(out, "witch's broom"), True)
check("TRAFFIC is not carried as a bullet", has(out, "farmers market"), False)
check("CAUSE already in the draft is not duplicated",
      out.lower().count("brought people in off the street"), 1)
check("the model's own bullets are kept", has(out, "chase the invoice"), True)

# Carried lines land in the right sections, not appended at the end.
uns = out.split("## Unresolved")[1].split("## Worth")[0]
check("carried problems are under Unresolved", has(uns, "red basket"), True)
check("...and unmet demand is not", has(uns, "cinnamon sticks"), False)

# A full section is left alone rather than overfilled.
FULL = ("# Log\n\nBody.\n\n## Unresolved\n- a1\n- a2\n- a3\n- a4\n- a5\n\n"
        "## Worth remembering\n- b1\n")
out = P._carry_through_notes(FULL, NOTES)
check("a full section is not overfilled", has(out, "not transferring calls"), False)
check("...while the other section still tops up", has(out, "cinnamon sticks"), True)

check("tags never reach the log",
      bool(P._NOTE_TAG_ANY_RE.search(P._NOTE_TAG_ANY_RE.sub("", out))), False)



# --- unknown tags and contact info --------------------------------------------
print("\nunknown tags / contact info")

# Given a closed list of six tags the model invents more. Discarding them threw
# away exactly what the carry-through exists to rescue.
WILD = ["""- [SUPPLY] Ordered 2 cases of 5oz gift tins
- [STORAGE] Matcha is now kept in the fridge, consuming space
- [SAFETY] Heavy stock shelved too high to take down safely
- [RHYTHM] Quiet until mid-morning"""]
out = P._carry_through_notes("# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n", WILD)
for probe in ("gift tins", "fridge", "shelved too high"):
    check(f"unknown tag still carried: {probe}", has(out, probe), True)
check("narrative-ish unknown tag is not a bullet", has(out, "quiet until"), False)
uns = out.split("## Unresolved")[1].split("## Worth")[0]
check("actionable unknown tags land in Unresolved", has(uns, "shelved too high"), True)

# The notes pass produced a customer email inside a [PROMISE] bullet, which the
# carry-through would have inserted verbatim.
out = P._scrub_contact_info(
    "- Ticket #3259 from Joy Mack and #3267 from sidhe7@juno.com pending check\n"
    "- Call the customer back on (505) 555-0142\n")
check("email scrubbed", has(out, "juno.com"), False)
check("phone scrubbed", has(out, "555-0142"), False)
check("...the open item survives", has(out, "#3259"), True)
check("...and so does the callback", has(out, "call the customer back"), True)

out = P._reject_sold_reorders(
    "Customer requested two ounces of Sandia spice and one immune support tea, "
    "neither available.\n", BIZ)
check("'neither available' without a copula is caught", has(out, "sandia"), False)



# --- staff names arriving via carried Slack notes ------------------------------
print("\nstaff attribution on carried notes")

STAFF = {"George", "George Quintero", "Shawn", "Verity"}
out = P._strip_staff_attribution(
    "- Reorder XL gloves as requested by George\n"
    "- The alarm now beeps in Shawns office instead of disabling\n"
    "- Restock the tins per George\n", STAFF)
check("'requested by <name>' de-identified", has(out, "by george"), False)
check("...the operational fact survives", has(out, "xl gloves"), True)
check("an office named after someone is generalised", has(out, "shawns office"), False)
check("...that fact survives too", has(out, "alarm now beeps"), True)

# One request must not arrive twice because the wordings differ.
DRAFT2 = "# Log\n\nBody.\n\n## Unresolved\n- Reorder XL gloves as requested by a staff member\n\n## Worth remembering\n"
DUP = ["- [PROBLEM] A staff member requested to reorder XL gloves as mediums no longer fit"]
out = P._carry_through_notes(DRAFT2, DUP)
check("a reworded restatement is not carried twice",
      out.lower().count("xl gloves"), 1)



# --- names we cannot enumerate ------------------------------------------------
print("\nunenumerated staff names / non-findings")

# Shawn did not work on 2026-07-23, so he was not in the staff set built from
# the timeclock and Slack display names - and "Shawns office" survived every
# named pattern.
out = P._strip_staff_attribution(
    "- The alarm now beeps in Shawns office instead of disabling\n", {"George"})
check("a room named after someone not on shift is still generalised",
      has(out, "shawns office"), False)
check("...the fact survives", has(out, "alarm now beeps"), True)

out = P._drop_non_events(
    "- No specific feedback noted from business records\n"
    "- Voicemail received (32 sec) but no content provided\n"
    "- Replace the damaged kettle\n")
check("'no feedback noted' dropped", has(out, "no specific feedback"), False)
check("'no content provided' dropped", has(out, "voicemail"), False)
check("...a real item survives", has(out, "damaged kettle"), True)


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("all gate tests passed")
