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

> **Cloudflare Access tokens are per-machine and SUFFIXED (`_HA` / `_AI_BOX`).**
> The old *unsuffixed* `CF_ACCESS_CLIENT_ID` / `CF_ACCESS_CLIENT_SECRET` are
> empty — passing them to cloudflared yields an Access **403**, which cloudflared
> reports as the misleading `websocket: bad handshake` (it looks like the SSH
> origin is down, but it isn't). Always use the suffixed names below.

| Variable | Sensitive | Meaning / value |
| --- | --- | --- |
| `HOMEASSISTANT_BASE_URL` | no | `https://ha.nmteaco.com` — HA web UI + REST API base |
| `HOMEASSISTANT_TOKEN` | **yes** | HA long-lived access token (REST API auth) |
| `HA_SSH_HOST` | no | `ssh.nmteaco.com` — HA host's Cloudflare Access SSH hostname |
| `HA_SSH_USER` | no | HA SSH login user. **May be empty — default to `root`** |
| `HA_SSH_KEY_B64` | **yes** | base64 of the ed25519 private key — authorizes **both** hosts |
| `CF_ACCESS_CLIENT_ID_HA` | **yes** | HA host's Cloudflare Access service-token ID |
| `CF_ACCESS_CLIENT_SECRET_HA` | **yes** | HA host's Cloudflare Access service-token secret |
| `AI_BOX_SSH_HOST` | no | `ssh-ai.nmteaco.com` — AI box's Cloudflare Access SSH hostname |
| `CF_ACCESS_CLIENT_ID_AI_BOX` | **yes** | AI box's Cloudflare Access service-token ID |
| `CF_ACCESS_CLIENT_SECRET_AI_BOX` | **yes** | AI box's Cloudflare Access service-token secret |

The AI box's SSH **user is `nmteaco`** (no env var — it's non-secret, baked into the
hook) and it uses the **same** `HA_SSH_KEY_B64` key.

---

## Architecture

There are **two separate machines** on the home network, each behind Cloudflare:

**1. Home Assistant** — a **Home Assistant Green** running **HAOS** (`aarch64`).
- Exposed through a **Cloudflare Tunnel + Cloudflare Access (Zero Trust)** — **no
  open inbound ports** at home.
- Two hostnames ride the **same tunnel**:
  - `https://ha.nmteaco.com` → HA web UI / REST API (origin: HA core, port 8123)
  - `ssh.nmteaco.com` → SSH, **Access-protected** (tunnel ingress: `ssh://core-ssh:22`,
    the *Terminal & SSH* add-on)

**2. The AI box** — hostname **`nmteacoaiserver`**, LAN **`192.168.22.6`**, an
**Ubuntu 24.04** x86_64 machine with a 6 GB NVIDIA GPU. This is **"Chloe."** It runs
the local-LLM / vision / voice workloads (see the *AI box (Chloe)* section below).
- `ssh-ai.nmteaco.com` → SSH, Access-protected, **its own** service token
  (`*_AI_BOX`), login user **`nmteaco`**.
- `https://aibox.nmteaco.com` → Chloe's web/chat API (app-level encrypted auth, not
  the Access service token).
- HA reaches the AI box over the **LAN** (`192.168.22.6:8190`) via `rest_command`s;
  it also Wake-on-LANs it (`script.wake_ai_box`, MAC `a8:5e:45:e6:62:1f`) and the AI
  box is the **NUT/UPS host**, so HA powers it down on low battery.

- **Neither `ssh.nmteaco.com` nor `ssh-ai.nmteaco.com` is raw SSH on port 22.** Each
  is SSH wrapped in an HTTPS WebSocket behind Cloudflare Access. You **must** tunnel
  through `cloudflared` with the *matching* service token — a plain `ssh -p 22` will
  not work, and the *wrong* (or empty/unsuffixed) token gives `bad handshake`.

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

# 3) ProxyCommand wrapper: cloudflared + the RIGHT service token, MITM proxy
#    stripped (cloudflared needs a direct TLS path to Cloudflare's edge on :443).
#    The wrapper picks the token by target hostname: AI box vs HA host.
#    NOTE the SUFFIXED var names — the unsuffixed ones are empty (=> Access 403
#    => misleading "websocket: bad handshake").
cat > ~/.ssh/ha_cf_proxy.sh <<'EOF'
#!/bin/bash
host="$1"
case "$host" in
  ssh-ai.nmteaco.com) id="$CF_ACCESS_CLIENT_ID_AI_BOX"; sec="$CF_ACCESS_CLIENT_SECRET_AI_BOX";;
  *)                  id="$CF_ACCESS_CLIENT_ID_HA";     sec="$CF_ACCESS_CLIENT_SECRET_HA";;
