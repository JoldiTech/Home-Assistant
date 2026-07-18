# Home Assistant — Connection Self-Assessment

**Date:** 2026-07-18
**Result:** ✅ Connectable out of the box via the **REST API**. SSH is provisioned
but gated behind Cloudflare Access and needs an extra credential that is not
present in this environment.

---

## TL;DR

Yes — this session can talk to Home Assistant without any setup. The REST API
credentials are already in the environment and work immediately. Use the
`scripts/ha` helper (or `curl` directly) with `HOMEASSISTANT_BASE_URL` +
`HOMEASSISTANT_TOKEN`.

---

## Credentials available in the environment

These are provided as environment variables (values not reproduced here):

| Variable                 | Purpose                                          | Usable? |
| ------------------------ | ------------------------------------------------ | ------- |
| `HOMEASSISTANT_BASE_URL` | HA base URL (`https://ha.nmteaco.com`)           | ✅ yes  |
| `HOMEASSISTANT_TOKEN`    | Long-lived access token (JWT) for the REST API   | ✅ yes  |
| `HA_SSH_HOST`            | SSH host (`ssh.nmteaco.com`, behind Cloudflare)  | ⚠️ gated |
| `HA_SSH_USER`            | SSH user (`root`)                                | ⚠️ gated |
| `HA_SSH_KEY_B64`         | base64-encoded ED25519 private key               | ⚠️ gated |

---

## Method 1 — REST API ✅ (recommended, works today)

The token + base URL work with no additional configuration. Outbound HTTPS is
allowed to the HA host through the session's egress proxy.

```bash
# Health check
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_BASE_URL/api/"
# -> {"message":"API running."}

# Read all entity states
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_BASE_URL/api/states"

# Call a service (write) — example shape, not executed during assessment
curl -sS -X POST -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"entity_id":"light.example"}' \
  "$HOMEASSISTANT_BASE_URL/api/services/light/turn_on"
```

Verified during this assessment (read-only calls):

- `GET /api/`        → `200 {"message":"API running."}`
- `GET /api/config`  → `200` (version, location, components)
- `GET /api/states`  → `200`, **626 entities**
- `GET /api/services`→ `200`, **67 service domains** (write path is authorized)
- `GET /api/websocket` → `400` (endpoint present; expects a WebSocket upgrade)

The token also grants the WebSocket API (`/api/websocket`) for real-time /
event-driven use.

## Method 2 — SSH ⚠️ (provisioned, but blocked by Cloudflare Access)

`ssh.nmteaco.com` resolves to a **Cloudflare** address. Findings:

- **Port 22 is not reachable** (times out) — raw SSH does not apply.
- **Port 443 is open**, and `cloudflared` (v2026.7.x) is installed, so the
  intended path is SSH-over-HTTPS via a `cloudflared access ssh` ProxyCommand.
- That path currently returns **HTTP 403 / `websocket: bad handshake`**. The
  host responds with `cf-access-domain: ssh.nmteaco.com` and a
  `cf-access-aud` header — i.e. the app sits behind **Cloudflare Access**.

To use SSH, one of the following must be added to the environment:

- A **Cloudflare Access service token** (`CF-Access-Client-Id` +
  `CF-Access-Client-Secret`) for a non-interactive service login, or
- An interactive `cloudflared access login` (needs a browser, not available in
  a headless session).

Reference ProxyCommand once a service token is available:

```bash
# Write $HA_SSH_KEY_B64 to a 0600 key file first, then:
ssh -i "$KEY" -o ProxyCommand="cloudflared access ssh --hostname %h" \
    "$HA_SSH_USER@$HA_SSH_HOST"
```

---

## Recommendation

Use the **REST API** for all interaction with this Home Assistant instance —
reading state, calling services, and (via `/api/websocket`) subscribing to
events. It requires no setup. Reserve SSH for host-level admin tasks and only
after a Cloudflare Access service token is provisioned.
