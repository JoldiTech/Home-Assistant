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

# ...but an ASSERTED unavailability makes the product the subject, so distance
# stops mattering. 2026-07-26 shipped three of these for products the register
# rang up, each name sitting just outside the 60-char window.
FAR = ("Jason asked for marshmallow root and requested a pound of marshmallow "
       "roux, but it was unavailable\n")
BIZ2 = {"sales": {"orders": [{"items": [{"name": "Marshmallow Root - Organic"},
                                        {"name": "Gift Card"}]}]}}
check("a sold product asserted unavailable is dropped, however far away",
      has(P._reject_sold_reorders(FAR, BIZ2), "marshmallow"), False)

# ...but a SUBSTITUTE offered after the marker is in stock on purpose. This is
# what the sentence is telling you, and searching past the marker deleted a
# genuine unmet-demand finding because the substitute had sold that day.
BIZ3 = {"sales": {"orders": [{"items": [{"name": "Cinnamon Chips - Organic"}]}]}}
SUB = "Customer asked for cinnamon sticks but we only have cinnamon chips\n"
check("a substitution offer is not an unavailability claim about the substitute",
      has(P._reject_sold_reorders(SUB, BIZ3), "cinnamon sticks"), True)


# --- garble, quotes and non-events --------------------------------------------
print("\ngarble / quoting / non-events")

out = P._drop_garble_claims(
    'A customer asked for "the 16 ounces of the Nahili mill jury and cleaner" '
    'but it was unavailable.\n')
check("long garbled quote drops the claim", has(out, "nahili"), False)

out = P._drop_garble_claims(
    '- Reorder "Earl Grey - Organic" and "rye, brunckel, strawberry black, mott"\n')
check("one garbled quote sinks a mixed bullet", has(out, "earl grey"), False)

# The same garble with the quotes removed. The gate had been reading
# punctuation rather than content: 2026-07-26 re-shipped this verbatim the
# moment the model stopped quoting it.
CATALOG = [{"name": "Earl Grey - Organic"}, {"name": "Sandia Spice"}]
UNQUOTED = "- A customer asked for a 16-ounce Nahili mill jury and cleaner but it wasn't available\n"
check("unquoted garble is caught too",
      has(P._drop_garble_claims(UNQUOTED, CATALOG), "nahili"), False)

# A long request naming real products is an order, not noise.
LONG_REAL = ("- A customer asked for a pound of Earl Grey - Organic and two "
             "ounces of Sandia Spice but neither was available\n")
check("a long request naming catalog products survives",
      has(P._drop_garble_claims(LONG_REAL, CATALOG), "earl grey"), True)

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
         "## Unresolved\n- Chase the unpaid invoice from the tea supplier\n\n## Worth remembering\n- A customer asked for a size we do not carry\n")
out = P._rebuild_bullet_sections(DRAFT, NOTES, [])
check("dropped PROBLEM carried into Unresolved", has(out, "not transferring calls"), True)
check("second PROBLEM carried too", has(out, "red basket"), True)
check("PROMISE carried into Unresolved", has(out, "sponsorship request"), True)
check("UNMET carried into Worth remembering", has(out, "cinnamon sticks"), True)
check("FEEDBACK carried into Worth remembering", has(out, "witch's broom"), True)
check("TRAFFIC is not carried as a bullet", has(out, "farmers market"), False)
check("CAUSE already in the draft is not duplicated",
      out.lower().count("brought people in off the street"), 1)
check("the model's own bullets are kept", has(out, "unpaid invoice"), True)

# Carried lines land in the right sections, not appended at the end.
uns = out.split("## Unresolved")[1].split("## Worth")[0]
check("carried problems are under Unresolved", has(uns, "red basket"), True)
check("...and unmet demand is not", has(uns, "cinnamon sticks"), False)

# A full section is left alone rather than overfilled.
FULL = ("# Log\n\nBody.\n\n## Unresolved\n- a1\n- a2\n- a3\n- a4\n- a5\n\n"
        "## Worth remembering\n- b1\n")
out = P._rebuild_bullet_sections(FULL, NOTES, [])
check("a strong note outranks weak draft bullets", has(out, "not transferring calls"), True)
check("...and the other section is filled too", has(out, "cinnamon sticks"), True)

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
out = P._rebuild_bullet_sections("# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n", WILD, [])
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
out = P._rebuild_bullet_sections(DRAFT2, DUP, [])
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