esac
exec env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy \
  cloudflared access ssh --hostname "$host" \
    --service-token-id "$id" --service-token-secret "$sec"
EOF
chmod 700 ~/.ssh/ha_cf_proxy.sh

# 4) Connect. HA host (user root); the AI box uses user `nmteaco` + the SAME key.
ssh -i ~/.ssh/ha_ssh_key \
  -o "ProxyCommand=$HOME/.ssh/ha_cf_proxy.sh %h" \
  -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes \
  "${HA_SSH_USER:-root}@${HA_SSH_HOST}" 'echo "connected: $(whoami)@$(hostname)"; ha core info'

# 4b) The AI box (Chloe):
ssh -i ~/.ssh/ha_ssh_key \
  -o "ProxyCommand=$HOME/.ssh/ha_cf_proxy.sh %h" \
  -o StrictHostKeyChecking=accept-new -o IdentitiesOnly=yes \
  "nmteaco@${AI_BOX_SSH_HOST}" 'echo "connected: $(whoami)@$(hostname)"'
```

With the committed hook, all of the above is automatic — just:

```bash
ssh homeassistant     # HAOS host  (root@core-ssh)
ssh aibox             # AI box     (nmteaco@nmteacoaiserver)  — alias: also `ssh chloe`
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
- **`websocket: bad handshake` from cloudflared** → the WS upgrade got a non-101.
  **Diagnose before assuming the origin is down** — curl the host with the token and
  read the HTTP status:
  ```bash
  env -u HTTPS_PROXY -u HTTP_PROXY curl -sS -o /dev/null -w '%{http_code}\n' \
    -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID_HA" \
    -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET_HA" https://ssh.nmteaco.com/
  ```
  - **403** → Cloudflare Access **rejected the token**. Almost always because the
    wrapper used the empty *unsuffixed* `CF_ACCESS_CLIENT_ID/SECRET` instead of the
    `_HA` / `_AI_BOX` names, or the AI box vs HA token got crossed. **The SSH origin
    is fine — do NOT restart the add-on.** (This exact 403-as-bad-handshake cost a
    whole debugging session once.)
  - **502** → token is fine but the tunnel can't reach the SSH origin (the case
    above: add-on stopped / wrong ingress).
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
  trailing-newline fix), rewrites the token-aware ProxyCommand wrapper, and rewrites
  **both** `ssh homeassistant` and `ssh aibox` aliases — all idempotent and
  secret-free.
- **Environment variables:** the hook reads every secret (`HA_SSH_KEY_B64`, the
  suffixed `CF_ACCESS_CLIENT_*_HA` / `*_AI_BOX`, `HOMEASSISTANT_TOKEN`, …) from the
  secret store at runtime.

Net effect: a brand-new container is fully connection-ready with no manual steps.
The hook activates for all sessions once merged to the default branch.

> Optional speed-up: the same tool-install commands can also go in the
> **environment's Setup script** setting (Claude Code on the web → environment
> settings). That installs `cloudflared` at provision time and caches it, so the
> hook's install step becomes a fast no-op. Not required — the hook installs it
> either way.

---

## The AI box ("Chloe") — local LLM / vision / voice

A second machine on the LAN, **`nmteacoaiserver`** (`192.168.22.6`, Ubuntu 24.04,
x86_64, **6 GB NVIDIA GPU**). Reach it with **`ssh aibox`** (user `nmteaco`, same key,
its own `_AI_BOX` Access token). This box is **"Chloe."** It does all the local AI
work HA can't; HA only pokes it over the LAN and Wake-on-LAN.

> ⚠️ **GPU driver mismatch (as of 2026-07):** `nvidia-smi` fails with
> `Driver/library version mismatch` (NVML 595.84). GPU workloads (SDXL image gen)
> are likely down until a driver reload/reboot. **Chat is CPU-only by design, so it
> keeps working.** Re-check with `nvidia-smi` before assuming image gen is available.

### Listening services

| Port | Bind | Process | What |
| --- | --- | --- | --- |
| `8190` | `0.0.0.0` | `~/transcribe/trigger_service.py` | **Captain's Log trigger** (`captains-transcribe.service`). HA calls `/run` & `/transcribe`; `/health` is open. Auth: `X-Trigger-Token: !secret aibox_trigger_token`. |
| `8189` | `127.0.0.1` | `~/imagegen/app.py` | **Chloe** (chat + image gen). Public via `https://aibox.nmteaco.com`. |
| `20241` | `127.0.0.1` | (internal helper) | loopback-only |
| `3493` | — | NUT | UPS daemon — the AI box is the **NUT/UPS host** |

### Chloe (`~/imagegen/app.py`, one ~1.4k-line app)

