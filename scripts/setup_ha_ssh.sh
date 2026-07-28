#!/usr/bin/env bash
# Provision the (ephemeral-disk) artifacts needed to SSH into Home Assistant and
# the AI box through their Cloudflare Access tunnels, using secrets kept in
# *sticky* env vars.
#
# The environment's disk is ephemeral but its environment variables persist, so
# this script reconstitutes on every session: the cloudflared binary, the SSH
# private key file, and an ssh_config entry. It is idempotent, non-interactive,
# and exits 0 even when unconfigured so it never blocks a SessionStart hook.
#
# Required sticky env vars (set once in the environment settings):
#   HA_SSH_HOST                 Cloudflare Access hostname, e.g. ssh.nmteaco.com
#   HA_SSH_KEY_B64              base64 of the PEM SSH private key  (`base64 -w0 key`)
#   CF_ACCESS_CLIENT_ID_HA      Cloudflare Access service-token Client ID
#   CF_ACCESS_CLIENT_SECRET_HA  Cloudflare Access service-token Client Secret
# Optional:
#   HA_SSH_USER                 SSH user on the HA box (default: root)
#   AI_BOX_SSH_HOST             AI box Access hostname, e.g. ssh-ai.nmteaco.com
#   AI_BOX_SSH_USER             SSH user on the AI box (default: nmteaco)
#   CF_ACCESS_CLIENT_ID_AI_BOX      the AI box's own service-token Client ID
#   CF_ACCESS_CLIENT_SECRET_AI_BOX  the AI box's own service-token Client Secret
#
# After a successful run:  ssh ha            # e.g.  ssh ha 'ha core info'
#                          ssh aibox         # when the AI box vars are set
set -euo pipefail

log() { printf 'ha-ssh: %s\n' "$*" >&2; }

HA_SSH_USER="${HA_SSH_USER:-root}"
BIN_DIR="$HOME/.local/bin"
SSH_DIR="$HOME/.ssh"
KEY_FILE="$SSH_DIR/ha_claude_ed25519"
CLOUDFLARED="$BIN_DIR/cloudflared"
WRAPPER="$BIN_DIR/cloudflared-ha"
AIBOX_WRAPPER="$BIN_DIR/cloudflared-aibox"
INCLUDE_FILE="$SSH_DIR/ha_tunnel.conf"

# --- 0) soft config check ---------------------------------------------------
missing=()
for v in HA_SSH_HOST HA_SSH_KEY_B64 CF_ACCESS_CLIENT_ID_HA CF_ACCESS_CLIENT_SECRET_HA; do
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
# github.com is out of scope in this environment, so the binary comes from
# Cloudflare's apt repo; the .deb is unpacked rather than installed to keep
# this script root-free.
if ! "$CLOUDFLARED" --version >/dev/null 2>&1; then
  case "$(uname -m)" in
    x86_64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *) arch=amd64 ;;
  esac
  log "downloading cloudflared ($arch)…"
  tmp="$(mktemp -d)"
  base="https://pkg.cloudflare.com/cloudflared"
  # The `|| true` tails keep set -e from aborting before the "download failed"
  # diagnostic below. They must not clear the variable: under pipefail the
  # upstream command SIGPIPEs once awk/head has taken what it needs, failing the
  # pipeline even though the value we want was captured.
  fn="$(curl -fsSL --retry 3 "$base/dists/any/main/binary-${arch}/Packages" 2>/dev/null \
        | awk '/^Filename:/{print $2; exit}')" || true
  bin=""
  if [ -n "$fn" ] \
     && curl -fsSL --retry 3 -o "$tmp/cf.deb" "$base/$fn" \
     && dpkg-deb -x "$tmp/cf.deb" "$tmp/x" >/dev/null 2>&1; then
    bin="$(find "$tmp/x" -type f -name cloudflared | head -n1)" || true
  fi
  if [ -n "$bin" ]; then
    install -m 0755 "$bin" "$CLOUDFLARED"
    rm -rf "$tmp"
  else
    rm -rf "$tmp"
    log "ERROR: cloudflared download failed; SSH is unavailable this session."
    exit 0
  fi
fi
log "cloudflared: $("$CLOUDFLARED" --version 2>/dev/null | head -n1)"

# --- 2) service-token wrappers (keep secrets out of ssh_config) -------------
# cloudflared reads TUNNEL_SERVICE_TOKEN_ID/SECRET; map our sticky vars at run
# time so the secret values are never written to disk. The environment's MITM
# HTTPS proxy breaks cloudflared's TLS path to Cloudflare's edge, so the proxy
# variables are stripped here.
write_wrapper() {  # $1=path  $2=id var name  $3=secret var name
  cat > "$1" <<EOF
#!/usr/bin/env bash
exec env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY \\
         -u https_proxy -u http_proxy -u all_proxy \\
    TUNNEL_SERVICE_TOKEN_ID="\${$2:-}" \\
    TUNNEL_SERVICE_TOKEN_SECRET="\${$3:-}" \\
    "$CLOUDFLARED" "\$@"
EOF
  chmod +x "$1"
}
write_wrapper "$WRAPPER" CF_ACCESS_CLIENT_ID_HA CF_ACCESS_CLIENT_SECRET_HA

AIBOX=0
if [ -n "${AI_BOX_SSH_HOST:-}" ] \
   && [ -n "${CF_ACCESS_CLIENT_ID_AI_BOX:-}" ] \
   && [ -n "${CF_ACCESS_CLIENT_SECRET_AI_BOX:-}" ]; then
  AIBOX=1
  write_wrapper "$AIBOX_WRAPPER" CF_ACCESS_CLIENT_ID_AI_BOX CF_ACCESS_CLIENT_SECRET_AI_BOX
fi

# --- 3) private key (sticky env -> ephemeral disk) --------------------------
umask 077
if ! printf '%s' "$HA_SSH_KEY_B64" | base64 -d > "$KEY_FILE" 2>/dev/null; then
  log "ERROR: HA_SSH_KEY_B64 is not valid base64; cannot write key."
  exit 0
fi
# Without a trailing newline OpenSSH rejects the key ("error in libcrypto").
if [ -s "$KEY_FILE" ] && [ "$(tail -c1 "$KEY_FILE" | od -An -tx1 | tr -d ' ')" != "0a" ]; then
  printf '\n' >> "$KEY_FILE"
fi
chmod 600 "$KEY_FILE"

# --- 4) ssh_config Host entries ("ha", "aibox") -----------------------------
{
  cat <<EOF
Host ha
    HostName ${HA_SSH_HOST}
    User ${HA_SSH_USER}
    IdentityFile ${KEY_FILE}
    IdentitiesOnly yes
    ProxyCommand ${WRAPPER} access ssh --hostname %h
    StrictHostKeyChecking accept-new
    ConnectTimeout 50
    ServerAliveInterval 15
EOF
  if [ "$AIBOX" -eq 1 ]; then
    cat <<EOF

Host aibox
    HostName ${AI_BOX_SSH_HOST}
    User ${AI_BOX_SSH_USER:-nmteaco}
    IdentityFile ${KEY_FILE}
    IdentitiesOnly yes
    ProxyCommand ${AIBOX_WRAPPER} access ssh --hostname %h
    StrictHostKeyChecking accept-new
    ConnectTimeout 50
    ServerAliveInterval 15
EOF
  fi
} > "$INCLUDE_FILE"
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
if [ "$AIBOX" -eq 1 ]; then
  log "AI box ready. Connect with:  ssh aibox"
fi
