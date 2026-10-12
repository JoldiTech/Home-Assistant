# Store agent and Revel alert: completion report

Done 2026-10-12, 03:40–04:10 MT (store closed). Brief:
`NM_Tea_Store_Agent_Revel_Handoff.md` (12 October 2026).

**Status in one line:** the agent is **deployed on Green (1.3.0)** and the
Datalog bridge is **live on the dashboard** (merged to `main` as `bd3c8da`,
2026-10-12 10:12 UTC). The safe remote acceptance run through Datalog
passed **18/18** (run `dash:20261012T101237:1186b8`). Printing is
unaffected. No live caller-ID record has been sent to a real register, and
Revel would ignore one today anyway (see Revel below).

## Versions and where things are

| | |
|---|---|
| Home-Assistant repo | branch `claude/bold-davinci-uxje1c` (built on `ccr-7d0e6fa1-2ia932`, which held the deployed 1.2.1 source byte-for-byte) |
| Green | add-on `local_nmt_print_agent` **1.3.0**, started, boot auto, watchdog on |
| Dashboard | New-Mexico-Tea-Company-Server `main` `bd3c8da` (branch `claude/store-agent-bridge`, commit `9f60fb0`): `dashboard/tools/datalog/store_agent.php`, `docs/store-agent-test.py`, CLAUDE.md |
| Agent URL | `https://print-downtown.nmteaco.com` (unchanged tunnel route `http://local-nmt-print-agent:8080`, Access-protected) |
| Bridge URL | `https://dashboard.nmteaco.com/tools/datalog/store_agent.php` (live) |

## Credentials (names only)

| Dashboard | Green / elsewhere | Used for |
|---|---|---|
| `CURBSIDE_AGENT_URL` | — | the agent hostname above |
| `CURBSIDE_AGENT_CF_CLIENT_ID` / `_SECRET` | Cloudflare Access service token `dashboard-print` (never reaches Green) | every request through the tunnel |
| `CURBSIDE_AGENT_SIGNING_KEY` | add-on option `signing_key` | HMAC both ways, unchanged |
| `DATALOG_API_TOKEN` | — | the bridge's bearer |

