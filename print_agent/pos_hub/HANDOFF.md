# Downtown counter hardware: scanners, receipt printer, network — handoff

Written 2026-10-26 (America/Denver) for an agent arriving with no context.
The plan this serves is `docs/pos-register-plan.md` in
`JoldiTech/New-Mexico-Tea-Company-Server`: replace the Revel register on the
counter iPads with a web app, with the Home Assistant box reading the barcode
scanners and printing receipts (its Appendix A is the add-on contract).
Everything here was measured, not assumed; each fact says how it was found.
Nothing on the network, the adapters, the printer or the add-on was changed.

> **Superseded in part, 2026-10-27.** The register is CryptoCart's POS app,
> and its box program runs inside this add-on as a second process (add-on
> 1.4.0; see `nmt_print_agent/CHANGELOG.md` and `DOCS.md`). The NM plan's
> Appendix A hub, §9's packaging notes and the A.9 capture below are replaced
> by CryptoCart's `apps/pos/APP.md` section 8. The measurements stand.

## 0. Where the software lives (owner's rule)

**Everything the counter needs from the store network is built into the
existing print agent add-on on the Home Assistant box**
(`print_agent/nmt_print_agent/`, `local_nmt_print_agent`): scanner
connections, scan capture, receipt printing, the register WebSocket. It
grows that app; nothing is duplicated elsewhere. **The AI box hosts no part
of it.** The listener described in §5 ran on the AI box once, as a one-day
measurement, because it needed no add-on release; its script is kept here
only as a record of how the capture was taken, not as code to reuse or
redeploy there. Production capture is plan Appendix A.9, inside the add-on.

## 1. Status in one paragraph

The Left scanner adapter (192.168.22.252) **can be shared with Revel**: on
2026-10-26 a read-only listener on the AI box held a second TCP connection to
it for the whole trading day (09:30–18:30 Denver, zero drops) and received
all 99 scans, while Revel's Blue iPad kept ringing sales from the same
scanner (20 of its orders were created in the same second as a captured
scan). Scans arrive with **no terminator** (no CR, no LF), one scan per TCP
chunk. The Right adapter (.251) has not been connected to at all. The
receipt printer is an Epson TM on a UB-E03 network card, port 9100 open.
Nothing has been built in the add-on yet; it is still version 1.3.0.

## 2. Network map (192.168.22.0/24, one flat LAN, no VLANs)

Found by ping sweep + ARP from the AI box, and the UniFi controller's
read API (`stat/sta`, `rest/user`, `stat/device`, v2 `clients/active`).

| IP | MAC | What it is | How it is connected |
|---|---|---|---|
| .1 | 74:fa:29:42:26:56 | UniFi **Dream Machine Pro Max** (UDMPROMAX, Network 5.1.33) — gateway, DHCP, DNS | — |
| .6 | a8:5e:45:e6:62:1f | **AI box** `nmteacoaiserver` (Ubuntu 24.04, ASUS board) — fixed IP in UniFi | UDM port 5, wired |
| .91 | 6e:5b:19:14:db:b4 | **Pink iPad**, Revel PosStation **14** (private Wi-Fi MAC) | Wi-Fi "NM Tea Co.", U7 Pro Max, DHCP, no reservation |
| .190 | 02:43:df:9f:d2:6f | **Blue iPad**, Revel PosStation **12** (private Wi-Fi MAC) | Wi-Fi "NM Tea Co.", U7 Pro Max, DHCP, no reservation |
| .207 | 64:eb:8c:28:e8:f0 | **Receipt printer**, Epson (Seiko Epson OUI) | wired, switch port unknown (see §8) |
| .238 | 5c:fb:3a:0d:80:7e | HP LaserJet M203dw — the *pickup-order* printer the add-on already drives; not the receipt printer | Wi-Fi, fixed IP |
| .251 | d8:b0:4c:00:91:3d | **Right scanner adapter**, USR IOT (Jinan USR IOT OUI) | wired, static IP, switch port unknown |
| .252 | d8:b0:4c:00:9d:e6 | **Left scanner adapter**, USR IOT | wired, static IP, switch port unknown |
| .253 | 5c:e7:53:8c:94:0c | **Govee H61E1 smart light** (UniFi fingerprint) — *not* a scanner adapter | Wi-Fi, DHCP |
| .254 | 20:f8:3b:03:b5:b6 | **Home Assistant Green** (Nabu Casa) — the box | UDM port 4, 1 Gbps, fixed IP in UniFi |

Other UniFi gear: **US-16-PoE-150W** (.119, cameras, phones), **US-8-60W**
(.110, Sonos, one camera), APs **U7 Pro Max** (.107, carries the iPads) and
**AC Pro** (.58).