# --- a drawer that is OVER ----------------------------------------------------
print("\nover-the-drawer discrepancies")

# 2026-07-19's real event, in the words staff actually used. The notes pass
# found it; the gate deleted it, because "over" was not till vocabulary and a
# bare $70 was required to share a line with the report.
SPEECH_OVER = "\n".join([
    "Okay so what is the total?",
    "Yesterday we were off by seven.",
    "No idea what happened.",
    "Like over that we're over today we charged people $70 more so the drawer "
    "is $70 more than this.",
    "Maybe we charged someone an extra 700 bucks.",
    "We have to talk to her about it tomorrow."])
out = P._reject_unsupported_discrepancies(
    "## Unresolved\n- Reconcile the $70 the drawer came up over; a card sale may "
    "have charged a customer twice\n", SPEECH_OVER)
check("a drawer that is OVER survives", has(out, "$70"), True)

# ...without reopening the hole: an unrelated bare figure far from any report
# is still rejected.
SPEECH_FAR = "\n".join(
    ["That will be 47 today.", "Thanks, have a good one."] + ["Chat."] * 8 +
    ["Okay the drawer does not match."])
out = P._reject_unsupported_discrepancies(
    "## Unresolved\n- Reconcile the discrepancy of $47\n", SPEECH_FAR)
check("...an unrelated bare figure is still dropped", has(out, "$47"), False)



# --- which way was the drawer wrong -------------------------------------------
print("\ndiscrepancy direction / stray sections")

out = P._fix_discrepancy_direction(
    "- Investigate unexplained register shortfall of $70\n", SPEECH_OVER)
check("an OVER drawer is not called a shortfall", has(out, "shortfall"), False)
check("...it is called an overage", has(out, "overage"), True)
check("...and the amount is untouched", has(out, "$70"), True)

# A genuine shortfall keeps its wording.
SPEECH_SHORT = "\n".join([
    "Let us count it again.", "We came up short by $70 today.", "No idea why."])
out = P._fix_discrepancy_direction(
    "- Investigate unexplained register shortfall of $70\n", SPEECH_SHORT)
check("a real shortfall is left alone", has(out, "shortfall"), True)

out = P._drop_stray_sections(
    "# Log\n\nBody.\n\n## Unresolved\n- a\n\n## Annotated\n- a\n\n"
    "## Worth remembering\n- b\n")
check("stray section dropped", has(out, "## Annotated"), False)
check("...its duplicated bullet goes with it", out.count("- a"), 1)
check("...the real sections survive",
      has(out, "## Unresolved") and has(out, "## Worth remembering"), True)



# --- ranking is the quality control -------------------------------------------
print("\nbullet ranking")

RANK_NOTES = ["""- [PROBLEM] The register had a $70 discrepancy, likely an order that did not go through but was charged twice
- [PROBLEM] A customer reported an issue with Spotify where music stops when it rains
- [UNMET] A customer asked for saffron, which we don't carry
- [UNMET] A customer asked for a product but the exact item is unclear
- [PROBLEM] Something happened
- [FEEDBACK] Customer praised the Sandia Spice for its warmth"""]
RANK_PRODUCTS = [{"name": "Sandia Spice"}]
DRAFTR = "# Log\n\nBody.\n\n## Unresolved\n- Chase it up\n\n## Worth remembering\n- Something nice\n"
out = P._rebuild_bullet_sections(DRAFTR, RANK_NOTES, RANK_PRODUCTS, want=3)
uns = out.split("## Unresolved")[1].split("## Worth")[0]
check("the money finding outranks the noise", has(uns, "$70"), True)
check("a bullet that names nothing loses its slot", has(uns, "something happened"), False)
wr = out.split("## Worth remembering")[1]
check("a real product outranks a placeholder", has(wr, "sandia spice"), True)
check("...and the placeholder is cut", has(wr, "exact item is unclear"), False)
check("saffron - a product we do NOT stock - still ranks", has(wr, "saffron"), True)



# --- what earns a slot --------------------------------------------------------
print("\nslot competition")

