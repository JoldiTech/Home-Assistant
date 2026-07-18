#!/usr/bin/env python3
"""tunnel_health.py — measure how reliable the Cloudflare tunnel actually is.

"It's flaky" is hard to act on; a success rate and a latency spread aren't.
This probe hammers the HA REST API (and optionally SSH) a fixed number of times
and reports success rate + latency percentiles, so you can quantify the problem,
prove a fix helped, or catch a regression later.

Standard-library only. Reads the same env vars as the rest of the repo:
    HOMEASSISTANT_BASE_URL   e.g. https://ha.nmteaco.com
    HOMEASSISTANT_TOKEN      long-lived access token

Usage:
    scripts/tunnel_health.py                 # 20 API probes
    scripts/tunnel_health.py -n 50           # 50 API probes
    scripts/tunnel_health.py --ssh           # also probe SSH (needs ssh alias)
    scripts/tunnel_health.py -n 30 --interval 0.5 --json

Exit code is 0 when every probed channel meets --threshold (default 95%)
success, else 1 — so it doubles as a CI/monitoring check.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request


def pct(values: list[float], p: float) -> float:
    """Nearest-rank percentile of a list of floats (empty -> 0.0)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return ordered[k]


def probe_api(base_url: str, token: str, timeout: float) -> tuple[bool, float, str]:
    """One GET /api/ probe. Returns (ok, elapsed_seconds, detail)."""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/",
        headers={"Authorization": f"Bearer {token}"},
    )
    ctx = ssl.create_default_context()
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            resp.read(256)
            return (resp.status == 200, time.monotonic() - start, str(resp.status))
    except urllib.error.HTTPError as e:
        return (False, time.monotonic() - start, f"HTTP {e.code}")
    except Exception as e:  # timeout, reset, TLS, DNS…
        return (False, time.monotonic() - start, type(e).__name__)


def probe_ssh(alias: str, timeout: float) -> tuple[bool, float, str]:
    """One SSH round-trip probe (`ssh <alias> true`)."""
    start = time.monotonic()
    try:
        r = subprocess.run(
            ["ssh", "-o", f"ConnectTimeout={int(timeout)}", alias, "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout + 10,
        )
        detail = "ok" if r.returncode == 0 else (
            r.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["fail"]
        )[0]
        return (r.returncode == 0, time.monotonic() - start, detail)
    except subprocess.TimeoutExpired:
        return (False, time.monotonic() - start, "timeout")
    except Exception as e:
        return (False, time.monotonic() - start, type(e).__name__)


def run_channel(name, fn, n, interval, verbose):
    oks, lat, fails = 0, [], {}
    for i in range(1, n + 1):
        ok, elapsed, detail = fn()
        lat.append(elapsed)
        if ok:
            oks += 1
        else:
            fails[detail] = fails.get(detail, 0) + 1
        if verbose:
            mark = "ok " if ok else "ERR"
            print(f"  [{name}] {i:>3}/{n}  {mark}  {elapsed:6.3f}s  {detail}",
                  file=sys.stderr)
        if i < n and interval:
            time.sleep(interval)
    return {
        "channel": name,
        "count": n,
        "ok": oks,
        "success_rate": round(100 * oks / n, 1) if n else 0.0,
        "p50_ms": round(pct(lat, 50) * 1000),
        "p90_ms": round(pct(lat, 90) * 1000),
        "p99_ms": round(pct(lat, 99) * 1000),
        "max_ms": round(max(lat) * 1000) if lat else 0,
        "failures": fails,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure Cloudflare tunnel reliability.")
    ap.add_argument("-n", "--count", type=int, default=20, help="probes per channel")
    ap.add_argument("--interval", type=float, default=0.3, help="seconds between probes")
    ap.add_argument("--timeout", type=float, default=15.0, help="per-probe timeout (s)")
    ap.add_argument("--ssh", action="store_true", help="also probe SSH")
    ap.add_argument("--ssh-alias", default=os.environ.get("HA_SSH_ALIAS", "homeassistant"))
    ap.add_argument("--threshold", type=float, default=95.0,
                    help="min success%% per channel for exit 0")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    base_url = os.environ.get("HOMEASSISTANT_BASE_URL")
    token = os.environ.get("HOMEASSISTANT_TOKEN")
    if not base_url or not token:
        print("error: HOMEASSISTANT_BASE_URL and HOMEASSISTANT_TOKEN must be set",
              file=sys.stderr)
        return 2

    verbose = not args.json
    results = [run_channel(
        "api", lambda: probe_api(base_url, token, args.timeout),
        args.count, args.interval, verbose)]
    if args.ssh:
        results.append(run_channel(
            "ssh", lambda: probe_ssh(args.ssh_alias, args.timeout),
            args.count, args.interval, verbose))

    if args.json:
        print(json.dumps({"results": results}, indent=2))
    else:
        print("\n=== Tunnel health ===")
        for r in results:
            print(f"\n{r['channel'].upper()}  ({r['ok']}/{r['count']} ok = "
                  f"{r['success_rate']}%)")
            print(f"  latency: p50 {r['p50_ms']}ms  p90 {r['p90_ms']}ms  "
                  f"p99 {r['p99_ms']}ms  max {r['max_ms']}ms")
            if r["failures"]:
                detail = ", ".join(f"{k}×{v}" for k, v in r["failures"].items())
                print(f"  failures: {detail}")

    worst = min((r["success_rate"] for r in results), default=0.0)
    return 0 if worst >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
