#!/usr/bin/env python3
"""Store view of the DowntownControls dashboard: the Store State control and
the Master Controls confirmations (owner's request, 2026-10-11).

- Store State: the three coloured state tiles (Open/Closed/Armed) each carry a
  select-options feature (buttons style), so the state is set and shown in one
  place; the three separate Open/Closed/Armed buttons are removed. The state is
  status only: automations set it (store_open/store_close scripts, 15 min after
  close -> Armed, 09:00 Armed -> Closed) and nothing acts on it, by design
  (see the 2026-07-19 incident in CLAUDE.md).
- Master Controls: "Store Close" and "All Lights Off" ask for confirmation,
  because one tap blacks out the store.

Idempotent. Back up the storage file first (see print_agent/dashboard/
apply_store_panel.py for the command). Dry run by default; --apply saves.
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "print_agent", "dashboard"))
from apply_store_panel import DASHBOARD, call, connect  # noqa: E402

STATE = "input_select.store_state"
SELECT_FEATURE = [{"type": "select-options", "style": "buttons"}]
CONFIRM = {
    "Store Close": "Close the store? This turns off every light and the music.",
    "All Lights Off": "Turn off every light in the store?",
}


def store_state_cards(cards):
    out = []
    for c in cards:
        if c.get("type") == "button" and c.get("tap_action", {}).get("target", {}).get("entity_id") == STATE:
            continue  # the separate Open/Closed/Armed buttons
        if c.get("type") == "conditional" and c.get("card", {}).get("entity") == STATE:
            c = copy.deepcopy(c)
            c["card"]["features"] = SELECT_FEATURE
            c["card"]["features_position"] = "bottom"
        out.append(c)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    ws = connect()
    config = call(ws, 1, {"type": "lovelace/config", "url_path": DASHBOARD})
    view = next(v for v in config["views"] if v.get("title") == "Store")
    changed = []
    for section in view["sections"]:
        heading = (section.get("cards") or [{}])[0].get("heading")
        if heading == "Store State":
            new = store_state_cards(section["cards"])
            if new != section["cards"]:
                changed.append(f"Store State: {len(section['cards'])} -> {len(new)} cards")
                section["cards"] = new
        if heading == "Master Controls":
            for c in section["cards"]:
                text = CONFIRM.get(c.get("name"))
                if text and c.get("tap_action", {}).get("confirmation") != {"text": text}:
                    c.setdefault("tap_action", {})["confirmation"] = {"text": text}
                    changed.append(f"Master Controls: confirmation on '{c['name']}'")
    print("\n".join(changed) or "nothing to change")
    if changed and a.apply:
        call(ws, 2, {"type": "lovelace/config/save", "url_path": DASHBOARD, "config": config})
        print("saved")
    elif changed:
        print("dry run; pass --apply to save")
    return 0


if __name__ == "__main__":
    sys.exit(main())