- **Chat LLM:** `llama-cpp-python` (`from llama_cpp import Llama`), **CPU-only**
  (`n_gpu_layers=0`) *on purpose* — the CUDA build reserves ~1 GB VRAM even idle,
  and the 6 GB card is reserved for the image model. Default chat model
  **`Gemma-4-12B-OBLITERATED.Q4_K_M.gguf`** (abliterated / uncensored). Any `.gguf`
  in `~/imagegen/models/` is selectable at Initialize (also present:
  `Rocinante-12B`, `NemoMix-Unleashed-12B`).
- **Image gen:** SDXL (`juggernautXL_ragnarok.safetensors`) on the GPU.
- **Persona:** `DEFAULT_SYSTEM_PROMPT` = *"You are Chloe, the user's work assistant…"*.
  `system_prompt` is user-editable and persisted (encrypted at rest).
- **Conversation memory:** per-conversation `history`, `MAX_HISTORY = 40`, in-process
  (encrypted persistence for history + gallery). Generation entry point:
  `_run_llm(history)` (non-streaming).
- **Web/chat API auth** (`aibox.nmteaco.com`): app-level encrypted
  challenge/response — PBKDF2 → HMAC login → **AES-GCM** payloads on
  `/api/challenge`, `/api/login`, `/api/init-status`, `/api/initialize`, `/api/chat`.
  The pre-shared key is a **secret** (secret store — never commit it). `chat_qc.py`
  is a QC harness that exercises `/api/chat` (prints `USER:` / `CHLOE:` + a
  loop/garbage check).

### Captain's Log pipeline (`~/transcribe/`, separate from Chloe's chat model)

`captains_pipeline.py`: Whisper **large-v3** (faster-whisper) transcription →
de-identify + summarize with **Qwen2.5-7B** (CPU) → commit the daily markdown to a
private repo's `captains-log` branch → delete the raw transcript. It coordinates GPU
use with Chloe (asks her image tool to free VRAM). Driven nightly at **7 pm MT**
(`automation.captains_log_nightly_transcription_7pm_mt`) and by HA buttons
(`script.captains_create_log` / `..._transcript` acting on
`input_datetime.captains_target_date`). Surfaced in HA as `sensor.captains_log`
(day count + rendered markdown) and `sensor.captains_status`.

### "Two assistants that converse with each other and me?" — yes, this is where

Chloe already has the scaffolding: a configurable **`system_prompt`** (persona =
"tracks itself"), per-conversation **`history`** ("tracks the conversation"), a
**GGUF model picker**, and a clean **`_run_llm(history)`** primitive, all on a
**local llama.cpp** backend (no cloud, no per-token cost).

**Implemented — "group chat" is a setting in Chloe** (`imagegen/app.py` + `static/app.js`;
a mirror lives in this repo at `ai-box/chloe/`). Settings → *group chat (personas)*:
toggle it on, set your name, and add personas (name + personality). Each turn every
persona replies **round-robin, streaming, one at a time to completion** (so the next
persona sees the previous reply and can respond to it); address one by name and they
answer first. A **"skip my turn"** button (chat composer, group mode only) runs one
round with no input from you, so the personas talk to each other — push again for
another round (`{"advance": true}` to `/api/chat`). Separation from ONE model: each persona is a distinct system prompt over
the shared speaker-labeled transcript, with name-tag stop sequences — no second model
needed. It's persisted in the encrypted `_prompts` and defaults **off** (Chloe behaves
normally until enabled).
- **CPU chat mode** (`cpu_images`, CPU chat + SDXL on GPU): 12B models are slow and a
  group round doubles calls per turn — pick **Qwen2.5-7B** for snappy multi-turn.
- **GPU chat mode** (`gpu_chat`, chat on GPU, **image gen off** — they can't share the
  6 GB card): the worker runs under `transcribe-env` (CUDA llama + torch's bundled
  CUDA-12 runtime, put on `LD_LIBRARY_PATH` by app.py). It offloads as many layers as
  fit: a ~7B **fully** fits (~0.6 s/reply); bigger models partial-offload (12B ≈ 3 s).
  `_spawn_chat_worker` tries a **descending layer chain across separate processes** and
  the worker does a **1-token warm-up before signalling ready**, so a too-tight offload
  falls back cleanly instead of OOM-crashing mid-reply (the old fixed 28-layer / 4096-ctx
  default OOM'd on the 6 GB card). Tunables: `GPU_CHAT_LAYERS` (default 999 = all),
  `GPU_CHAT_CTX` (default 2048).

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

### Repo conventions

- Scripts live in `scripts/`, are standard-library-only Python 3, and read
  credentials from the env vars above — no secrets in code.
- Keep this file current as you learn about the instance (new cameras, renamed
  entities, retention limits) so the next session starts fast.
