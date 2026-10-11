# Downtown print agent: Phases 2–4

Done 2026-10-11 (MT morning, store closed). Phase 1 report approved by the
owner first. Contract: `INSTRUCTIONS.md`.

**Printer fixed IP for the curbside tablet's Fix Printer Issues page:
`192.168.22.238`.**

## Phase 2: network and tunnel

### Settings changed

| What | Before | After |
| --- | --- | --- |
| UniFi client `5c:fb:3a:0d:80:7e` (`_id 659728f49a62e95d43422b61`, hostname NPI02EFF1): `use_fixedip` | unset | `true` |
| … `fixed_ip` | unset | `192.168.22.238` |
| … `network_id` | unset | `5e13358d4c7ad505143f64ac` (Default) |
| … `noted` | unset | `false` (added by UniFi on the write, no effect) |

Nothing else changed, in UniFi or anywhere else in Phase 2.

**Call** (from the AI box, key from `/etc/nmteaco/protect.env`, not shown):

```
PUT https://192.168.22.1/proxy/network/api/s/default/rest/user/659728f49a62e95d43422b61
{"use_fixedip":true,"fixed_ip":"192.168.22.238","network_id":"5e13358d4c7ad505143f64ac"}
→ HTTP 200 {"meta":{"rc":"ok"}, …}
```

**Before** (GET of the same record): `last_ip 192.168.22.238`,
`last_connection_network_id 5e13358d…` (Default), Wi-Fi (`is_wired false`,
uplink U7 Pro Max), no `use_fixedip`/`fixed_ip`/`network_id` keys. The live
client list showed the printer at .238, connected for ~19.6 days.

**Pre-checks:** looked up by MAC, not name or IP. Fixed IPs on the site before
the change: DesignPC .192, homeassistant .254, Ai Server .6. Nobody held .238.

**Read-back:** a fresh GET returns `use_fixedip true`, `fixed_ip
192.168.22.238`, `network_id 5e13358d4c7ad505143f64ac`; the site's fixed-IP list
now has four entries including the printer; the printer answered ping straight
after. Its address didn't change, so no restart was needed.

### Not changed (not needed)

- **HA box IP:** already a UniFi fixed IP (192.168.22.254).
- **Firewall:** HA box and printer share the Default network (192.168.22.0/24).
- **SNMP:** already on, read-only community `public`.
- **Cloudflare:** the owner's setup, untouched.

### Credentials

The existing console API key that the AI box already uses for Protect and
Network (`/etc/nmteaco/protect.env`). No new key or account was created. UniFi
API keys aren't scoped (the same key works for Protect, Network and the legacy
API), so a less-privileged key isn't available. A local read/write account
limited to Network would be the narrowest option if the owner wants to separate
it.

### Tunnel and hostname (Phase 2, step 4)

- The Supervisor gave the add-on the hostname **`local-nmt-print-agent`**, as
  the route expects, so no route change is needed. Add-on IP on the hassio
  network: 172.30.33.3.
- From the HA box, `http://local-nmt-print-agent:8080/status` answers: 401
  unsigned, 200 signed.
- `https://print-downtown.nmteaco.com/status` without the token gets **403**
  from Cloudflare Access (`cf-access-domain: print-downtown.nmteaco.com`).

## Phase 3: the add-on

Source: `nmt_print_agent/` (`config.yaml`, `build.yaml`, `Dockerfile`,
`DOCS.md`, `agent/agent.py`, `agent/ipp.py`, `agent/snmp.py`). It's installed
on the box from `/local_apps/nmt_print_agent` as `local_nmt_print_agent`
v1.0.0: `boot: auto`, watchdog on (TCP 8080), state in `/data`. Options are set
with the signing key (64 characters) and the Phase 1 printer values.

**How a job goes:** `POST /print` is checked (signature, duplicate, printer
state) and answered in ~15 ms. The HTML is held in `/data/spool/<id>.html`
until the job ends. The worker then:
1. marks the job `printing` (fsynced);
2. renders it with WeasyPrint to a Letter PDF (Letter is forced even if the
   HTML asks for A4);
3. reads the SNMP counter;
4. runs `lp` to the driverless CUPS queue (`-m everywhere`, error policy
   `abort-job`);
5. waits for the CUPS job (≤ 3 min);
6. polls the counter until it's stable for 10 s (≤ 60 s);
7. records the outcome and queues the result in one write, then deletes the
   HTML and PDF.

Results are POSTed signed to `/tools/orders/curbsideAgent.php` and retried
after 5 s, 30 s and 2 min, then every 5 min, until the dashboard answers
`{"ok": true}`. The undelivered list lives in `/data` and survives restarts
and reboots.

**Signing details** the dashboard side must match:
- The HMAC key is the signing key's UTF-8 bytes, as PHP
  `hash_hmac('sha256', $msg, $key)` does.
- PATH is the URL path without any query string.
- The signature is lowercase hex; the agent accepts either case.

**Printer state** comes from IPP `printer-state` / `printer-state-reasons`:
- Suffixes are stripped, and synonyms are folded into the plan's names:
  `media-needed` → `media-empty`; `cover-open` / `interlock-open` → `door-open`.