# 2026-07-19: five pickup-notification restatements held the whole Unresolved
# section while the day's real findings sat unused in the notes.
REAL_NOTES = ["""- [UNMET] A customer asked for saffron, which we don't carry
- [PROBLEM] The label printer was on the wrong settings and misprinted labels
- [PROMISE] Staff promised to call the customer back about a special order"""]
RESTATE_DRAFT = ("# Log\n\nBody.\n\n## Unresolved\n"
                 "- Send email details for Josh's pickup order\n"
                 "- Send email details for Melo's pickup order\n"
                 "- Josh's order is ready for pickup at NM Tea Co.\n\n"
                 "## Worth remembering\n- Customer texts confirming order readiness\n")
out = P._rebuild_bullet_sections(RESTATE_DRAFT, REAL_NOTES, [], want=3)
uns = out.split("## Unresolved")[1].split("## Worth")[0]
check("the label printer beats a pickup restatement", has(uns, "label printer"), True)
check("the callback promise beats one too", has(uns, "call the customer back"), True)
check("pickup-email restatements lose their slots", has(uns, "send email details"), False)
wr = out.split("## Worth remembering")[1]
check("saffron - carries no number and no catalog match - still wins a slot",
      has(wr, "saffron"), True)
check("a restatement does not ship even into an empty slot",
      has(wr, "texts confirming order readiness"), False)

# A draft bullet that earned a record reference keeps its edge.
ANN = ("# Log\n\nBody.\n\n## Unresolved\n"
       "- Reconcile the shortfall (likely order #58212, $43.50 at 2:14pm)\n\n"
       "## Worth remembering\n- x\n")
out = P._rebuild_bullet_sections(ANN, REAL_NOTES, [], want=1)
check("an annotated draft bullet keeps its slot", has(out, "#58212"), True)



# --- one problem, one bullet --------------------------------------------------
print("\nsame-claim dedupe / amount-less discrepancies")

check("two bullets citing the same amount are one claim",
      P._same_claim("Investigate unexplained register overage of $70",
                    "The register had an unexplained overage of $70, possibly "
                    "from an order that didn't go through"), True)
check("different amounts stay separate",
      P._same_claim("Reconcile the $70 overage", "Reconcile the $12.35 shortfall"), False)
check("the same record id is one claim",
      P._same_claim("Chase ticket #3128", "Reply to #3128 about sponsorship"), True)

out = P._reject_unsupported_discrepancies(
    "## Unresolved\n"
    "- A register shortfall was noted during the shift, though no cause was identified\n"
    "- Reconcile the register discrepancy of $37.66\n", SPEECH_REAL)
check("an amount-less discrepancy is dropped", has(out, "no cause was identified"), False)
check("...the one with a figure survives", has(out, "$37.66"), True)



# --- filter before ranking ----------------------------------------------------
print("\nfilter-before-rank")

# 2026-07-26: six phantom discrepancies scored higher than every real finding
# (money +2 and the word "register" +3), took the slots, were deleted by the
# gates afterwards, and the section shipped with two bullets. Coverage DROPPED.
PHANTOMS = ["""- [PROBLEM] Reconcile the register shortfall of $2.50 at closing
- [PROBLEM] The register had a $2.14 discrepancy that needs reconciliation
- [PROBLEM] register discrepancy of $0.75 unaccounted for
- [PROBLEM] Phones at Counter not working, calls to the warehouse went unanswered
- [PROBLEM] The fridge door was being held open by a red basket"""]
DRAFT3 = "# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n"

# Without the filter the fakes crowd out the real findings at rank time.
loose = P._rebuild_bullet_sections(DRAFT3, PHANTOMS, [], want=3)
check("unfiltered, the phantoms take the slots", has(loose, "$2.50"), True)

# With the gate applied first, only survivors compete.
reject_money = lambda t: "$" not in t          # stand-in for the money gates
tight = P._rebuild_bullet_sections(DRAFT3, PHANTOMS, [], want=3, keep=reject_money)
check("filtered, the phantoms never compete", has(tight, "$2.50"), False)
check("...and the real findings get the slots", has(tight, "phones at counter"), True)
check("...both of them", has(tight, "red basket"), True)



# --- the two sections are read differently ------------------------------------
print("\nper-section caps")

