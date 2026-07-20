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
| `HA_SSH_KEY_B64` | **yes** | base64 of the ed25519 private key (also authorized on the AI box, see below) |
| `CF_ACCESS_CLIENT_ID_HA` | **yes** | Cloudflare Access service-token ID for the **HA box's** tunnel |
| `CF_ACCESS_CLIENT_SECRET_HA` | **yes** | Cloudflare Access service-token secret for the **HA box's** tunnel |
| `AI_BOX_SSH_HOST` | no | `ssh-ai.nmteaco.com` — SSH hostname for the **AI box** (see "AI box" below) |
| `AI_BOX_SSH_USER` | no | SSH login user on the AI box. Defaults to `nmteaco` if unset |
| `CF_ACCESS_CLIENT_ID_AI_BOX` | **yes** | Cloudflare Access service-token ID for the **AI box's** tunnel (separate from HA's) |
| `CF_ACCESS_CLIENT_SECRET_AI_BOX` | **yes** | Cloudflare Access service-token secret for the **AI box's** tunnel |

> **Naming history:** the Cloudflare Access vars used to be plain `CF_ACCESS_CLIENT_ID`/
> `CF_ACCESS_CLIENT_SECRET` (HA was the only box). They were renamed to the `_HA`
> suffix when the AI box got its own independent tunnel + service token, so each
> box's credentials are distinct and independently revocable. If something that
> reaches `ssh.nmteaco.com` breaks with `Permission denied (publickey,password)`
> after an env-var change, check for this exact renaming first.

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

### AI box (`nmteacoaiserver`)

A separate machine — Ubuntu 24.04, NVIDIA RTX 2060 (driver `nvidia-driver-595-open`,
CUDA 13.2 ceiling) — for GPU workloads (local transcription models, etc.). It has
its **own independent Cloudflare Tunnel and Access application**, entirely separate
from the HA box's tunnel (own service token, own hostname `ssh-ai.nmteaco.com`) —
so it's reachable even if the HA box or its tunnel is ever down. It reuses the
**same SSH private key** as the HA box (`HA_SSH_KEY_B64`'s public half is
authorized on both machines) — only the Access layer differs per box.

Connect with `ssh aibox` (set up by the same SessionStart hook / Setup script as
`ssh homeassistant`).

**Python ML stack** lives in a venv at `~/transcribe-env` on the box (activate with
`source ~/transcribe-env/bin/activate`): `torch` (CUDA build, confirmed working on
the RTX 2060), `faster-whisper`, `demucs`, `nemo_toolkit[asr]` (Parakeet). This is
the exact Demucs-vocal-isolation → Whisper/Parakeet pipeline validated on the HA
Green earlier — same tools, now with real GPU acceleration.

**Talks to UniFi Protect directly** (no HA in the loop) via Protect's local
Integrations API. Credentials are **not** in this repo or in the Claude session
environment — they live only on the box itself:

- `/etc/nmteaco/protect.env` (mode `600`, owned by `nmteaco`) — `PROTECT_API_KEY`
  and `PROTECT_HOST` (`192.168.22.1` — the UniFi console's LAN IP; originally
  discovered via HA's `unifiprotect` config entry, which reports a
  `*.id.ui.direct` hostname — we resolved and pinned the LAN IP instead so the
  pipeline doesn't depend on that DNS mechanism at boot).
- Verified: `curl -k -H "X-API-KEY: $PROTECT_API_KEY" https://$PROTECT_HOST/proxy/protect/integration/v1/meta/info`
  → `200 {"applicationVersion":"7.1.87"}`.
- Any future systemd service on this box should read secrets via
  `EnvironmentFile=/etc/nmteaco/protect.env`, never hardcode them.

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
    --service-token-id "$CF_ACCESS_CLIENT_ID_HA" \
    --service-token-secret "$CF_ACCESS_CLIENT_SECRET_HA"
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

**AI box:** identical pattern — same key (`~/.ssh/ha_ssh_key`), but its own proxy
wrapper reading `CF_ACCESS_CLIENT_ID_AI_BOX`/`CF_ACCESS_CLIENT_SECRET_AI_BOX`,
hostname `${AI_BOX_SSH_HOST}`, user `${AI_BOX_SSH_USER:-nmteaco}`. The
SessionStart hook sets both up automatically — connect with `ssh aibox`.

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

### Local audio transcription (Tea One)

A local add-on transcribes the **Tea One** camera mic on-device (whisper.cpp
`tiny.en`, aarch64/NEON) into a searchable log + a dashboard feed. Source of
truth is `addons/tea_one_transcribe/` in this repo; it's deployed to
`/addons/tea_one_transcribe/` on the box and managed by Supervisor as
`local_tea_one_transcribe`.

- **Gate (efficiency):** only runs while `binary_sensor.g6_dome_speaking_detected`
  (Tea One's on-camera speaking AI) is `on` — enabled via
  `switch.g6_dome_speaking_detection`. Idle cost ~12 MB RAM, 0 % CPU; whisper
  only fires (a ~15 s spike, RTF ~0.97) during real speech. `base.en` is too slow
  (RTF ~1.3) — stay on `tiny.en`.
- **Audio source:** HA's own `camera.record` (already 16 kHz mono AAC) via the
  Supervisor proxy — no UniFi creds / RTSP needed.
- **Models:** live on the persistent `/share/whisper_models/` (NOT baked into the
  image), auto-downloaded from HF on first run. Keeps rebuilds fast.
- **Outputs:** searchable log `/share/tea_one_transcript.log`; entity
  `sensor.tea_one_transcript` (state = last line, `lines` attr = rolling feed);
  a **"Tea One Transcript"** view on the **DowntownControls** dashboard
  (`dashboard-downtowncontrols`).
- **Manage:** `ha apps {info,logs,restart,rebuild,stats} local_tea_one_transcribe`
  over SSH. Options are set via the Supervisor API
  (`POST http://supervisor/addons/local_tea_one_transcribe/options` with
  `$SUPERVISOR_TOKEN`; send the FULL options object — partial payloads are
  rejected for missing required keys). There is no `ha apps options` subcommand.
- **CLI note:** `ha addons` is deprecated in favor of `ha apps` on this box.

### Repo conventions

- Scripts live in `scripts/`, are standard-library-only Python 3, and read
  credentials from the env vars above — no secrets in code.
- Keep this file current as you learn about the instance (new cameras, renamed
  entities, retention limits) so the next session starts fast.