Traps found here:

- **Revel's "Tes" test adapter at .253 does not exist any more**: DHCP gave
  .253 to a Govee light, which has traffic every hour. Do not configure
  anything against .253.
- **The DHCP pool is .6–.254**, so it covers the adapters' and printer's
  static addresses. That is how a light got .253; the same could happen to
  .251/.252/.207. Fix (owner's change, not made): shrink the pool or add
  reservations in UniFi → Settings → Networks → Default.
- The UniFi controller does not list .251, .252 or .207 as clients (static
  addresses with LAN-only traffic), so it cannot say which switch port they
  are on. Someone has to trace the cables. UDM port 3 (100 Mbps) already
  carries two office printers, so there is probably a small unmanaged switch
  behind it; UDM port 6 (100 Mbps) has no known device. Both are candidates.

## 3. The box: Home Assistant Green

- HA OS **18.3** (slot B still 18.1), Core 2026.10.x, board `green`,
  interface **`end0`**, IP **192.168.22.254**, reserved in UniFi.
- Add-ons: Matter Server (host network), Cloudflared, Z-Wave JS, Terminal &
  SSH, VS Code (error state, pre-existing), **NMT Print Agent
  `local_nmt_print_agent` 1.3.0**.
- Host ports open on the LAN: 22 (SSH add-on), 8123 (HA). **8080, 8081 and
  8443 are free** (connection refused from the LAN).
- **An add-on `ports:` mapping keeps the caller's real address**: a held
  connection from the AI box to the SSH add-on's mapped port showed peer
  `192.168.22.6` inside the add-on, not a Docker address. So a LAN allow-list
  in the add-on works without host networking.
- Host networking is possible (Matter uses it) but **must not be used for the
  print agent**: the Cloudflare tunnel reaches it as
  `http://local-nmt-print-agent:8080`, which host networking would break.

## 4. Scanner adapters (.251 Right, .252 Left)

Read from each adapter's web page on port 80, signed in with the factory
default account (still unchanged — the owner should change it outside
opening hours). Nothing was saved. Both are identical:

| Setting | Value |
|---|---|
| Hardware | Revel-branded **USR IOT** serial-to-Ethernet module, three serial ports; footer "Copyright 2008-2014, Revel Systems"; web server lwIP 1.3.1. The page never prints a USR model number; **"R212-0001" is only the editable module name**. Read the label on the unit for the model. |
| Firmware | **3003** |
| Address | static IP, 255.255.255.0, gateway .1 |
| Port 0 (the scanner) | **TCP Server, local port 10001**, "TCP Server detail: default" |
| Port 0 serial | **115200 baud, 8 data bits, no parity, 1 stop bit, no flow control** |
| Port 0 packing | timeout 0 (none), **UART packet time 5 ms**, packet length 512 |
| Ports 1 and 2 | TCP Server on 26 and 29, RS485, 115200 8N1, packet time 10 ms (nothing known to use them) |
| Max clients / kick-oldest / open connections | **Not shown anywhere** in this firmware |

Security notes: the sign-in only guards the frame page — the status and
settings pages (`s2e.shtml`, `config1.shtml`, `misc.shtml`) load without
signing in, and the password page shows the password in clear.

Rules for anyone touching them while Revel is live:
- **Never send a byte to port 10001.** Port 0 is the scanner's serial line;
  anything written goes to the scanner.
- Do not open the web settings and press Submit/Restart; every form on those
  pages writes configuration (`config.cgi`, `ip.cgi`, `misc.cgi`,
  `cpass.cgi`, `defaults.cgi`, `login.cgi?restart`).
- **.251 (Right) has never been connected to.** Treat it as unknown until it
  gets its own test.

## 5. The 2026-10-26 capture (Left adapter, .252)

### How it was run

- A systemd timer + service on the AI box, outside any shell session:
  `scanner-listen.timer` (one-shot, `OnCalendar=2026-10-26 09:30:00
  America/Denver`) → `scanner-listen.service` (user `nmteaco`, sandboxed,
  `IPAddressAllow=192.168.22.252/32` only, `RuntimeMaxSec=9h5min`).
  Unit files: on the AI box, `/etc/systemd/system/scanner-listen.*`.
- Script: on the AI box at `~/scanner-listen/listen.py` (not in git). It opens one TCP connection with keepalive
  (idle 60 s, interval 10 s, count 3), **never calls `send()`**, logs each
  received chunk raw as a JSON line, reconnects at most once a minute, gives
  up after 3 drops, and closes itself at 18:30 Denver. At close it records
  the kernel's own counters (`TCP_INFO`) for bytes sent.
- Log format: `{"at": "<UTC ISO ms>", "hex": "…", "text": "<printable,
  others escaped as \xNN>"}` for data; `{"at", "event": start|connect|
  reconnect|connect_failed|disconnect|reset|close|stop, …}` for events.

### Result

| | |
|---|---|
| Connected | 2026-10-26 15:30:00.504Z (09:30:00 MDT) from 192.168.22.6:45962 |
| Closed | 2026-10-27 00:30:00.940Z (18:30:00 MDT), reason `deadline` |
| Uptime | **32,400.4 s, one connection, 0 drops, 0 resets, 0 reconnects** |
| Bytes sent | **0** (kernel `bytes_sent` 0, `data_segs_out` 0) |
| Bytes received | 1,363 = the sum of the 99 logged chunks |
| Scans | **99**, first 10:38:03 MDT (16:38:03.339Z), last 17:45:21 MDT (23:45:21.063Z) |
| After close | service inactive, no process, no socket to .252; the elapsed one-shot timer remains loaded with no future trigger (remove with `sudo systemctl disable --now scanner-listen.timer`) |

The full log is on the AI box at `~/scanner-listen/left-2026-10-26.jsonl`;
it is kept out of git, and its figures are all in this report.

### What the bytes say (framing)

- **No terminator at all**: not one CR (0x0D) or LF (0x0A) in 1,363 bytes,
  no prefix, no suffix. Each scan arrives as **exactly one TCP chunk**,
  because the adapter's 5 ms UART packet time closes a packet when the
  serial line goes quiet. All bytes printable ASCII.
- So Appendix A.4's "`suffix: auto` = CR, LF or CRLF" would never split a
  scan. Frame on **one read = one scan**, with an idle-gap fallback (bytes
  arriving within ~50 ms join the same scan) in case a chunk is ever split.
- What was scanned: 55 `teaurl.com/<n>` QR codes (our own labels), 5
  `www.nmteaco.com/_p_<id>.html` QR codes, 17 twelve-digit (UPC-A), 12
  thirteen-digit (EAN-13), 10 eight-digit. Length 8–28 characters, so A.4's
  64-character cap is fine.
- Fastest gap between scans 1.4 s. Identical consecutive codes came 3–7 s
  apart (8 cases) — deliberate second units, not double reads. A 300 ms
  debounce would not have touched any of them.
- Not every `teaurl.com/<n>` is a Revel barcode: e.g. `teaurl.com/2814`
  is in "Fiesta Collection"'s `alternate_lookup`, but most `teaurl.com`
  codes scanned matched no barcode or `alternate_lookup` of any product sold
  that day. The new register's barcode table must carry these label URLs
  (and the `_p_<id>` URLs) or resolve them itself.

### Proof that the scans also reached Revel

Revel API (`datalog/revel_query.php`, endpoints `Order`, `OrderItem`,
`Product`) for 2026-10-26: 83 orders, 46 on PosStation 12 (Blue iPad), 37
on PosStation 14 (Pink iPad).

- **20 Blue-iPad orders were created in the same second as a captured
  scan; zero Pink-iPad orders were.** A Revel order is created by its first
  scan, so the Blue iPad was receiving the same scans as the listener.
- 19 scanned codes were Revel barcodes; every one of them appears as an item
  on a Blue-iPad order opened at that scan's second (e.g. 11:47:58 MDT
  `728987043222` → "Feline Clean sponged", order 340766).
