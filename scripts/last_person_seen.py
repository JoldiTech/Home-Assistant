#!/usr/bin/env python3
"""Report when a human was last seen on the Home Assistant cameras.

Uses the UniFi Protect "Person detected" AI binary sensors
(binary_sensor.*_person_detected). Requires two environment variables:

    HOMEASSISTANT_BASE_URL   e.g. https://ha.nmteaco.com
    HOMEASSISTANT_TOKEN      a long-lived access token

Examples
--------
    # Fast answer (one API call): who's on camera now / when last seen
    ./scripts/last_person_seen.py

    # Show the recent detection windows (movement path) over the last 3 hours
    ./scripts/last_person_seen.py --detail

    # Same, but look back 12 hours
    ./scripts/last_person_seen.py --detail 12

    # List every person-detection camera and its current state
    ./scripts/last_person_seen.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

# The store's local timezone (from HA /api/config: America/Denver).
LOCAL_TZ_NAME = os.environ.get("HA_TZ", "America/Denver")
PERSON_SUFFIX = "_person_detected"
INACTIVE_STATES = {"unavailable", "unknown", None, ""}


def _local_tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(LOCAL_TZ_NAME)
        except Exception:
            pass
    return timezone.utc


LOCAL_TZ = _local_tz()


def _api(path: str):
    base = os.environ.get("HOMEASSISTANT_BASE_URL")
    token = os.environ.get("HOMEASSISTANT_TOKEN")
    if not base or not token:
        sys.exit(
            "ERROR: set HOMEASSISTANT_BASE_URL and HOMEASSISTANT_TOKEN environment "
            "variables (they are provided by the session environment)."
        )
    url = base.rstrip("/") + path
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f"ERROR: HTTP {exc.code} from {path}: {exc.reason}")
    except urllib.error.URLError as exc:
        sys.exit(f"ERROR: could not reach {url}: {exc.reason}")


def _parse_ts(value: str) -> datetime:
    # HA returns ISO 8601 with an explicit offset, e.g. 2026-07-18T00:51:57.7+00:00
    return datetime.fromisoformat(value)


def _fmt_local(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).strftime("%A, %B %-d, %Y at %-I:%M:%S %p %Z")


def _fmt_clock(dt: datetime) -> str:
    return dt.astimezone(LOCAL_TZ).strftime("%-I:%M:%S %p")


def _ago(dt: datetime) -> str:
    delta = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return f"{secs} second{'s' if secs != 1 else ''} ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins / 60
    if hours < 48:
        return f"{hours:.1f} hours ago"
    return f"{hours / 24:.1f} days ago"


def _person_sensors(states):
    return [s for s in states if s["entity_id"].endswith(PERSON_SUFFIX)]


def _strip(name: str) -> str:
    # Friendly names end in " Person detected"; drop it for readable output.
    for suffix in (" Person detected", " person detected"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _name(state) -> str:
    raw = state.get("attributes", {}).get("friendly_name") or state["entity_id"]
    return _strip(raw)


def cmd_default(states) -> int:
    sensors = _person_sensors(states)
    if not sensors:
        print("No '*_person_detected' sensors found on this Home Assistant.")
        return 1

    active = [s for s in sensors if s["state"] == "on"]
    if active:
        print("PERSON ON CAMERA RIGHT NOW:")
        for s in sorted(active, key=lambda s: _parse_ts(s["last_changed"])):
            since = _parse_ts(s["last_changed"])
            print(f"  - {_name(s)}  (since {_fmt_clock(since)}, {_ago(since)})")
        return 0

    # Nobody on camera now: most recent "off" transition ~= end of last detection.
    off = [s for s in sensors if s["state"] not in INACTIVE_STATES]
    if not off:
        print("No person detected, and all person-detection sensors are offline.")
        return 1

    latest = max(off, key=lambda s: _parse_ts(s["last_changed"]))
    when = _parse_ts(latest["last_changed"])
    print("No person is detected on any camera right now.")
    print()
    print(f"Last human seen:  {_fmt_local(when)}")
    print(f"                  ({_ago(when)})")
    print(f"Camera:           {_name(latest)}  [{latest['entity_id']}]")

    offline = [s for s in sensors if s["state"] in INACTIVE_STATES]
    if offline:
        print()
        print("Note - these person-detection cameras are currently offline:")
        for s in offline:
            print(f"  - {_name(s)}  [{s['entity_id']}]")
    return 0


def cmd_list(states) -> int:
    sensors = sorted(_person_sensors(states), key=_name)
    if not sensors:
        print("No '*_person_detected' sensors found.")
        return 1
    width = max(len(_name(s)) for s in sensors)
    print(f"{'CAMERA'.ljust(width)}  STATE        LAST CHANGE           ENTITY")
    for s in sensors:
        try:
            changed = _fmt_clock(_parse_ts(s["last_changed"]))
        except Exception:
            changed = "?"
        print(
            f"{_name(s).ljust(width)}  {s['state']:<11}  {changed:<19}  {s['entity_id']}"
        )
    return 0


def cmd_detail(hours: float) -> int:
    states = _api("/api/states")
    sensors = _person_sensors(states)
    ids = [s["entity_id"] for s in sensors]
    if not ids:
        print("No '*_person_detected' sensors found.")
        return 1

    start = datetime.now(timezone.utc).timestamp() - hours * 3600
    start_iso = datetime.fromtimestamp(start, timezone.utc).isoformat()
    query = urllib.parse.urlencode({"filter_entity_id": ",".join(ids)})
    history = _api(f"/api/history/period/{urllib.parse.quote(start_iso)}?{query}")

    # Build (camera, start, end) intervals from each sensor's on->off transitions.
    intervals = []
    for series in history:
        if not series:
            continue
        name = _strip(
            series[0].get("attributes", {}).get("friendly_name")
            or series[0]["entity_id"]
        )
        on_since = None
        for entry in series:
            st = entry.get("state")
            ts = _parse_ts(entry["last_changed"])
            if st == "on" and on_since is None:
                on_since = ts
            elif st != "on" and on_since is not None:
                intervals.append((name, on_since, ts))
                on_since = None
        if on_since is not None:
            intervals.append((name, on_since, None))  # still on

    if not intervals:
        print(f"No person detections in the last {hours:g} hours.")
        return 0

    intervals.sort(key=lambda i: i[1], reverse=True)
    print(f"Person detections in the last {hours:g} hours (most recent first):")
    print()
    for name, s_ts, e_ts in intervals[:25]:
        if e_ts is None:
            span = "ongoing"
            end = "now"
        else:
            dur = int((e_ts - s_ts).total_seconds())
            span = f"{dur}s"
            end = _fmt_clock(e_ts)
        print(
            f"  {s_ts.astimezone(LOCAL_TZ).strftime('%m-%d %I:%M:%S %p')} -> {end:<12} "
            f"({span:<8}) {name}"
        )
    if len(intervals) > 25:
        print(f"  ... and {len(intervals) - 25} earlier detections")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--list", action="store_true", help="list all person-detection cameras"
    )
    group.add_argument(
        "--detail",
        nargs="?",
        const=3.0,
        type=float,
        metavar="HOURS",
        help="show recent detection windows (default: last 3 hours)",
    )
    args = parser.parse_args()

    if args.detail is not None:
        return cmd_detail(args.detail)

    states = _api("/api/states")
    if args.list:
        return cmd_list(states)
    return cmd_default(states)


if __name__ == "__main__":
    raise SystemExit(main())
