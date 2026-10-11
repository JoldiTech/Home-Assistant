#!/usr/bin/env python3
"""Put the Instore Order Printing cards in the "Store State" panel.

The DowntownControls dashboard is stored in HA's UI storage, so it is changed
through the websocket API: fetch the whole config, replace our cards at the end
of the Store view's "Store State" section, save. Idempotent: everything from
our "Instore Order Printing" heading to the end of that section is ours and is
replaced on each run, so keep the cards last in the section.

Back up first (on the box):
    cp /config/.storage/lovelace.dashboard_downtowncontrols \\
       /config/.storage/lovelace.dashboard_downtowncontrols.bak-$(date +%Y%m%d%H%M%S)

    pip install websocket-client
    python3 print_agent/dashboard/apply_store_panel.py          # dry run: prints the section
    python3 print_agent/dashboard/apply_store_panel.py --apply  # saves

Needs HOMEASSISTANT_BASE_URL and HOMEASSISTANT_TOKEN. One auth attempt only:
`ip_ban` is on.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.parse

import websocket  # websocket-client

HERE = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = "dashboard-downtowncontrols"
VIEW_TITLE = "Store"
SECTION_HEADING = "Store State"
OUR_HEADING = "Instore Order Printing"


def our_cards():
    with open(os.path.join(HERE, "store_state_printing.json")) as f:
        cards = json.load(f)
    for card in cards:
        content = card.get("content", "")
        if content.startswith("@"):
            with open(os.path.join(HERE, content[1:])) as f:
                card["content"] = f.read()
    return cards


def connect():
    base = urllib.parse.urlsplit(os.environ["HOMEASSISTANT_BASE_URL"])
    url = f"{'wss' if base.scheme == 'https' else 'ws'}://{base.netloc}/api/websocket"
    opts = {}
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        p = urllib.parse.urlsplit(proxy)
        opts.update(http_proxy_host=p.hostname, http_proxy_port=p.port, proxy_type="http")
    ca = os.environ.get("SSL_CERT_FILE") or ("/root/.ccr/ca-bundle.crt" if os.path.exists("/root/.ccr/ca-bundle.crt") else None)
    sslopt = {"cert_reqs": ssl.CERT_REQUIRED, **({"ca_certs": ca} if ca else {})}
    ws = websocket.create_connection(url, sslopt=sslopt, timeout=20, **opts)
    hello = json.loads(ws.recv())
    if hello.get("type") != "auth_required":
        raise SystemExit(f"unexpected greeting: {hello}")
    ws.send(json.dumps({"type": "auth", "access_token": os.environ["HOMEASSISTANT_TOKEN"]}))
    ok = json.loads(ws.recv())
    if ok.get("type") != "auth_ok":
        raise SystemExit(f"auth failed: {ok.get('type')}")  # no retry: ip_ban
    return ws


def call(ws, n, msg):
    ws.send(json.dumps({"id": n, **msg}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == n:
            if not r.get("success"):
                raise SystemExit(f"{msg['type']} failed: {r.get('error')}")
            return r.get("result")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    ws = connect()
    config = call(ws, 1, {"type": "lovelace/config", "url_path": DASHBOARD})
    view = next(v for v in config["views"] if v.get("title") == VIEW_TITLE)
    section = next(s for s in view["sections"]
                   if s.get("cards") and s["cards"][0].get("heading") == SECTION_HEADING)
    cards = section["cards"]
    start = next((i for i, c in enumerate(cards) if c.get("type") == "heading" and c.get("heading") == OUR_HEADING),
                 len(cards))
    before = len(cards)
    section["cards"] = cards[:start] + our_cards()
    print(f"'{SECTION_HEADING}' section: {before} cards -> {len(section['cards'])} "
          f"({'replacing' if start < before else 'adding'} the printing cards)")
    if not a.apply:
        print(json.dumps(section["cards"][start:], indent=1)[:4000])
        print("dry run; pass --apply to save")
        return 0
    call(ws, 2, {"type": "lovelace/config/save", "url_path": DASHBOARD, "config": config})
    print("saved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
