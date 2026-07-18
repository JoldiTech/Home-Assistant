# Home Assistant — Connection Self-Assessment

**Date:** 2026-07-18
**Result:** ✅ **Both** connection paths work — REST API **and** SSH.

> The full working runbook (SSH setup via the SessionStart hook, troubleshooting,
> camera/entity reference) lives in **[`CLAUDE.md`](CLAUDE.md)** — treat that as
> the source of truth. This file is the point-in-time assessment kept for the
> evidence snapshot.

---

## TL;DR

This session can reach Home Assistant two ways, both using credentials already in
the environment — no manual setup:

- **REST API** — `HOMEASSISTANT_BASE_URL` + `HOMEASSISTANT_TOKEN` (plain HTTPS).
  Use `scripts/ha` or `curl`.
- **SSH** — `cloudflared access ssh` + the `CF_ACCESS_CLIENT_ID` /
  `CF_ACCESS_CLIENT_SECRET` service token, landing as `root@core-ssh` (HAOS).

---

## Credentials available in the environment

Provided as environment variables (values not reproduced here):

| Variable | Purpose | Works? |
| --- | --- | --- |
| `HOMEASSISTANT_BASE_URL` | HA base URL (`https://ha.nmteaco.com`) | ✅ |
| `HOMEASSISTANT_TOKEN` | Long-lived access token (JWT) for the REST API | ✅ |
| `HA_SSH_HOST` | SSH host (`ssh.nmteaco.com`, behind Cloudflare Access) | ✅ |
| `HA_SSH_USER` | SSH user (empty → default `root`) | ✅ |
| `HA_SSH_KEY_B64` | base64-encoded ED25519 private key | ✅ |
| `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | Cloudflare Access service token | ✅ |

---

## Method 1 — REST API ✅

The token + base URL work with no configuration. Outbound HTTPS reaches the HA
host through the session's egress proxy.

```bash
# Health check
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" "$HOMEASSISTANT_BASE_URL/api/"
# -> {"message":"API running."}

# Read all entity states
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" "$HOMEASSISTANT_BASE_URL/api/states"

# Call a service (write)
curl -sS -X POST -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  -H "Content-Type: application/json" -d '{"entity_id":"light.example"}' \
  "$HOMEASSISTANT_BASE_URL/api/services/light/turn_on"
```

Verified (read-only calls):

- `GET /api/` → `200 {"message":"API running."}`
- `GET /api/config` → `200` — HA **2026.7.2**, America/Denver, RUNNING
- `GET /api/states` → `200`, **626 entities**
- `GET /api/services` → `200`, **67 service domains** (write path authorized)
- `GET /api/websocket` → `400` (endpoint present; expects a WebSocket upgrade)

## Method 2 — SSH ✅

`ssh.nmteaco.com` is **not** raw port-22 SSH — it sits behind **Cloudflare
Access** (port 22 times out; 443 is open). `cloudflared` is installed, so SSH is
tunneled over HTTPS via a `cloudflared access ssh` ProxyCommand, authenticated
with the Cloudflare Access service token.

```bash
# key: decode $HA_SSH_KEY_B64 to a 0600 file (append a trailing newline if missing)
export TUNNEL_SERVICE_TOKEN_ID="$CF_ACCESS_CLIENT_ID"
export TUNNEL_SERVICE_TOKEN_SECRET="$CF_ACCESS_CLIENT_SECRET"
ssh -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new \
    -o ProxyCommand="cloudflared access ssh --hostname %h" \
    "${HA_SSH_USER:-root}@$HA_SSH_HOST" 'ha core info'
```

Verified 2026-07-18: connected as `root@core-ssh`; `ha core info` returns
(HAOS, `aarch64`). The committed SessionStart hook
(`.claude/hooks/session-start.sh`) automates all of this, so `ssh homeassistant`
works from the first moment of a fresh session.

> **Correction:** an earlier revision of this file claimed SSH was "blocked /
> service token not present." That was wrong — the `CF_ACCESS_CLIENT_ID` /
> `CF_ACCESS_CLIENT_SECRET` service token **is** present, and SSH works. The 403
> seen initially was simply because the token had not been passed to cloudflared.

---

## Recommendation

Use the **REST API** for entity/service/state work (simplest — no tunnel). Use
**SSH** for OS/filesystem access and the `ha` CLI (Supervisor/add-on/host
control, which the REST API's long-lived token cannot reach). Full details in
[`CLAUDE.md`](CLAUDE.md).