- So: **Left adapter = Blue iPad (station 12)**, presumably Right = Pink.

**Timestamp trap:** Revel's `created_date` was exactly **one hour behind**
Denver wall time on this date (scan 11:47:58 MDT ↔ order 10:47:58). It
behaves as UTC−7 (MST) while Denver was on MDT. The server repo's CLAUDE.md
says `Revel_Orders.created_date` is Denver wall time; that held for its
example, but on 2026-10-26 it did not. Check the offset before matching
anything by time.

## 6. Receipt printer (.207)

- SNMP v1, community `public`: sysDescr `EPSON UIB 10/100Base-T Ethernet
  Interface Card`, sysName `UB-E28E8F0ENPC`, hrDeviceDescr `EPSON UB-E03`.
- Web page (EPSON TMNet WebConfig 1.00, no sign-in for status): interface
  card **UB-E03**, hardware 03.00, software 01.03, Printer ID 104, status
  Online.
- **The printer model itself (TM-T88V, TM-T20, …) is not reported**; read
  the label. It is an Epson TM receipt printer speaking ESC/POS.
- **TCP 9100 accepts connections** (2 ms). Tested by connect-and-close only;
  nothing has ever been sent to it from here. Do not send to it while Revel
  is live without the owner's say-so — it is Revel's receipt printer.