# Distinct wording on purpose: seven formulaic "the X is not working" lines
# share trigrams and correctly collapse to one, which says more about the
# fixture than the cap.
MANY = ["""- [PROBLEM] The label printer keeps reverting to settings that misprint
- [PROBLEM] The fridge door will not shut with the red basket in the way
- [PROBLEM] Phones at the counter no longer transfer through to the warehouse
- [PROBLEM] One payment terminal rejects cards until it is unplugged
- [PROBLEM] Heavy insta-chai boxes are shelved too high to lift down safely
- [PROBLEM] The remote needs new batteries; lights take four presses
- [PROBLEM] Exposed wiring behind the wall calendar was flagged as a fire risk
- [UNMET] A customer asked for saffron, which we don't carry
- [UNMET] Cinnamon sticks were requested; only chips are stocked
- [UNMET] Vietnam black tea is out with no restock date to give a regular
- [UNMET] Someone wanted a fruit tisane containing chamomile; nothing matches
- [UNMET] Milk oolong was asked for and we had none left
- [UNMET] A roasted yaupon was requested after a tasting elsewhere
- [UNMET] Bergamot rooibos came up twice and is not in the range
- [UNMET] Green hojicha is no longer sourceable from that supplier"""]
EMPTY = "# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n"
out = P._rebuild_bullet_sections(EMPTY, MANY, [])
uns = out.split("## Unresolved")[1].split("## Worth")[0]
wr = out.split("## Worth remembering")[1]
check("tomorrow's to-do list stays short",
      uns.count("- "), P.SECTION_CAPS["## Unresolved"])
check("the searchable archive is allowed to be long",
      wr.count("- ") > P.SECTION_CAPS["## Unresolved"], True)
check("...but still bounded",
      wr.count("- ") <= P.SECTION_CAPS["## Worth remembering"], True)

# The cap must not resurrect padding: a quiet day still ends up short.
THIN = ["- [UNMET] A customer asked for saffron, which we don't carry"]
out = P._rebuild_bullet_sections(EMPTY, THIN, [])
check("a quiet day stays quiet", out.count("- "), 1)



# --- model re-ranking ---------------------------------------------------------
# The ranker is OFF in production - it was measured against the four audited
# days and lost to plain keyword order (27% vs 35% of hand-identified findings
# covered). These tests cover its MECHANICS, which still have to be sound for
# anyone who re-tests it with SUMMARIZER_RANK=1, so they turn it on explicitly
# rather than depending on the default.
print("\nllm re-ranking")
P.SUMMARIZER_RANK = True

CANDS = [(f"candidate {i}", 0, "note", "audio") for i in range(6)]

# The model can only ever return INDICES into a list it was given, so nothing
# new can enter the log however badly it answers.
out = P._llm_rank(CANDS, "## Unresolved", 3, lambda u: "4, 2, 6")
check("model order is honoured", [t for t, *_ in out[:3]],
      ["candidate 3", "candidate 1", "candidate 5"])
check("unranked candidates keep their place behind", len(out), len(CANDS))

check("out-of-range indices are ignored",
      [t for t, *_ in P._llm_rank(CANDS, "## X", 3, lambda u: "99, 1, 0, -4")[:1]],
      ["candidate 0"])
check("a duplicate index is not repeated",
      len(P._llm_rank(CANDS, "## X", 3, lambda u: "2, 2, 2")), len(CANDS))
check("garbage reply falls back to keyword order",
      [t for t, *_ in P._llm_rank(CANDS, "## X", 3, lambda u: "no idea")],
      [t for t, *_ in CANDS])
def boom(u):
    raise RuntimeError("model died")
check("a failed rank pass never fails the run",
      [t for t, *_ in P._llm_rank(CANDS, "## X", 3, boom)],
      [t for t, *_ in CANDS])
check("no ranker at all is fine", P._llm_rank(CANDS, "## X", 3, None), CANDS)



# --- padding from thin slices -------------------------------------------------
print("\nabsence-of-record padding")

# Smaller notes slices ask for up to eight bullets from a slice that may hold
# almost nothing, and the model pads with what it did NOT find - the same way
# Whisper fills silence. All four of these shipped on 2026-07-19.
for pad in ["No unmet demand recorded in this time slice",
            "No mention of storage issues or stock movement in the provided business records",
            "No direct feedback on products or services received from the ticket or texts",
            "No specific feedback noted"]:
    check(f"padding dropped: {pad[:44]}",
          has(P._drop_non_events(f"## Unresolved\n- {pad}\n"), pad[:20].lower()), False)

# The half that matters: an absence in the WORLD is often the finding itself.
# A rule keyed on "starts with no" would delete all of these.
for real in ["No one has picked up the package mailed June 13",
             "No small bills left in the drawer after the afternoon rush",
             "Nobody could find the spare key for the back gate",
             "The Vietnam black tea is out with no restock date from the supplier"]:
    check(f"real finding kept: {real[:44]}",
          has(P._drop_non_events(f"## Unresolved\n- {real}\n"), real[:20].lower()), True)

