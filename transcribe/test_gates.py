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


print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for f in FAILURES:
        print("  -", f)
    sys.exit(1)
print("all gate tests passed")
