#!/usr/bin/env python3
"""Head-to-head: a regenerated Captain's Log vs the hand-written control.

Scores COVERAGE - how many of the findings identified by hand from the same
input the pipeline reaches. Fabrication counts are the other half of the
picture and are checked separately; a log can score zero fabrications while
covering a quarter of the day, which is exactly where this pipeline was.

Usage: put the regenerated logs beside this file as regen_YYYY-MM-DD.md
(DRY_RUN=1 output from captains_pipeline.py) and run it. The hand-written
controls are the .hand-written.md files in this directory; the probes below
are those findings reduced to regexes.

Write probes ORDER-INDEPENDENTLY. "overage of $70" puts "over" before the
number, and a probe of the form `70.*over` scored a correct bullet as a miss
- flattering the earlier version and penalising the later one. Use lookaheads
when a finding has two required parts.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
EVAL = "/home/user/Home-Assistant/captains_log/evaluation"

# The findings my hand-written log carried for each day, as probes.
FINDINGS = {
    "2026-07-19": [
        ("drawer $70 over / possible double-charge",
         r"(?=.*\b70\b)(?=.*(?:overage|over|charg))|double.?charg|charged twice"),
        ("exposed wiring behind the calendar", r"wir(e|ing)|faceplate|fire (pass|hazard)|electrical|calendar"),
        ("new hire's first shift / register training", r"new (hire|staff)|training|onboard|first shift"),
        ("store remote batteries dying", r"batter|remote"),
        ("pickup order dissolved / returned to warehouse", r"dissolv|returned to the warehouse|cancelled order"),
        ("saffron asked for, not carried", r"saffron"),
        ("label printer on wrong settings", r"label printer|printer.*setting"),
        ("someone forgot to clock out", r"clock out|clocked out|timecard"),
        ("paper canister question (ticket #3078)", r"canister|3078"),
    ],
    "2026-07-21": [
        ("greeting-card price conflict", r"card.*(price|pricing|\$5|\$6)|price.*card"),
        ("POS refund control broken", r"refund"),
        ("Vietnam black tea out indefinitely", r"vietnam"),
        ("sponsorship request (ticket #3128)", r"sponsor"),
        ("Insta Masala Chai restock notify (#3139)", r"insta.?masala|unsweetened"),
        ("package mailed June 13 uncollected", r"june 13|refused|uncollected|not picked up"),
        ("enamel blowing off the teapots", r"enamel"),
        ("old gift cards mis-linking accounts", r"gift card"),
        ("new hire register training", r"train"),
        ("phones / call did not ring through", r"phone|call.*(ring|through)"),
    ],
    "2026-07-23": [
        ("rain as the day's cause", r"rain"),
        ("Extra Sleepy Bear stronger than usual", r"sleepy bear|valerian"),
        ("Japanese festival vendor/demo request", r"festival|demonstration|japanese"),
        ("twig green tea no longer sourceable", r"source|twig|hojicha|kukicha"),
        ("matcha kits new supplier / feedback wanted", r"matcha kit|whisk|supplier"),
        ("newsletter opt-out kills order-ready email", r"newsletter|opt.?out|order.ready|email list"),
        ("drawer short of small bills / IOU / bank run", r"\biou\b|small bills|ones and fives|bank"),
        ("pot/spoon measuring scoop discontinued", r"measur|scoop|pot and spoon|tube"),
        ("XL gloves (real this day)", r"xl gloves"),
        ("help button in the back office", r"alarm|help button|beeps"),
    ],
    "2026-07-26": [
        ("phones not transferring to warehouse", r"phone"),
        ("buchu sold but not in the POS system", r"buchu|boochoo|rung up as|not in the system"),
        ("Grand Keemun still on pre-tariff price", r"keemun|tariff"),
        ("fridge wedged open / storage change", r"fridge|refrigerat"),
        ("heavy stock shelved unsafely high", r"heavy|unsafe|high shelf|insta.?chai"),
        ("chai split across three shelves", r"chai.*(shel|spread)|shelv"),
        ("Witch's Broom new batch differs", r"witch|broom|batch"),
        ("cinnamon sticks not carried", r"cinnamon"),
        ("fruit tisane with chamomile not stocked", r"chamomile"),
        ("roasted yaupon supplier went silent", r"yaupon|texas supplier"),
        ("144 gift tins ordered", r"tins?\b"),
    ],
}


def body(p):
    return open(p).read().split("## By the numbers")[0].lower()


tot_mine = tot_hit = 0
for d, probes in FINDINGS.items():
    pipe = f"{HERE}/regen_{d}.md"
    if not os.path.exists(pipe):
        print(f"{d}: no regenerated log")
        continue
    got = body(pipe)
    hits = [(n, bool(re.search(p, got))) for n, p in probes]
    n_hit = sum(1 for _, h in hits if h)
    tot_mine += len(hits)
    tot_hit += n_hit
    print(f"\n{'='*64}\n{d}  —  pipeline covers {n_hit}/{len(hits)} of the hand-written findings\n{'='*64}")
    for n, h in hits:
        print(f"  {'HIT ' if h else 'MISS'}  {n}")

print(f"\nTOTAL: {tot_hit}/{tot_mine} "
      f"({100*tot_hit//max(tot_mine,1)}%) of hand-written findings covered")