# The log is about the shop, never about its own inputs - whatever the phrasing.
check("a bullet about the pipeline's input is dropped",
      has(P._drop_non_events(
          "## Unresolved\n- Staff discussed restocking in this time slice\n"),
          "restocking"), False)


# --- the rebuild must not depend on the merge ---------------------------------
print("\nrebuild when the merge drops a heading")

# An overloaded merge prompt made the 8B emit a narrative and nothing else.
# The rebuild skipped both sections because neither heading was there, and
# 2026-07-19 shipped with zero bullets out of 280 notes. The merge is the
# stage least to be trusted; the sections cannot be conditional on it.
NARRATIVE_ONLY = "# Captain's Log — Sunday 2026-07-19\n\nThe day was busy.\n"
FINDINGS = ["""- [PROBLEM] The label printer keeps reverting to settings that misprint
- [UNMET] A customer asked for saffron, which we don't carry"""]
out = P._rebuild_bullet_sections(NARRATIVE_ONLY, FINDINGS, [])
check("a missing Unresolved heading is created", has(out, "## Unresolved"), True)
check("a missing Worth remembering heading is created",
      has(out, "## Worth remembering"), True)
check("...and the notes reach it", has(out, "label printer"), True)
check("...without disturbing the narrative", has(out, "the day was busy"), True)


# --- notes must fit the merge prompt ------------------------------------------
print("\nnotes trimming for the merge")

# Smaller notes slices mean more note blocks, and the merge prompt has a budget
# the notes pass does not. Trimming is fine; trimming the RECORDS block is the
# exact failure this area keeps having, so it is never the one dropped.
tok = lambda s: len(s.split())                    # words stand in for tokens
SIDE = ["records block " * 10]
AUDIO = [f"audio block {i} " * 10 for i in range(9)]

check("notes that fit are passed through whole",
      P._fit_notes(tok, SIDE + AUDIO, 1, 10_000), "\n".join(SIDE + AUDIO))

# budget chosen to admit several audio blocks, so "spans the day" is testable
# at all - at a budget that fits only one, any implementation looks the same.
out = P._fit_notes(tok, SIDE + AUDIO, 1, 140)
check("over budget, the result fits", tok(out) <= 140, True)
check("...the records block survives the trim", has(out, "records block"), True)
check("...some audio is dropped", tok(out) < tok("\n".join(SIDE + AUDIO)), True)
# Thinned, not truncated. Truncating would end the narrative mid-afternoon on
# a long day and never say so, which is a worse failure than losing detail.
check("...the day still reaches its end", has(out, "audio block 8"), True)
check("...and still starts at the beginning", has(out, "audio block 0"), True)

# A budget too small for even the records block must not loop forever or
# return nothing - the records block is the floor and is never traded away.
out = P._fit_notes(tok, SIDE + AUDIO, 1, 1)
check("an impossible budget still returns the records block", out, SIDE[0])
check("...and no audio rides along with it", has(out, "audio block"), False)
check("no records block means no special case",
      P._fit_notes(tok, AUDIO, 0, 1), AUDIO[0])
check("empty notes are fine", P._fit_notes(tok, [], 0, 10), "")


# --- records get reserved slots -----------------------------------------------
print("\nrecords reservation")

# A long day is ~10 audio slices of notes against ONE records block, so
# findings somebody actually wrote down are a few percent of the pool and get
# buried by volume. 2026-07-19 shipped twelve "Worth remembering" bullets, ten
# of them customers complimenting a tea, while the exposed wiring and the
# uncollected pickup order - both written down - never appeared.
# The fixture has to reproduce BOTH halves of the real burial or it proves
# nothing. Same tag on both sides, so they compete for one section. Each audio
# line a DIFFERENT problem, or twenty rephrasings collapse under _same_claim
# and leave the section roomy. And each audio line has to genuinely OUTSCORE
# the records one - which is what happens in production, because floor chatter
# names catalog products and carries amounts while "exposed wiring behind the
# calendar" names nothing the scorer can see.
_THINGS = ["kettle", "grinder", "scale", "chalkboard", "awning", "door chime",
           "sink tap", "shelf bracket", "till drawer", "card reader",
           "sample tray", "window blind", "step stool", "wall clock",
           "floor mat", "spice rack", "tea timer", "stock ladder",
           "back gate", "ceiling fan"]