- Reasons ending `-warning` or `-report` (e.g. today's `toner-low`) never block.
- A blocking reason, or the printer being unreachable, gives 503 on `/print`
  and holds the queue. Held jobs are reported `failed` with that reason after
  10 minutes.

## Phase 4: test results

Test job ids **2100000001 and up** keep test ids away from the dashboard's real
job ids. A collision would make a real order answer "duplicate" and never print.

| # | Test | Result |
| --- | --- | --- |
| 1 | Normal print | ✅ 202 in 0.015 s; one sheet; `printed`, counter 7163 → 7164, 21 s end to end |
| 2 | Duplicate (same id) | ✅ `200 {"accepted":true,"duplicate":true,"outcome":"printed"}`; counter stayed 7164 |
| 3 | Bad signature / timestamp 400 s old / unsigned / no Cloudflare token | ✅ 401, 401, 401 from the agent, nothing queued; 403 from Cloudflare without the token |
| 4 | Out of paper | ⏳ needs someone at the printer. Covered by the e2e and unit tests: a blocking reason → 503, nothing queued |
| 5 | Door open mid-queue | ⏳ needs someone at the printer. Covered by e2e: held job printed once on release, `failed` after the grace period, never printed |
| 6 | Printer powered off | ⏳ needs someone at the printer. Covered by e2e: `reachable:false`, `/print` 503 `unreachable` in < 5 s |
| 7 | Restart mid-job | ✅ the add-on restarted ~9 s after accepting; on start the job was reported `uncertain`, "agent restarted during the job", page delta 1; counter 7165 and stayed there; resending the id → duplicate `uncertain` |
| 8 | Reboot the HA box | ✅ `ha host reboot` at 05:40:56 MT. HA web back at 05:43:49; cloudflared started; the agent started by itself at 05:45:04 with no job lost, and its two undelivered results kept retrying. Signed `/status` → 200; tunnel without token → 403. ⏳ A 200 through the tunnel needs the `dashboard-print` token, which isn't in this environment |
| 9 | Dashboard unreachable | ◐ effectively live: `curbsideAgent.php` returns **404** today, and the agent is retrying both test results on schedule (5 s, 30 s, 2 min, then every 5 min) across a restart and a reboot. Delivery once access returns: ✅ in the e2e test, delivered exactly once. Live, it can only be confirmed once the dashboard endpoint exists |
| 10 | Real order Reprint | ⏳ needs staff at the printer, after the dashboard side is live |

The **page counter tracks paper**: 7163 → 7164 → 7165 for the two test sheets.
That stands in for the Phase 1 second reading, which was waiting on a staff
Reprint.

**Local test suites** (in the repo, rerunnable):
- `tests/test_agent.py`: 13 unit tests, all pass.
- `tests/e2e.py`: 22 checks, all pass. They run the real image against a fake
  CUPS printer, a fake SNMP counter and a fake dashboard. Cases:
  - normal, duplicate, refusals;
  - printer off;
  - dashboard down then back;
  - restart mid-job;
  - in-flight job timing out → `uncertain`;
  - queue held then released (prints once);
  - held past the grace period (→ `failed`, never printed);
  - logs free of job content, key and signatures; spool empty afterwards.

## For the dashboard side

- **Answer `{"ok": true}` for an unknown job id.** Results for test jobs
  **2100000001** and **2100000007** are queued on the agent now and are retried
  every 5 minutes until accepted. Until then they hit
  `/tools/orders/curbsideAgent.php` and get the current 404.
- `uncertain` results come from two cases: a restart mid-job, and a job still
  unfinished after 3 minutes. Neither should be resent automatically. Show
  them to staff.
- The agent answers duplicates from its last 1000 job ids. Don't reuse an id
  for a different print; that's already the plan's rule for retries.

## Where the plan didn't fit, and what was done instead

1. **Add-on folder** is `/local_apps/nmt_print_agent`, not
   `/addons/local/nmt_print_agent`. The slug and hostname are as planned.
2. **Printer state** comes from IPP, not SNMP `hrPrinterStatus`, which reads
   `other` while the M203 sleeps. SNMP is used only for the page counter, since
   IPP has none.
3. **A job that times out (3 min) is `uncertain`, not `failed`.** The plan's
   `failed` gets resent under a new id. A job that reached the printer and then
   stalled (a jam, a network blip) can still print later, so `failed` would risk
   a double print. `failed` is used only when CUPS/the printer aborted the job and
   the counter didn't move, or for held jobs that never reached CUPS.
4. **`listen_port`** is an option, as the plan asks, but must stay 8080: the
   tunnel route and the watchdog both use 8080.
5. **`build.yaml`** works on this Supervisor but is logged as deprecated. The
   Dockerfile already defaults `BUILD_FROM` to the aarch64 Debian base, ready
   for when it's removed.
6. **The watchdog** had to be switched on separately (Supervisor API
   `{"watchdog": true}`, not touching the options). `config.yaml` only declares
   it.
7. **Fetching resources:** WeasyPrint may load only `data:` URIs and `https`
   files from the dashboard's own host. Anything else is refused, so a job
   can't make the box contact a third party.

## Still open

- Tests 4, 5, 6 and 10 (someone at the printer).
- A 200 through the tunnel (test 8 completion), from the dashboard or with the
  `dashboard-print` token added to this environment as
  `CF_ACCESS_CLIENT_ID_PRINT` / `CF_ACCESS_CLIENT_SECRET_PRINT`.
- Live confirmation of test 9 once `curbsideAgent.php` exists.
- Unchanged from Phase 1: toner at 2 %, no printer admin password, printer on Wi-Fi.
