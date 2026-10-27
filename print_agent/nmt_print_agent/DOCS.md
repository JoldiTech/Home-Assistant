# NMT Print Agent (downtown store agent)

Prints pickup orders that the dashboard pushes as finished PDFs, on the
downtown HP LaserJet M203dw, and tells the dashboard whether paper came out.
Since 1.3.0 it also runs typed requests for the dashboard (`/v1`, below):
diagnostics and Revel caller-ID records to one register. Since 1.4.0 it also
runs CryptoCart's register box (below) for the counter scanners and receipt
printer. Printing keeps its
own route, queue and state file; a slow or failing request cannot hold it up.
The contract is `print_agent/INSTRUCTIONS.md` (Phase 3) as amended by
`print_agent/INSTRUCTIONS-UPDATE-PDF.md`, in the Home-Assistant repository.

## Options

| Option | Meaning |
| --- | --- |
| `dashboard_url` | Where results are sent: `<dashboard_url>/tools/webhook/printAgent.php` |
| `signing_key` | Shared HMAC key from the dashboard. Requests are refused until it is set |
| `agent_name` | Name reported in `/status` and results (`downtown`) |
| `printer_uri` | `ipp://192.168.22.238/ipp/print` |
| `printer_ip` | `192.168.22.238` (UniFi fixed IP) — used for the SNMP page counter |
| `snmp_community` | `public` |
| `listen_port` | `8080`. Keep it: the tunnel route and the watchdog both use 8080 |
| `lan_ip`, `lan_subnet` | Home Assistant's LAN address and network (reported in diagnostics; registers must be on `lan_subnet`) |
| `store_timezone` | `America/Denver`: the clock written into caller-ID records |
| `revel_caller_id_mode` | `off` (dry runs only), `test` (live only to a register marked `test`, with the test identity), `on` |
| `revel_caller_id_port` | `3520`, CallerID.com's default |
| `revel_caller_id_copies` | `1`. CallerID.com sends 8 copies 40 ms apart for Wi-Fi displays; whether Revel drops repeats is unverified |
| `revel_test_number` | `505-555-0100` (555-01xx numbers are fictional) |
| `revel_targets` | The registers: `id`, `name`, `ip`, `revel_station`, `test` |
| `cryptocart_company` | The company's CryptoCart address for the register box. Empty: the box stays off |

## How it is reached

Only through the Cloudflare tunnel: `print-downtown.nmteaco.com` →
`http://local-nmt-print-agent:8080`, behind Cloudflare Access (service token
`dashboard-print`). Port 8080 is never mapped to the host.

## Endpoints (both need `X-NMT-Timestamp` and `X-NMT-Signature`)

- `POST /print` `{"id", "order", "kind": "auto|reprint|test", "pdf": "<base64>"}` →
  `202` accepted, `200` duplicate (with its outcome), `503` printer can't print
  (`media-empty`, `media-jam`, `door-open`, `offline`, `unreachable`, …),
  `400 {"reason": "bad-request"}` (no PDF, bad base64, not `%PDF-`, over 5 MB),
  `401` bad signature or clock. The PDF is printed as sent; nothing is rendered.
- `GET /status` → agent, version, `formats: ["pdf"]`, printer
  reachability/state/reasons/page count, queue length.

## Typed requests (`/v1`, signed the same way)

- `GET /v1/capabilities`: handlers, payload rules, limits, HTTP codes.
- `GET /v1/targets`: configured registers and the last probe/send for each.
- `GET /v1/diagnostics`: cached and read-only (network mode, queues, recent
  error codes). Never probes, scans, prints or sends.
- `POST /v1/requests`: `{"version":1, "request_id", "type", "target",
  "dry_run": true|false, "expires_at": "…Z", "payload": {…}}` →
  `202` accepted, `200` exact repeat (stored answer, not run again), `409` id
  reused for other content (a dry-run id can never go live), `410` expired,
  `413`, `422` invalid, `429` rate limit, `503` unavailable/queue full.
- `GET /v1/requests/{request_id}`: state (`accepted`, `running`,
  `completed`, `dry_run_passed`, `sent`, `failed`, `uncertain`, `expired`),
  times (UTC), attempts, handler version, evidence, error code.

Handlers: `diagnostic.echo` (returns its challenge through the queue; sends
nothing), `diagnostic.probe` (3 pings to one register; no reply proves
nothing), `revel.caller_id` (one 83-byte CallerID.com Ethernet Link start
record by UDP unicast to one register, at most 120 s after it was asked
for). Records are kept 7 days in `/data/requests.json`; a finished record
keeps no payload. A request found mid-send after a restart is `uncertain`
and never sent again.

## Results and alerts

Each outcome is POSTed signed to the dashboard. A network error or 5xx is
retried 4 times (5 s, 30 s, 2 min, 5 min); a 4xx stops at once; `unknown: true`
stops quietly. Home Assistant gets a persistent notification when an order
fails, may not have printed, can't be reported, or when the printer blocks an
order (cleared when it recovers).

## Home Assistant entity

`sensor.instore_order_printing`, republished every 30 s and on every job change:
state `ready` / `printing` / `problem` / `unreachable`; attributes `problem`
(the blocking reason), `printer_state`, `reasons`, `page_count`, `queue`,
`last_jobs` (the three newest: id, order, kind, status, pages, error, time in
UTC) and `checked`. Order numbers only, never job content. The "Store State"
panel on the DowntownControls dashboard shows it.

## Register box (CryptoCart)

A second process beside the agent, each restarted alone by s6: CryptoCart's
`box.mjs` on Node 24, from the CryptoCart commit named in `CHANGELOG.md`.
Its contract is CryptoCart's `apps/pos/APP.md` section 8; nothing of it is
written here.

- It dials out to `wss://<cryptocart_company>/api/pos/box` once paired, and
  reads the counter scanners and prints receipts as POS's settings tell it.
- Registers on the shop's Wi-Fi reach it on host port **8443**, opened only
  once its first certificate arrives. The certificate's address is `lan_ip`.
- Pairing: a one-time code from POS's back office, sent as one line with the
  `hassio.addon_stdin` service (`addon: local_nmt_print_agent`).
- Its data is `/data/cryptocart/`. It logs JSON lines with an `event`, never a
  scanned code.
- It stays down, logging `box_down`, when `cryptocart_company` is empty or it
  refuses its options at start (a `lan_ip` off the shop's network). Fix the
  option and restart the add-on.

## Log

One line per request (method, path, job or request id, answer), one per job
(id, order number, outcome, page delta, seconds, error) and one per typed
request (id, type, target, state, error code). Never job content, a caller-ID
name or number, or header values.