_BLENDS = ["Assam Gold", "Dragon Pearl", "Jasmine Silver", "Rooibos Vanilla",
           "Hibiscus Cooler", "Genmaicha Toast", "Oolong Milk", "Sencha Spring",
           "Pu-erh Ripe", "Chamomile Dream", "Peppermint Snap", "Lemon Verbena",
           "Ginger Fire", "Turmeric Glow", "Lavender Fields", "Rose Congou",
           "Matcha Ceremony", "Barley Roast", "Yerba Clean", "Honeybush Warm"]
CATALOG_ONE = [{"name": b} for b in _BLENDS]
# distinct product per line, so they neither collapse under _same_claim nor
# tie on score - each names a catalog product, an amount and a record id
CHATTER = [f"- [PROBLEM] The {b} {t} is broken, $85 to replace (order #{400 + i})"
           for i, (b, t) in enumerate(zip(_BLENDS, _THINGS))]
RECORD = ["- [PROBLEM] Exposed wiring behind the wall calendar in the back room"]
BARE = "# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n"

# The reordering itself, tested directly. Going through _rebuild_bullet_sections
# for this could not be made to fail without the fix, because _same_claim
# collapses same-shaped audio lines and leaves the section roomy - the existing
# machinery already protects diversity better than expected. That is worth
# recording rather than working around: the reservation earns its place on the
# four-day measurement, not on a fixture rigged until it agrees.
LOW = ("Exposed wiring behind the wall calendar", 1, "note", "records")
HIGH = [(f"The {b} display is broken, $85 (order #{400+i})", 10, "note", "audio")
        for i, b in enumerate(_BLENDS)]
out = P._reserve_for_records(HIGH + [LOW], "## Unresolved")
check("a records candidate is pulled to the front", out[0][0], LOW[0])
check("...and nothing else is lost or duplicated", len(out), len(HIGH) + 1)
check("...with the rest still in score order", [c[0] for c in out[1:]],
      [c[0] for c in HIGH])

# Never more than the floor, however many records candidates there are.
MANY_REC = [(f"Records finding {i}", 1, "note", "records") for i in range(9)]
out = P._reserve_for_records(HIGH + MANY_REC, "## Unresolved")
check("the floor is a ceiling on the reservation",
      [c[3] for c in out[:3]], ["records", "records", "audio"])

# All-audio must be returned untouched - no reordering, no cost.
allaudio = list(HIGH)
check("an all-audio list is passed straight through",
      P._reserve_for_records(allaudio, "## Unresolved"), allaudio)

# The reservation is a ceiling, not a quota: it must never pad a section with
# records notes that are not there, and never invent a slot.
out = P._rebuild_bullet_sections(BARE, CHATTER, [], n_side=0)
check("no records notes reserves nothing", has(out, "is broken"), True)

# Reordering only - a reserved candidate is still subject to every gate,
# because the reservation happens after `keep` has already filtered.
out = P._rebuild_bullet_sections(BARE, RECORD + CHATTER, [], n_side=1,
                                 keep=lambda t: "wiring" not in t.lower())
check("a reserved candidate is not exempt from the gates",
      has(out, "exposed wiring"), False)


# --- the floor must not terminate a reordered list ----------------------------
print("\nfloor vs reordering")

# A reordered list is not sorted by keyword score, so a low scorer early in it
# must be SKIPPED, not treated as the end of the queue. 2026-07-19 shipped an
# empty Unresolved section out of 31 surviving candidates because of this.
MIXED = ["""- [PROBLEM] Send email details for the pickup order
- [PROBLEM] The label printer keeps reverting to settings that misprint
- [PROBLEM] Phones at the counter no longer reach the warehouse"""]
EMPTY2 = "# Log\n\nBody.\n\n## Unresolved\n\n## Worth remembering\n"
# put the restatement (scores below the floor) first, as a ranker might
out = P._rebuild_bullet_sections(EMPTY2, MIXED, [], rank=lambda u: "1, 2, 3")
check("a below-floor line does not end the queue", has(out, "label printer"), True)
check("...the second good one survives too", has(out, "phones at the counter"), True)
check("...and the below-floor line still does not ship",
      has(out, "send email details"), False)


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("all gate tests passed")
