# Home Assistant remote-access runbook

This environment exists so Claude Code can reach a self-hosted **Home Assistant**
instance for diagnostics, testing, and automation. Everything needed to connect
is documented below — it has already been figured out, so follow it directly
instead of re-deriving it.

> **Secrets live in the environment's secret store, not in this file.** Only
> variable *names* and non-secret values (hostnames, the login user) appear here.
> Never commit the private key, the API token, or the Cloudflare service-token
> secret to the repo.

---

## When to use the REST API vs SSH

Two independent ways to reach the box. Pick by *what you're doing*:

| Use the **REST API** (default) | Use **SSH** |
| --- | --- |
| Read/set entity states (lights, sensors, switches, climate…) | Shell / OS-level access to the HAOS host or an add-on container |
| Call services (turn_on/off, set values, trigger automation/script/scene) | Edit YAML config on disk (`configuration.yaml`, `automations.yaml`) |
| Query config, history, logbook, events, render templates | Manage files, logs, backups at the filesystem level |
| Anything HA exposes over HTTP | Run the `ha` CLI (core/supervisor/add-on/host control, restarts) |
| Simplest & fastest — plain HTTPS, no tunnel, nothing to install | Anything the REST API does **not** expose |

**Rule of thumb:** reach for the **API first** — it's simpler and needs no tooling.
Drop to **SSH** when you need the operating system, the filesystem, or Supervisor
control. Note the Supervisor REST endpoints (`/api/hassio/*`) return **401** for
long-lived tokens, so **add-on / Supervisor / host management must go through SSH**
(via the `ha` CLI), not the API.

---

## Environment variables

| Variable | Sensitive | Meaning / value |
| --- | --- | --- |
| `HOMEASSISTANT_BASE_URL` | no | `https://ha.nmteaco.com` — HA web UI + REST API base |
| `HOMEASSISTANT_TOKEN` | **yes** | HA long-lived access token (REST API auth) |
| `HA_SSH_HOST` | no | `ssh.nmteaco.com` — the Cloudflare Access SSH hostname |
| `HA_SSH_USER` | no | SSH login user. **May be empty — default to `root`** |
| `HA_SSH_KEY_B64` | **yes** | base64 of the ed25519 private key |
| `CF_ACCESS_CLIENT_ID` | **yes** | Cloudflare Access service-token ID (`*.access`) |
| `CF_ACCESS_CLIENT_SECRET` | **yes** | Cloudflare Access service-token secret |

---

## Architecture

- The box is a **Home Assistant Green** running **HAOS** (`aarch64`) on a home network.
- It is exposed through a **Cloudflare Tunnel + Cloudflare Access (Zero Trust)** —
  there are **no open inbound ports** at home.
- Two hostnames ride the **same tunnel**:
  - `https://ha.nmteaco.com` → HA web UI / REST API (origin: HA core, port 8123)
  - `ssh.nmteaco.com` → SSH, **Access-protected** (tunnel ingress: `ssh://core-ssh:22`,
    the *Terminal & SSH* add-on)
- **`ssh.nmteaco.com` is NOT raw SSH on port 22.** It is SSH wrapped in an HTTPS
  WebSocket behind Cloudflare Access. You **must** tunnel through `cloudflared` and
  authenticate with the service token — a plain `ssh -p 22` will not work.

---

## Connect over SSH (copy-paste)

Requires `ssh` + `cloudflared` (the environment setup script installs both; the
guards below self-install if missing).

