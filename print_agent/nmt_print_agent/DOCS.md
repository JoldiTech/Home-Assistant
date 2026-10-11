# NMT Print Agent

Prints pickup orders that the dashboard pushes, on the downtown HP LaserJet
M203dw, and tells the dashboard whether paper came out. The full contract is
`print_agent/INSTRUCTIONS.md` (Phase 3) in the Home-Assistant repository.

## Options

| Option | Meaning |
| --- | --- |
| `dashboard_url` | Where results are sent: `<dashboard_url>/tools/orders/curbsideAgent.php` |
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

- `POST /print` `{"id", "order", "kind": "auto|reprint|test", "html"}` →
  `202` accepted, `200` duplicate (with its outcome), `503` printer can't print
  (`media-empty`, `media-jam`, `door-open`, `offline`, `unreachable`, …),
  `401` bad signature or clock.
- `GET /status` → agent, version, printer reachability/state/reasons/page count,
  queue length.

## Log

One line per request (method, path, job id, answer) and one per job (id,
order number, outcome, page delta, rendered pages, seconds, error). Never job
content or header values.
