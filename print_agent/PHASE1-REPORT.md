# Downtown print agent: Phase 1 survey (read-only)

Surveyed 2026-10-11 ~01:20–01:30 MT. **Nothing was changed** on the printer,
UniFi, Cloudflare or Home Assistant. Raw IPP/SNMP output is in
`phase1-raw-ipp-snmp.txt` (it covers both HP printers on the LAN).

## 1. Network layout

- One UniFi site ("Default", UDM Pro Max). Networks: `Default` 192.168.22.0/24
  (no VLAN tag, DHCP .6–.254, 24 h lease), plus the One-Click VPN and two WANs.
- **Home Assistant Green**: 192.168.22.254, `Default`, wired (switch port 4),
  already a UniFi fixed IP.
- **Pickup printer**: 192.168.22.238, `Default`, **Wi-Fi** (SSID "NM Tea Co.").
- Same site, same network — no VPN and no firewall rule needed. From the HA box:
  ping OK; TCP 80, 443, 631, 9100 all open (8080 also open on the printer).

## 2. Printer identity

| | |
|---|---|
| Make / model | **HP LaserJet M203dw** (product G3Q47A, family M203–M206) |
| Serial | VNB3G42744 |
| Firmware | 20200605 (date code 2020-06-05) |
| MAC | 5c:fb:3a:0d:80:7e (Wi-Fi module; UniFi shows OUI "Chongqing Fugui") |
| Hostname | NPI02EFF1 |
| Current IP | 192.168.22.238, **from DHCP** (no reservation; UniFi `use_fixedip` unset) |

Matches the staff description (small laser, Resume button, Ready/Attention LEDs).
Other printers on the LAN, *not* the target: HP Color LaserJet M553
(192.168.22.73, wired) and a Brother HL-L8360CDW (192.168.22.134).

## 3. Protocols

**IPP — works.** `ipp://192.168.22.238/ipp/print` (also `ipps://…/ipp/print`):

```
document-format-supported = image/urf, application/PCLm, application/octet-stream,
                            application/pdf, application/postscript,
                            application/vnd.hp-PCL, application/vnd.hp-PCLXL, image/jpeg
printer-state            = 3 (idle)
printer-state-reasons    = toner-low-warning
printer-make-and-model   = HP LaserJet M203-M206
urf-supported            = V1.4, CP99, W8, OB10, PQ3-4-5, DM1, IS1, MT1-3-5, RS600
job-impressions-supported   = 1-99999
job-media-sheets-supported  = 1-99999
printer-alert            = code=markerTonerAlmostEmpty
media-ready              = na_letter_8.5x11in
```

No IPP attribute exposes a lifetime page/impression counter (only the
`job-*-supported` ranges above), so the page count comes from SNMP.
`application/pdf` is supported, and so is URF — a driverless CUPS
`-m everywhere` queue will work.

**Bonjour/mDNS** (multicast PTR queries from the AI box, same LAN):

```
_ipp._tcp            192.168.22.238  HP LaserJet M203dw (02EFF1)
_ipps._tcp           192.168.22.238  HP LaserJet M203dw (02EFF1)
_pdl-datastream._tcp 192.168.22.238  HP LaserJet M203dw (02EFF1)
_printer._tcp        192.168.22.238  HP LaserJet M203dw (02EFF1)
```

**Raw 9100:** open (nothing sent).

**SNMP v2c, community `public`: ON (read works)** — no change needed in Phase 2.

```
1.3.6.1.2.1.43.10.2.1.4.1.1  lifetime page count          = 7163
1.3.6.1.2.1.25.3.5.1.1.1     hrPrinterStatus              = 1 (other)  ← printer was in power save
1.3.6.1.2.1.25.3.5.1.2.1     hrPrinterDetectedErrorState  = 0x20 (lowToner)
1.3.6.1.2.1.1.1.0            sysDescr = HP ETHERNET MULTI-ENVIRONMENT,SN:VNB3G42744,FN:3B75LRN,SVCID:32020,PID:HP LaserJet M203dw
1.3.6.1.2.1.43.18 (prtAlert) walk returned no rows
```

