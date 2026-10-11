# Handoff: downtown print agent (start of Phase 2)

For the next Claude session. Read this, then `PHASE1-REPORT.md`, and continue on
branch `claude/tender-goodall-oz5zgt` (PR JoldiTech/Home-Assistant#15). Phase 1
was done by an earlier session; nothing has been changed yet on any device.

The owner's full plan (phases 1–4, contract, ground rules) was uploaded as
`downtown-print-agent-instructions.md` in that earlier chat. It isn't in this
repo: **ask the owner to re-attach it** before you build anything. The contract
below is a summary for orientation, not a replacement.

## First steps in the new session

1. Run `/test` once (it checks every connection; don't re-run it).
2. Confirm the secrets are visible. Check that each variable is set; never print the value:
   - `PRINT_AGENT_SIGNING_KEY`: added by the owner, needed to sign test requests.
   - `CF_ACCESS_CLIENT_ID_PRINT` / `CF_ACCESS_CLIENT_SECRET_PRINT`: the
     `dashboard-print` service token. **Not added yet.** Only needed to test
     `/status` *through* the tunnel (Phase 2 step 4, Phase 4 tests 3 and 8).
     Everything else can be tested on the LAN at `http://local-nmt-print-agent:8080`.
3. **Get explicit owner approval of the Phase 1 report before any Phase 2
   change.** The plan requires it, and it had not been given when this was written.
   Adding the signing key doesn't count as approval.

## Key facts (verified 2026-10-11)

| | |
|---|---|
| Printer | HP LaserJet M203dw, MAC `5c:fb:3a:0d:80:7e`, **192.168.22.238** (DHCP, Wi-Fi) |
| IPP URI | `ipp://192.168.22.238/ipp/print`; PDF + URF supported → `lpadmin -m everywhere` |
| Page counter | SNMP v2c `public`, OID `1.3.6.1.2.1.43.10.2.1.4.1.1` (was 7163). Also `/DevMgmt/ProductUsageDyn.xml` TotalImpressions |
| Printer state | Use IPP `printer-state` / `printer-state-reasons`. SNMP `hrPrinterStatus` reads `1 (other)` in power save, so don't use it for state |
| HA box | HAOS 18.1, aarch64, Green, 192.168.22.254 (already fixed IP), same LAN as printer, no firewall needed |
| Local add-on dir | **`/local_apps`** (via `ssh homeassistant`), not `/addons/local` |
| Tunnel route | `print-downtown.nmteaco.com` → `http://local-nmt-print-agent:8080`, Access-protected (403 without token, verified) |
| UniFi client `_id` | `659728f49a62e95d43422b61` (printer), no fixed IP yet; .238 not reserved by anyone else |
| UniFi API | Key in `/etc/nmteaco/protect.env` on the AI box. Legacy API `https://192.168.22.1/proxy/network/api/s/default/rest/user/<_id>`; network id for Default = `5e13358d4c7ad505143f64ac`. Reads verified; **write not yet tested** |

## Remaining work

**Phase 2** (after approval):
- Pin the printer to 192.168.22.238 through the UniFi API: PUT `rest/user/<_id>` with
  `use_fixedip: true`, `fixed_ip`, `network_id`. Record before and after. Change nothing else.
- No change is needed for the HA box IP, firewall, or SNMP.
- Do not touch Cloudflare.

**Phase 3:** build add-on `nmt_print_agent` in `/local_apps/nmt_print_agent`:
- Python on the HA aarch64 base image, with CUPS (driverless queue) and WeasyPrint for HTML → Letter PDF.
- Listens on 8080, `boot: auto`, watchdog on, state in `/data`.
- HMAC contract: `X-NMT-Timestamp` and `X-NMT-Signature`, where the signature is HMAC-SHA256 over `ts\nMETHOD\nPATH\nbody`, with ±300 s allowed.
- Endpoints: `POST /print` (202, 200 duplicate, 503 with reason, 401), `GET /status`. Results go to `POST <dashboard>/tools/orders/curbsideAgent.php`.
- Keep the source in this repo under `print_agent/` too.

**Phase 4:** 10 tests. Tests 4–7 and 10 need someone at the printer (paper out,
door, power off, a real Reprint).

**Still pending from Phase 1:** the second page-counter reading after staff press
Reprint (store hours).

## Concerns already raised with the owner

- Toner at 2 %.
- Printer admin password not set.
- Printer is on Wi-Fi.

## Rules carried over

- Never log job HTML or header values.
- Never print a job twice.
- Leave HP ePrint on.
- `ip_ban` is on HA: no auth retry loops.
- Don't touch store lights or music.
