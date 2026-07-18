# CLAUDE.md

Guidance for working in this repository.

## Connecting to Home Assistant over SSH

Home Assistant is reachable over SSH, but **not** through a direct port-22
connection. `ssh.nmteaco.com` sits behind **Cloudflare Tunnel + Cloudflare
Access**, so `ssh root@ssh.nmteaco.com` on port 22 just times out. SSH must be
tunneled over HTTPS with `cloudflared`, authenticated with a Cloudflare Access
service token.

### Connection facts (all supplied via session env vars)

| What | Env var | Notes |
|------|---------|-------|
| SSH host | `HA_SSH_HOST` | `ssh.nmteaco.com` (Cloudflare-proxied) |
| SSH user | `HA_SSH_USER` | `root` |
| SSH private key | `HA_SSH_KEY_B64` | base64-encoded ED25519 key — decode to a file, `chmod 600` |
| Access service token | `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` | cloudflared reads them as `TUNNEL_SERVICE_TOKEN_ID` / `TUNNEL_SERVICE_TOKEN_SECRET` |
| HA web UI | `HOMEASSISTANT_BASE_URL` | `https://ha.nmteaco.com`, reachable over HTTPS; long-lived API token in `HOMEASSISTANT_TOKEN` |

- Remote is **Home Assistant OS (HAOS)**; the `ha` CLI is available
  (`ha core info`, `ha supervisor ...`). The landing container is `core-ssh`
  (the SSH add-on).
- Local (this) container is **x86_64 / amd64**.

### How to connect

If the session **setup script** installed the `ha-ssh` wrapper (see below):

```bash
ha-ssh                 # interactive shell
ha-ssh 'ha core info'  # run a command
```

Otherwise, the raw sequence:

```bash
mkdir -p ~/.ssh
printf '%s' "$HA_SSH_KEY_B64" | base64 -d > ~/.ssh/ha_key && chmod 600 ~/.ssh/ha_key
export TUNNEL_SERVICE_TOKEN_ID="$CF_ACCESS_CLIENT_ID"
export TUNNEL_SERVICE_TOKEN_SECRET="$CF_ACCESS_CLIENT_SECRET"
ssh -i ~/.ssh/ha_key \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ProxyCommand="cloudflared access ssh --hostname %h" \
  "$HA_SSH_USER@$HA_SSH_HOST" 'ha core info'
```

### Gotchas

- **Port 22 direct connect times out** — the host is Cloudflare-proxied, not a
  raw SSH endpoint. You must use the `cloudflared access ssh` ProxyCommand.
- **`websocket: bad handshake`** means the Cloudflare Access service token was
  not presented (or is wrong). Export `TUNNEL_SERVICE_TOKEN_ID` /
  `TUNNEL_SERVICE_TOKEN_SECRET` from `CF_ACCESS_CLIENT_ID` /
  `CF_ACCESS_CLIENT_SECRET` before connecting.
- Outbound egress here is **HTTPS-only through the agent proxy**; cloudflared
  tunnels SSH over 443, which is why it works when raw port 22 does not.
- Feed the service token to cloudflared via **env vars, not CLI flags**, so the
  secret never appears in a process list or a persisted config file.

## Session setup script

The environment's setup script (configured in the Claude Code web UI, not in
this repo) should install the client tooling and materialize the SSH helper.
It must stay **secret-free** (reads secrets from env vars, hardcodes none),
**idempotent**, and must **not** make any live network connection at provision
time — a setup script provisions the environment; testing connectivity is a
runtime action. Two parts:

1. **Tooling** — install `openssh-client` and `cloudflared` (cloudflared from
   Cloudflare's apt repo, since GitHub releases are out of scope here).
2. **Convenience** — decode `HA_SSH_KEY_B64` to `~/.ssh/ha_key` (chmod 600) and
   install a `/usr/local/bin/ha-ssh` wrapper that exports the token env vars and
   execs `ssh` with the cloudflared ProxyCommand.