**Web page** (`http://192.168.22.238/`, title "HP LaserJet M203dw 192.168.22.238")
is the consumer-style EWS. The `/hp/device/...` pages of business models are
404; the useful data is in the LEDM XML:

- `/DevMgmt/ProductStatusDyn.xml` — `StatusCategory=inPowerSave`; alerts
  `cartridgeLow` (Warning, black toner) and `genuineHP` (Info, drum).
- `/DevMgmt/ProductUsageDyn.xml` — **page count shown**: `TotalImpressions = 7163`
  (equals the SNMP counter).
- `/ePrint/ePrintConfigDyn.xml` — `EmailService=enabled`,
  `RegistrationState=registered`, `XMPPConnectionState=connected` (HP ePrint is
  live; leave it alone).
- `/DevMgmt/ProductConfigDyn.xml` — `PasswordStatus=notSet` (see Concerns).

## 4. Page counter

- Reading 1: **7163** (SNMP and EWS agree), 2026-10-11 ~01:25 MT.
- Reading 2: **pending** — needs staff to press Reprint once on the curbside
  tablet (or hold Resume ~5 s for a config page). The store is closed now.

## 5. Home Assistant

| | |
|---|---|
| Installation type | Home Assistant OS 18.1 (board `green`, Supervisor 2026.09.3), Core 2026.7.2 |
| Add-ons (Apps) | Available. **Local add-on folder is `/local_apps`** (not `/addons/local`), present and writable over SSH |
| Architecture | aarch64 |
| Disk | 18.7 GB free of 28 GB |
| RAM | 3.9 GB total, ~2.3 GB available |
| cloudflared | App "Cloudflared" `9074a9fa_cloudflared` v7.0.17, started, `boot: auto`, watchdog on, token-mode (options: `tunnel_token`, `run_parameters`, …) |
| IPP integration | **Already added**: config entry "HP LaserJet M203dw (02EFF1)", loaded — `sensor.hp_laserjet_m203_m206` = idle, black cartridge **2 %**, drum 51 % |

## 6. Outbound reach and clock

- HA box → `https://dashboard.nmteaco.com/`: **HTTP 302 in 0.44 s** (reachable).
- Clock: HAOS reports `use_ntp: true`, `dt_synchronized: true`; box UTC
  07:25:39 matched the dashboard's `Date:` header to the second.

## 7. Cloudflare (owner-managed; not changed)

`https://print-downtown.nmteaco.com/status` without a token → **403 "Cloudflare
Access"** page (`cf-access-domain: print-downtown.nmteaco.com`). DNS resolves to
Cloudflare. Access is enforced; the route itself can't be checked until the
add-on runs.

## Phase 2 pre-checks (read-only)

- UniFi client record for 5c:fb:3a:0d:80:7e: `_id 659728f49a62e95d43422b61`,
  `use_fixedip` unset, `fixed_ip` unset. **No other client reserves
  192.168.22.238** (3 of 86 known clients have fixed IPs). Pinning it in place is
  safe.
- HA box already has fixed IP 192.168.22.254 — no change needed.
- No firewall rule needed (same network).
- No SNMP change needed (already on, read-only community `public`).

## Things in the instructions that don't match this install

1. **Local add-on path** is `/local_apps` on this Supervisor, not
   `/addons/local/nmt_print_agent`. The slug `nmt_print_agent` should still give
   the hostname `local-nmt-print-agent`; confirmed in Phase 2.
2. **`hrPrinterStatus` reads `1 (other)` while the printer sleeps**, not
   `3 (idle)`. The agent must take state from IPP `printer-state` /
   `printer-state-reasons` and use SNMP only for the page counter.
3. **IPP exposes no page counter**, so SNMP is required (it's already on).

## Concerns for the owner

- **Toner is at 2 %** (`toner-low-warning`, `markerTonerAlmostEmpty`). A
  toner-out stop will look like a print failure. Replace the CF230X soon.
- **The printer has no admin password** (`PasswordStatus=notSet`). Anyone on the
  store Wi-Fi can change its settings, including turning off ePrint. Not changed
  (out of scope); worth setting.
- **The printer is on Wi-Fi.** Wired would be more reliable. If it ever moves to
  Ethernet its MAC changes and the reservation must be redone.