```bash
# 1) Tooling (idempotent). cloudflared comes from Cloudflare's apt repo —
#    GitHub releases are out of scope in this environment.
command -v ssh >/dev/null || { apt-get update -qq && apt-get install -y --no-install-recommends openssh-client; }
command -v cloudflared >/dev/null || {
  tmp=$(mktemp -d); base=https://pkg.cloudflare.com/cloudflared
  fn=$(curl -fsSL "$base/dists/any/main/binary-amd64/Packages" | awk '/^Filename:/{print $2; exit}')
  curl -fsSL -o "$tmp/cf.deb" "$base/$fn" && apt-get install -y "$tmp/cf.deb"; rm -rf "$tmp"
}

# 2) Materialize the private key. The trailing-newline line is REQUIRED —
#    without it OpenSSH rejects the key with "error in libcrypto".
mkdir -p ~/.ssh && chmod 700 ~/.ssh
printf '%s' "$HA_SSH_KEY_B64" | base64 -d > ~/.ssh/ha_ssh_key
[ "$(tail -c1 ~/.ssh/ha_ssh_key | od -An -tx1 | tr -d ' ')" = 0a ] || printf '\n' >> ~/.ssh/ha_ssh_key
chmod 600 ~/.ssh/ha_ssh_key

# 3) ProxyCommand wrapper: cloudflared + service token, with the MITM proxy
#    stripped (cloudflared needs a direct TLS path to Cloudflare's edge on :443).
cat > ~/.ssh/ha_cf_proxy.sh <<'EOF'
#!/bin/bash
exec env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy \
  cloudflared access ssh --hostname "$1" \
    --service-token-id "$CF_ACCESS_CLIENT_ID" \
    --service-token-secret "$CF_ACCESS_CLIENT_SECRET"
EOF
chmod 700 ~/.ssh/ha_cf_proxy.sh

# 4) Connect (user defaults to root when HA_SSH_USER is empty).
ssh -i ~/.ssh/ha_ssh_key \
  -o "ProxyCommand=$HOME/.ssh/ha_cf_proxy.sh %h" \
  -o StrictHostKeyChecking=accept-new \
  -o IdentitiesOnly=yes \
  "${HA_SSH_USER:-root}@${HA_SSH_HOST}" 'echo "connected: $(whoami)@$(hostname)"; ha core info'
```

For an interactive/repeat session you can drop the same values into `~/.ssh/config`
as a `Host homeassistant` alias and then just `ssh homeassistant`.

---

## Use the REST API (copy-paste)

Plain HTTPS through the environment proxy — no tunnel, no cloudflared.

```bash
# Liveness + token check -> {"message":"API running."}
curl -fsS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" "$HOMEASSISTANT_BASE_URL/api/"

# Read an entity state
curl -fsS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_BASE_URL/api/states/sun.sun"

# Call a service (e.g. turn on a light)
curl -fsS -X POST -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  -H "Content-Type: application/json" -d '{"entity_id":"light.example"}' \
  "$HOMEASSISTANT_BASE_URL/api/services/light/turn_on"
```

---

## Troubleshooting (already-solved gotchas — don't rediscover)

- **`error in libcrypto` / `Permission denied (publickey)`** → the decoded key is
  missing its trailing newline. Re-run step 2 above (it appends one).
- **HTTP `502 Bad Gateway` from `ssh.nmteaco.com`** → Cloudflare Access auth is fine
  but the tunnel can't reach the SSH origin. Confirm with a side-by-side: if
  `ha.nmteaco.com` returns 200 while `ssh.nmteaco.com` 502s, the problem is on the
  **HA side** — the Terminal & SSH add-on is stopped, or the tunnel's SSH ingress
  target is wrong (it should be `ssh://core-ssh:22`, not `ssh://localhost:22`).
- **`cloudflared` won't install** → do **not** use github.com (out of scope here).
  Use `https://pkg.cloudflare.com/cloudflared` (see step 1).
- **`cloudflared` TLS errors** → the environment's MITM HTTPS proxy breaks its edge
  connection. Strip `HTTPS_PROXY`/`HTTP_PROXY` for cloudflared (the wrapper does this);
  direct outbound to `:443` is allowed.
- **`websocket: bad handshake` from cloudflared** → the WebSocket upgrade got a
  non-101 (usually the 502 above). Same root cause: origin/ingress on the HA side.
- **Supervisor API returns `401`** → expected; long-lived tokens can't hit
  `/api/hassio/*`. Use SSH + the `ha` CLI for Supervisor/add-on/host actions.
- **`HA_SSH_USER` is empty** → default to `root` (the Terminal & SSH add-on user).

---

## Environment setup script

Tooling (`openssh-client` + `cloudflared`) is meant to be installed at container
provision time so it's ready from the start of every session. The script is
secret-free and idempotent; it lives in the **environment's Setup script** setting
(Claude Code on the web → environment settings), not in the repo:

```bash
#!/bin/bash
set -euo pipefail
if ! command -v ssh >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y --no-install-recommends openssh-client
fi
if ! command -v cloudflared >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  tmp="$(mktemp -d)"; base="https://pkg.cloudflare.com/cloudflared"
  fn="$(curl -fsSL "$base/dists/any/main/binary-amd64/Packages" | awk '/^Filename:/{print $2; exit}')"
  curl -fsSL -o "$tmp/cloudflared.deb" "$base/$fn"; apt-get install -y "$tmp/cloudflared.deb"; rm -rf "$tmp"
fi
```
