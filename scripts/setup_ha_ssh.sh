#!/usr/bin/env bash
# Provision the (ephemeral-disk) artifacts needed to SSH into Home Assistant
# through a Cloudflare Access tunnel, using secrets kept in *sticky* env vars.
#
# The environment's disk is ephemeral but its environment variables persist, so
# this script reconstitutes on every session: the cloudflared binary, the SSH
# private key file, and an ssh_config entry. It is idempotent, non-interactive,
# and exits 0 even when unconfigured so it never blocks a SessionStart hook.
#
# Required sticky env vars (set once in the environment settings):
#   HA_SSH_HOST              Cloudflare Access hostname, e.g. ssh.nmteaco.com
#   HA_SSH_KEY_B64           base64 of the PEM SSH private key  (`base64 -w0 key`)
#   CF_ACCESS_CLIENT_ID      Cloudflare Access service-token Client ID
#   CF_ACCESS_CLIENT_SECRET  Cloudflare Access service-token Client Secret
# Optional:
#   HA_SSH_USER              SSH user (default: root)
#
# After a successful run:  ssh ha            # e.g.  ssh ha 'ha core info'
set -euo pipefail

log() { printf 'ha-ssh: %s\n' "$*" >&2; }

HA_SSH_USER="${HA_SSH_USER:-root}"
BIN_DIR="$HOME/.local/bin"
SSH_DIR="$HOME/.ssh"
KEY_FILE="$SSH_DIR/ha_claude_ed25519"
CLOUDFLARED="$BIN_DIR/cloudflared"
WRAPPER="$BIN_DIR/cloudflared-ha"
INCLUDE_FILE="$SSH_DIR/ha_tunnel.conf"

# --- 0) soft config check ---------------------------------------------------
missing=()
for v in HA_SSH_HOST HA_SSH_KEY_B64 CF_ACCESS_CLIENT_ID CF_ACCESS_CLIENT_SECRET; do
  [ -n "${!v:-}" ] || missing+=("$v")
done
if [ "${#missing[@]}" -ne 0 ]; then
  log "not configured yet — missing: ${missing[*]}"
  log "set these as persistent environment variables to enable SSH-over-Cloudflare."
  exit 0
fi

mkdir -p "$BIN_DIR" "$SSH_DIR"
chmod 700 "$SSH_DIR"

# --- 1) cloudflared binary (ephemeral disk -> fetch if absent) --------------
if ! "$CLOUDFLARED" --version >/dev/null 2>&1; then
  case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) arch=amd64 ;;
  esac
  log "downloading cloudflared ($arch)…"
  if ! curl -fsSL --retry 3 \
      "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}" \
      -o "$CLOUDFLARED"; then
    log "ERROR: cloudflared download failed; SSH is unavailable this session."
    exit 0
  fi
  chmod +x "$CLOUDFLARED"
fi
log "cloudflared: $("$CLOUDFLARED" --version 2>/dev/null | head -n1)"

# --- 2) service-token wrapper (keeps secrets out of ssh_config) -------------
# cloudflared reads TUNNEL_SERVICE_TOKEN_ID/SECRET; map our sticky vars at run
# time so the secret values are never written to disk.
cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
export TUNNEL_SERVICE_TOKEN_ID="\${CF_ACCESS_CLIENT_ID:-}"
export TUNNEL_SERVICE_TOKEN_SECRET="\${CF_ACCESS_CLIENT_SECRET:-}"
exec "$CLOUDFLARED" "\$@"
EOF
chmod +x "$WRAPPER"

# --- 3) private key (sticky env -> ephemeral disk) --------------------------
umask 077
if ! printf '%s' "$HA_SSH_KEY_B64" | base64 -d > "$KEY_FILE" 2>/dev/null; then
  log "ERROR: HA_SSH_KEY_B64 is not valid base64; cannot write key."
  exit 0
fi
chmod 600 "$KEY_FILE"

# --- 4) ssh_config Host entry ("ha") ----------------------------------------
cat > "$INCLUDE_FILE" <<EOF
Host ha
    HostName ${HA_SSH_HOST}
    User ${HA_SSH_USER}
    IdentityFile ${KEY_FILE}
    IdentitiesOnly yes
    ProxyCommand ${WRAPPER} access ssh --hostname %h
    StrictHostKeyChecking accept-new
    ConnectTimeout 20
EOF
chmod 600 "$INCLUDE_FILE"

# Ensure the include is active (must precede any Host blocks -> prepend once).
CONFIG="$SSH_DIR/config"
touch "$CONFIG"
chmod 600 "$CONFIG"
if ! grep -qxF "Include ${INCLUDE_FILE}" "$CONFIG"; then
  printf 'Include %s\n%s\n' "$INCLUDE_FILE" "$(cat "$CONFIG")" > "$CONFIG.tmp"
  mv "$CONFIG.tmp" "$CONFIG"
  chmod 600 "$CONFIG"
fi

log "ready. Connect with:  ssh ha            (e.g.  ssh ha 'ha core info')"