Nothing was rotated or renamed. No secret is in source, examples or
responses. (One slip during the survey: a Supervisor `ha apps info` dump
showed the add-on options, including `signing_key`, in this session's own
tool output. It was not committed, logged elsewhere or sent anywhere, but it
did pass through the session transcript. Rotate it if that matters to you;
rotating needs both the dashboard's API Cred page and the add-on option.)

## Agent API (1.3.0)

Same authentication on every route: Access service token at Cloudflare,
then `X-NMT-Timestamp` + `X-NMT-Signature` = HMAC-SHA256 over
`timestamp\nMETHOD\npath\nbody` (empty body for GET), ±300 s, constant-time
compare. `Cache-Control: no-store`. The `/v1` routes refuse query strings, so
the signed path is the sent path.

| Route | |
|---|---|
| `GET /status` | unchanged fields (`agent`, `version`, `formats`, `printer`, `queue`) plus additive `agent_api: 1`, `handlers` |
| `POST /print` | unchanged |
| `GET /v1/capabilities` | handlers, payload rules, limits, HTTP codes, error shape |
| `GET /v1/targets` | configured registers, last probe/send each |
| `GET /v1/diagnostics` | cached, read-only: network mode, LAN facts, queues by handler, oldest work, recent error codes |
| `POST /v1/requests` | submit (below) |
| `GET /v1/requests/{request_id}` | durable state and sanitized evidence |

**Envelope:** `{"version":1, "request_id": "8–128 of A-Z a-z 0-9 . _ : -",
"type", "target", "dry_run": true|false (required), "expires_at":
"…Z", "payload": {…}}`. Unknown fields are refused.

**Codes:** 202 accepted · 200 status / exact repeat (`duplicate: true`) ·
401 signature/clock · 404 unknown id · 409 `request_id_conflict` · 410
`expired` · 413 `body_too_large` · 422 `invalid_json`, `unknown_field`,
`unsupported_version`, `invalid_request_id`, `unknown_handler`,
`dry_run_required`, `invalid_target`, `invalid_expires_at`,
`invalid_payload`, `ttl_too_long`, `live_disabled`, `live_not_permitted` ·
429 `rate_limited` · 503 `handler_unavailable`, `queue_full`.
Error shape: `{"agent_api":1,"error":{"code","message"}}`.

**Semantics:** a request is persisted (fsync) before it can run, bound to the
SHA-256 of its whole validated content (type, target, dry_run, expiry,
payload). Exact repeat → stored answer, never run again; other content under
the id → 409, so a dry-run id can never go live. Expiry is checked at accept
and again in the instant before a send. A send is marked durably *before*
the bytes go; a restart finds it `uncertain` and never replays it (a request
started but not yet at the wire becomes `failed`, one not yet started is
re-queued). Records kept **7 days** (max 5,000); a finished record keeps no
payload. One worker and one bounded queue per handler; printing has its own.

| Handler | Max TTL | Rate | Queue | Sends |
|---|---|---|---|---|
| `diagnostic.echo` (target `agent`, payload `{"challenge": 8–64 of A-Z a-z 0-9 _ -}`) | 600 s | 30/min | 50 | nothing |
| `diagnostic.probe` (a register, payload `{}`) | 600 s | 6/min | 10 | 3 pings; no reply proves nothing |
| `revel.caller_id` (a register, payload `{"name","number"}`) | **120 s** | 6/min | 20 | one UDP record to that register |

**Caller-ID payload:** `name` 1–15 characters of `A-Z a-z 0-9 space . , & ' # / -`,
not starting with a space; **longer names are refused, never cut** (the
record's field is 15). `number` 10 digits, `5055550100` or `505-555-0100`,
NANP-valid; sent as `505-555-0100`. Mode `test` (deployed): live only to a
register marked `test`, only with a name starting `TEST` and the number
`505-555-0100`. A dry run returns the packet only for that synthetic
identity; otherwise only its shape.

## Revel caller ID: what was established

| Question | Answer | Basis |
|---|---|---|
| Packet | CallerID.com Ethernet Link basic inbound start record: `^^<U>` + 6 binary bytes + `<S>` + 6 bytes + `$` (21) then `01 I S 0000 G A1 MM/DD hh:mm AM <number 14> <name 15>` (62 ASCII), 83 bytes, no CR/LF | CallerID.com EL Manual v3.1; byte layout of CallerID.com's own Ethernet Emulator source; two independent parsers |
| Port | UDP 3520 | same |
| Direct vs broadcast | Real units broadcast to 255.255.255.255; unicast to one IP is a documented unit setting. **Whether Revel's iPad app accepts unicast is not documented** | EL manual |
| Can Green reach the register | Unicast yes (container is NATed out through 192.168.22.254; same network, WLAN client isolation off, verified via UniFi). **Broadcast no**: the add-on is on HA's internal bridge (172.30.33.3), so its broadcasts never reach the LAN. Networking was not changed | `/v1/diagnostics`; UniFi read |
| Revel setting | `PosStation.caller_id`, per station. **false on all 15 stations** on 2026-10-12. Read-only in Revel's API; old Revel docs say Revel Support turns it on per station | Revel PosStation read (1 API call); Revel OpenAPI |
| How Revel shows it | An "Incoming Call" pop-up with **Hang up** (dismiss) and **Answer**; Answer opens a Customer window pre-filled with the number, then an order | Revel support article excerpts (medium confidence; the current article could not be fetched) |
| Sound | **Not documented; unobserved** | — |
| Creates/modifies records | Answer opens a customer/order flow; whether a customer is saved without the operator is **unverified**. Tests must use Hang up | as above |
| Licensing / support | Revel documents only real CallerID.com hardware ("Whozz Calling?" Ethernet). The emulator is a CallerID.com test tool; no licence found that permits or forbids an emulator-style sender in production. **Revel support for this setup is not established** | CallerID.com legacy software page; Revel excerpts |
| Wi-Fi duplicates | CallerID.com sends 8 copies 40 ms apart for wireless displays; Revel's de-duplication unknown, so the agent sends 1 (option `revel_caller_id_copies`) | CallerID.com install guide; emulator |

## Registers

| Target id | Revel station | Name | IP (configured) | Observed 2026-10-12 |
|---|---|---|---|---|
| `pink-ipad` (**test target**) | 14 | Pink Ipad | 192.168.22.91 | UniFi: on "NM Tea Co." Wi-Fi, seen 17 s earlier; Revel IP matches; Revel last used 2026-10-11 23:56 |
| `blue-ipad` | 12 | Blue Ipad | 192.168.22.190 | UniFi: same; Revel IP matches; last used 2026-10-11 23:49 |

Both use private Wi-Fi MACs and **DHCP without reservations**: the
configured IP can go stale. `store_agent.php?action=targets&revel=1` shows
Revel's recorded station IPs (recorded, not live) next to the agent's. Not
configured: "Mobile Station" (station 1, Revel last used 2026-09-16; .93 is
an iPad on the network but which one is unknown). Neither iPad answered ping
at 04:00 (asleep); that does not mean offline.

## Acceptance results

**On Green, over the LAN** (signed with the deployed key from inside the
box; run prefix `lan-10120958-ce4096`):

| Check | Result |
|---|---|
| capabilities / targets / diagnostics | 200, as above |
| echo through the queue | 202 → `completed`, challenge returned, attempts 1 |
| exact repeat | 200 `duplicate: true`, attempts still 1 |
| same id, changed payload | 409 `request_id_conflict` |
| unknown handler / unknown target / 22-char name / expired / no dry_run / version 9 | 422 / 422 / 422 / 410 / 422 / 422, nothing stored |
| unknown request id | 404 `unknown_request` |
| caller-ID dry run → `pink-ipad` | `dry_run_passed`: 83 bytes, recipients 1, "pink-ipad (Pink Ipad) only, no broadcast", port 3520, self-check ok, **outbound_sends 0**; local time in record (03:58 AM) |
| that dry-run id sent live | 409 |
| live to `blue-ipad` (not test) | 422 `live_not_permitted` |
| probe pink-ipad / blue-ipad | `completed`, 3 sent, 0 replies (asleep) |
| `/status` | 1.3.0, formats `["pdf"]`, queue 0 |

**Dashboard → agent, end to end, locally:** the real `store_agent.php` and
`curbsidePrint.php` (PHP 8.3; only the database and auth includes stubbed)
against the real 1.3.0 handler over TLS behind a stand-in Access check,
driven by `docs/store-agent-test.py --live --target pink-ipad`: **21/21
passed**, including the live send (one 83-byte record received by a local UDP
listener) and its repeat (nothing more received). Failure classification:
wrong service token → 502 `reached: cloudflare`; wrong signing key → 401
`reached: agent, agent_refused_signature`; agent down → 502 `reached: none`;
bad bearer → 401. No secret in either log.

**Unit tests:** `print_agent/tests/test_agent.py` (25, the print regression
suite, unchanged and passing) and `print_agent/tests/test_store_api.py` (30):
auth on every route, path binding, idempotency, 409/410/413/422/429/503,
dry run sends nothing, test-mode limits, port-unreachable → `failed`,
customer identity never echoed, restart mid-send → `uncertain` and not
replayed (isolated fixture), expiry re-checked before send, a stuck handler
blocks neither echo nor printing, reads change nothing.

**Against the handoff's remote sequence (through Datalog):**

Run `dash:20261012T101237:1186b8`, `python3 docs/store-agent-test.py` from
this sandbox with only `DATALOG_API_TOKEN`: **18/18 passed.**

| Step | State |
|---|---|
| 1 reads | status (1.3.0, `pdf`), capabilities, targets, diagnostics: all `reached: agent`, 200 |
| 2 echo | 202 → `completed`, challenge returned (`…:echo:1`) |
| 3 repeat / reuse | exact repeat 200 `duplicate`, attempts 1; changed content 409 |
| 4 rejections | unknown handler 400 (bridge allowlist), unknown target 422, 19-char name 422, expired 410, no dry_run 400, version 9 422 |
| 5 dry run | `dry_run_passed`, pink-ipad only, port 3520, 83 bytes, 0 sends; its id refused live (409) |
| 7 live repeat | proven in the end-to-end harness; on a real register, with step 6 |
| 9 print compatibility | `/status` fields intact through the bridge and the dashboard's own print client |
| 10 unauthenticated | bad bearer 401 at the bridge; unsigned 401 at the agent (unit tests) |
| 6 (one live test) | **Not run.** Needs the bridge (or a LAN run), an explicit go-ahead, Revel caller ID turned on for station 14, and a person at the Pink Ipad |
| 8 (restart/uncertain) | Done with the isolated fixture (unit test), as the brief asks |

## At the register: unobserved

Nothing has been sent live to a real register. Whether Revel shows a pop-up
from a unicast record, whether it sounds, whether Hang up dismisses it
cleanly and whether Answer creates anything are **all unobserved**. They need:
1. Revel caller ID enabled on **station 14 (Pink Ipad) only** (Revel Support
   or the console; it is false everywhere now). Record that it was false.
2. Someone at the Pink Ipad, Revel open in the foreground.
3. `python3 docs/store-agent-test.py --live --target pink-ipad` from the
   dashboard AI, then that person reports: pop-up yes/no, sound yes/no, Hang
   up dismissed it yes/no. **Do not press Answer.**

If nothing appears with caller ID on, the likely cause is that Revel only
listens for broadcasts. A broadcast needs the add-on on the host network
(`host_network: true`). That would move it off `local-nmt-print-agent:8080`
and so needs the tunnel route changed too. Not done; it is a separate
decision.

## What changed, and rollback

| Where | Change |
|---|---|
| Green | add-on 1.2.1 → **1.3.0**; new options took their defaults (`signing_key` and the rest untouched, verified length 64); new file `/data/requests.json` |
| Green | HA backup **`d2654eba`** "nmt_print_agent 1.2.1 before 1.3.0" (add-on, its options and `/data`); source copy `/share/nmt_print_agent-1.2.1-src/` |
| Revel, UniFi, Cloudflare, HA automations, lights, music, cameras | **nothing** |

Rollback: `ssh homeassistant 'ha backups restore d2654eba --app local_nmt_print_agent'`
(or copy `/share/nmt_print_agent-1.2.1-src/.` back to
`/local_apps/nmt_print_agent/`, `ha store reload`, `ha apps rebuild
local_nmt_print_agent`). 1.2.1 never reads `requests.json`, and the print
state file kept its format, so either direction keeps the print queue.

## Print service

The dashboard read `/status` through the tunnel straight after the upgrade:
1.3.0, `formats ["pdf"]`, printer reachable and idle, queue 0, so it keeps
sending orders to the agent. No page was printed for testing. **No automatic
curbside → Revel forwarding exists**: nothing in the dashboard calls the
caller-ID handler, and the agent's mode is `test`.

## Remaining limitations

- Revel caller ID is off everywhere and can't be switched on through the API.
- Revel behavior (pop-up, sound, Hang up, Answer) is unobserved.
- Unicast acceptance by Revel is unverified; broadcast is impossible from
  the add-on's current network without a tunnel-route change.
- Production use of an emulator-style sender has no stated Revel or
  CallerID.com support.
- Register IPs are DHCP without reservations.
- iPads asleep or with Revel in the background will not receive anything.
