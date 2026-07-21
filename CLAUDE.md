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

> **In this repo it's already automated.** The committed SessionStart hook
> (`.claude/hooks/session-start.sh`) runs the steps below on every fresh session,
> so you can just `ssh homeassistant` with no setup. The steps here are the
> manual fallback and the explanation of what the hook does.

Requires `ssh` + `cloudflared` (the hook installs both; the guards below
self-install if missing).

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

## How this persists (the container is wiped every session)

The container filesystem does **not** survive between sessions — installed tools,
the decoded key, and `~/.ssh/config` are all gone next time. Only three stores are
durable: **startup scripts, environment variables, and GitHub.** This setup uses
all three so nothing depends on ephemeral state:

- **GitHub + startup script:** `.claude/hooks/session-start.sh` (registered in
  `.claude/settings.json`) is committed to the repo and runs on every SessionStart.
  It reinstalls `openssh-client` + `cloudflared`, re-materializes the key (with the
  trailing-newline fix), rewrites the ProxyCommand wrapper, and rewrites the
  `ssh homeassistant` alias — all idempotent and secret-free.
- **Environment variables:** the hook reads every secret (`HA_SSH_KEY_B64`,
  `CF_ACCESS_CLIENT_*`, `HOMEASSISTANT_TOKEN`, …) from the secret store at runtime.

Net effect: a brand-new container is fully connection-ready with no manual steps.
The hook activates for all sessions once merged to the default branch.

> Optional speed-up: the same tool-install commands can also go in the
> **environment's Setup script** setting (Claude Code on the web → environment
> settings). That installs `cloudflared` at provision time and caches it, so the
> hook's install step becomes a fast no-op. Not required — the hook installs it
> either way.

---

## Cameras, sensors & domain knowledge (New Mexico Tea Company instance)

Beyond the generic connection info above, these instance-specific facts save time:

- **HA version:** 2026.7.x · **Timezone:** `America/Denver` (Mountain). The REST
  API returns timestamps in **UTC** — convert to Mountain for anything shown to a
  human.
- **Cameras are UniFi Protect.** AI detections surface as
  `binary_sensor.<camera>_<type>_detected` (person, vehicle, animal, …) and are
  toggled by matching `switch.<camera>_<type>_detection` entities.

### "When was a human last seen on the cameras?"

`scripts/last_person_seen.py` answers in one API call:

```bash
./scripts/last_person_seen.py            # who's on camera now / when last seen
./scripts/last_person_seen.py --list     # every person-detection camera + state
./scripts/last_person_seen.py --detail   # recent detection windows (movement path)
./scripts/last_person_seen.py --detail 12 # look back 12 hours
```

A human was seen == a `binary_sensor.*_person_detected` sensor was `on`. If one is
`on` now, someone is on camera live; otherwise its `last_changed` is when the most
recent detection cleared (≈ last seen).

### Person-detection cameras

⚠️ **Entity IDs do NOT match friendly names.** Map via `friendly_name`, not the
entity prefix — this mismatch is the #1 time-sink.

| Location (friendly name) | Person-detected entity |
|---|---|
| Emporium Floor | `binary_sensor.tea_two_person_detected` |
| Emporium Hall | `binary_sensor.emporium_hall_person_detected` |
| Tea One | `binary_sensor.g6_dome_person_detected` |
| Tea Two Camera | `binary_sensor.tea_two_neo_person_detected` |
| Packing Station | `binary_sensor.packing_station_person_detected` |
| Store Room | `binary_sensor.store_room_person_detected` |
| Back Yard | `binary_sensor.g6_180_person_detected` |
| Tea One (secondary, often offline) | `binary_sensor.tea_one_person_detected` |

Motion-only cameras (no person AI): **Kitchen**, **Curbside / Backdoor**,
**12th Street Emporium**. Each camera also exposes `_motion`, `_vehicle_detected`,
`_animal_detected`, plus audio detections.

### Useful raw API calls (cameras)

```bash
# All person sensors, newest change first:
curl -sS -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  "$HOMEASSISTANT_BASE_URL/api/states" \
  | jq -r '.[] | select(.entity_id|endswith("_person_detected"))
      | "\(.last_changed)\t\(.state)\t\(.attributes.friendly_name)"' | sort -r

# History for one entity since a UTC timestamp:
curl -sS -G -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" \
  --data-urlencode "filter_entity_id=binary_sensor.tea_two_person_detected" \
  "$HOMEASSISTANT_BASE_URL/api/history/period/2026-07-18T00:00:00+00:00"
```

The history endpoint's `minimal_response` shrinks payloads but **omits `entity_id`
on repeated rows** — don't use it when you need to know which camera each row
belongs to.

### Store open/close automations (lights & music)

- `script.store_open` → `all_lights_on` + `music_on`; `script.store_close` →
  `all_lights_off` + `music_off`. `all_lights_on/off` template-target **every**
  `light.*` plus every `switch.*` whose name contains "light" plus
  `switch.smart_strip` — running `store_close` blacks out the whole store.
- Triggers: WallMote Quad button 4 = store open, button 3 = store close
  (`zwave_js_value_notification`, Central Scene). A morning automation also runs
  `store_open` on the first person seen 9–10am on Tea Two Neo.
- `input_select.store_state` (Open/Closed/Armed) is an **orphaned helper** —
  nothing acts on it since 2026-07-19.

### ⚠️ Incident 2026-07-19: live automation experiment blacked out the store

A session working through this token created `automation.store_state_apply`
(bare `state:` trigger on `input_select.store_state`, no conditions, action =
`script.store_close`) at 9:48am **while the store was staffed**. Setting the
select killed every light twice (9:48:57, 9:53:20 — the second firing happened
during edit/delete because a bare state trigger also fires on attribute/metadata
updates); employees had to mash WallMote button 4 to recover. Rules learned:

- **Service calls via this token are attributed to the "New Mexico Tea Company"
  user** — indistinguishable from the store dashboard. Your actions ARE
  production actions.
- **Never create/enable/test automations that touch lights or music during
  business hours** (roughly 9am–7pm MT, any day people are on camera). Create
  new automations with `initial_state: false` or test after hours.
- Automations acting on state helpers must use explicit `to:` triggers plus
  guard conditions — never a bare `state:` trigger wired to `store_close`.

### Repo conventions

- Scripts live in `scripts/`, are standard-library-only Python 3, and read
  credentials from the env vars above — no secrets in code.
- Keep this file current as you learn about the instance (new cameras, renamed
  entities, retention limits) so the next session starts fast.
