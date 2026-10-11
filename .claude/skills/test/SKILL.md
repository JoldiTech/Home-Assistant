---
name: test
description: Test every remote connection this environment depends on — HA web/API/SSH, the AI box over SSH, Cloudflare Access enforcement, store WANs, UPS, Protect, Chloe, the datalog token and Captain's Log freshness — and report what's broken and how to fix it. Use when the user types /test or asks to check/test the connections.
---

# /test — connection check

Run the read-only check script from the repo root and report the result:

```bash
bash scripts/test_connections.sh
```

It changes nothing on either box. Run it **once**. Do not re-run it, and do
not follow up with your own `curl`/`ssh` retries against Home Assistant: the
script makes exactly one authenticated HA API request because `ip_ban` is on
(5 failed authentications bans this sandbox's IP), and a retry loop with a
stale token is how that ban happens.

## Datalog token repair

If the report has a **FAIL datalog API** row and `DATALOG_API_TOKEN` is set in
this environment (the script WARNs when it isn't), run once:

```bash
bash scripts/sync_datalog_token.sh
```

It pushes the environment's token to the AI box's `/etc/nmteaco/captains.env`
over ssh stdin (never on a command line or in output), but only after the datalog
API accepts it, and only if the box's current token is failing. No restart is
needed. Report its one-line result; never print the token. If it's unset, tell
the user to add `DATALOG_API_TOKEN` in the environment settings (value: the
dashboard's `DATALOG_API_TOKEN`, API Credentials page) and start a new session.
Do not ask the user to paste it into chat.

## Reporting

Lead with one line: everything is up, or which connections are down.
Then a table of only the FAIL and WARN rows (all PASS → say so, no table),
each with the fix from the map below. Don't paste the raw output unless asked.

## What a failure means

| Row | Detail | Cause → fix |
|---|---|---|
| secrets present | missing: … | Add them in the environment settings (environment menu → Edit), then start a **new** session — env changes never reach a running one. |
| legacy var names | CF_ACCESS_CLIENT_ID/SECRET set | Pre-rename names; nothing reads them. Use `CF_ACCESS_CLIENT_ID_HA` / `_SECRET_HA`. |
| alias 'homeassistant' / 'aibox' | points at the wrong host | `HA_SSH_HOST` / `AI_BOX_SSH_HOST` changed, or the session started from a branch whose `session-start.sh` predates the rename. |
| web | 530 | HA's tunnel has no connector: the Cloudflared app on the HA Green is stopped. Restart it (HA → Settings → Apps → Cloudflared). It runs on a tunnel **token** (tunnel `ha-green`). |
| web | 403 | This IP is likely banned — remove it from `/config/ip_bans.yaml` over SSH and restart HA core. |
| REST API | 401 | `HOMEASSISTANT_TOKEN` is revoked/rotated. New long-lived token: HA profile → Security. |
| Access enforced | reaches SSH WITHOUT a token | The SSH hostname isn't in any Cloudflare Access application — the box is exposed to anyone with cloudflared. Add it in Zero Trust → Access → Applications. Treat as urgent. |
| Access enforced / SSH login | no DNS record | The tunnel's published route for that hostname is missing. |
| SSH login | Access rejected the service token | The ID/secret pair in this environment doesn't match a live token: the secret was rotated in Zero Trust but not here, or the ID and secret come from different tokens (each box has its own — `_HA` vs `_AI_BOX`). Compare both values against Zero Trust → Service credentials. If Chloe (public) passes, that box's tunnel is up and it's the credentials. |
| SSH login | box rejected the SSH key/user | HA user is `root`; AI box user is `nmteaco`. Same key (`HA_SSH_KEY_B64`) for both. |
| tunnel transport | not http2, or no protocol line | QUIC over this ISP causes multi-minute UI freezes. Set `run_parameters: ["--protocol=http2"]` on the Cloudflared app's Configuration page — never via the Supervisor options API, which would wipe the tunnel token. "No protocol line" just means the log rotated; not a fault by itself. |
| app: Cloudflared / Terminal & SSH | not started | Start it in HA → Settings → Apps. |
| service: … | inactive/failed | On the AI box: `sudo systemctl restart <name>` and read `journalctl -u <name>`. |
| AI box egress | failed over to the AT&T dongle | The store's primary WAN is down; the box is on its backup line. |
| UPS | ON BATTERY | Store power is out. The AI box shuts itself down at 50% / 10 min. |
| WAN1 / WAN2 | no monitor data or < 99% | That ISP link is down or flapping at the UDM. |
| HA -> UniFi Network / Protect | entities unavailable, or none | HA's UniFi integration can't reach the console (192.168.22.1): reload the config entry, check the console is up. SKIP "not installed" is informational — the AI box reads the UDM directly, so HA doesn't need the Network integration. |
| UniFi Network API (AI box) | no answer | The key in `/etc/nmteaco/protect.env` is invalid or the console is down. |
| GPU | nvidia-smi: NVML mismatch | The driver packages were upgraded but the old kernel module is still loaded. A reboot fixes it: `ssh aibox 'sudo reboot'` (after 20:30 MT, so it doesn't cut into the 19:00 Captain's Log run). A reboot brings itself back; WoL (`script.wake_ai_box`) is only for after a UPS **shutdown**. |
| datalog API | HTTP 401/403 | `DATALOG_API_TOKEN` in `/etc/nmteaco/captains.env` on the AI box doesn't match the dashboard's → run the repair above. Business numbers are missing from every log until fixed. |
| Captain's Log | latest day-file is old | A nightly run was missed. Recoverable from Protect for ~30 days via the dashboard's Create Transcript / Create Log buttons. |
| Chloe (public) | not 200 | The AI box's tunnel or `imagegen.service` is down. |

A SKIP is not a failure — it means a prerequisite was missing (e.g. no token
set, or HA web was down so the API check was not attempted).