## 7. Wi-Fi, DNS, Stripe

- iPads use SSID **"NM Tea Co."** (WPA2, 2.4 + 5 GHz) on the Default network.
  **Client isolation off**, proxy ARP off, no guest mode: iPads reach the box
  directly (the AI box pings both iPads).
- LAN DNS is the UDM, **192.168.22.1** (DHCP hands out the gateway); no
  DNS-over-HTTPS, ad blocking off. HA's internal resolver forwards to it.
- `api.stripe.com` is reachable over HTTPS from the box.
- **The UDM drops DNS answers that point at private addresses**:
  `10-42-42-42.test.device.stripe-terminal-local-reader.net` returns an empty
  answer from 192.168.22.1 (and from HA's resolver), while 1.1.1.1 returns
  10.42.42.42. A Stripe Terminal reader on the local network needs the UDM
  to allow `stripe-terminal-local-reader.net` (owner's change, not made).
  The plan drives the S700 from the server, which may avoid this; check
  before relying on local discovery.

## 8. Unknowns (honestly unmeasured)

- USR hardware model number, and how many TCP clients firmware 3003 accepts
  before refusing or dropping one. Two at once is proven on .252 (Revel +
  listener, all day); more is untested.
- Whether .251 behaves like .252.
- The Epson printer's model.
- Which switch ports .251, .252 and .207 are on (cable trace needed).

## 9. The print_agent add-on (where the hub will live)

- Code: `print_agent/nmt_print_agent/` in this repository; Python 3 standard
  library only, `agent/agent.py` (one `ThreadingHTTPServer` on
  `0.0.0.0:8080`), `agent/store_requests.py` + `agent/handlers.py` for the
  typed `/v1` requests. Debian bookworm image with CUPS.
- Options are `options:` + `schema:` in `config.yaml`; new top-level options
  take their defaults on upgrade without touching `signing_key`.
- Deploy: copy the folder to `/local_apps/nmt_print_agent` on the box, bump
  `version`, `ha store reload`, `ha apps rebuild local_nmt_print_agent`
  (~15 min aarch64 build). The deployed copy is identical to `main` (1.3.0).
- For the hub (plan Appendix A.0): add `ports:` for `8443/tcp` and
  `8081/tcp`; **do not map `8080/tcp`** (it would expose `/print` and `/v1`
  on the LAN outside Cloudflare Access); keep host networking off; watch the
  first upgrade for watchdog restarts (`watchdog: tcp://[HOST]:[PORT:8080]`).
  Keep options flat — Supervisor's schema cannot express A.1's free-keyed
  `assignments: {"1": …}` or three levels of nesting.

## 10. How to reach things

From a Claude cloud session in this repo, the SessionStart hook sets up:

- `ssh homeassistant` (or `scripts/ha-ssh '<cmd>'`, with retry) — shell in
  the Terminal & SSH add-on on the box; it is on HA's Docker bridge, so it
  cannot see the LAN's ARP table. `ha …` CLI works there.
- `ssh aibox` — the AI box, on the LAN directly (has `ping`, `arping`,
  `tcpdump`, `curl`, `python3`; no `nmap`/`snmpget`/`dig` — use Python).
  The tunnel is flaky; open a master first:
  `ssh -fN -o ControlMaster=yes -o ControlPersist=7200 -S ~/.ssh/cm/aibox-m aibox`
  and pass `-S ~/.ssh/cm/aibox-m` on later calls. **Don't `pkill -f` a
  pattern that appears in your own command line** — it kills your shell.
- UniFi read API from the AI box: `set -a; . /etc/nmteaco/protect.env` then
  `curl -sk -H "X-API-KEY: $PROTECT_API_KEY"
  https://$PROTECT_HOST/proxy/network/api/s/default/stat/sta` (also
  `stat/device`, `rest/user`, `rest/wlanconf`, `stat/report/hourly.user`
  (POST, a query), `/proxy/network/v2/api/site/default/clients/active`).
  The responses include secrets (mesh PSK, VPN keys) — never paste them.
- Revel data: `https://dashboard.nmteaco.com/tools/datalog/revel_query.php?endpoint=Order&params={…}`
  with `Authorization: Bearer $DATALOG_API_TOKEN` (send the header yourself
  if the variable is set; otherwise the proxy adds it).

The next capture (including the first look at the Right adapter, .251) is
the add-on's job: build A.9 into `nmt_print_agent` and ship it with
`connect: false`, then switch on one adapter. Do not set up another
listener on the AI box. The one-day units there are finished; the elapsed
timer can be removed with `sudo systemctl disable --now scanner-listen.timer`.
