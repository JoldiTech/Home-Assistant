# NMT Print Agent

Prints pickup orders that the dashboard pushes as finished PDFs, on the
downtown HP LaserJet M203dw, and tells the dashboard whether paper came out.
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

## How it is reached

Only through the Cloudflare tunnel: `print-downtown.nmteaco.com` →
`http://local-nmt-print-agent:8080`, behind Cloudflare Access (service token
`dashboard-print`). No host port is mapped.

## Endpoints (both need `X-NMT-Timestamp` and `X-NMT-Signature`)

- `POST /print` `{"id", "order", "kind": "auto|reprint|test", "pdf": "<base64>"}` →
  `202` accepted, `200` duplicate (with its outcome), `503` printer can't print
  (`media-empty`, `media-jam`, `door-open`, `offline`, `unreachable`, …),
  `400 {"reason": "bad-request"}` (no PDF, bad base64, not `%PDF-`, over 5 MB),
  `401` bad signature or clock. The PDF is printed as sent; nothing is rendered.
- `GET /status` → agent, version, `formats: ["pdf"]`, printer
  reachability/state/reasons/page count, queue length.

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

## Log

One line per request (method, path, job id, answer) and one per job (id,
order number, outcome, page delta, seconds, error). Never job content or
header values.
