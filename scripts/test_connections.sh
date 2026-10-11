#!/bin/bash
# =============================================================================
# Connection smoke test behind the /test skill. Read-only: it changes nothing
# on either box and writes no secrets anywhere.
#
# Output is one line per check:  PASS|WARN|FAIL|SKIP  <check>  <detail>
# Exit status is the number of FAILs (0 = all good).
#
# The HA API check makes exactly ONE authenticated request and never retries:
# ip_ban is on (login_attempts_threshold: 5), and every rejected token counts
# toward banning this sandbox's egress IP.
# =============================================================================
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$REPO/.claude/hooks/session-start.sh"
NOPROXY=(env -u HTTPS_PROXY -u HTTP_PROXY -u ALL_PROXY -u https_proxy -u http_proxy -u all_proxy)

PASS=0 WARN=0 FAIL=0
res() {
  printf '%-4s  %-26s %s\n' "$1" "$2" "${3:-}"
  case "$1" in PASS) PASS=$((PASS+1));; WARN) WARN=$((WARN+1));; FAIL) FAIL=$((FAIL+1));; esac
}
section() { printf '\n== %s\n' "$1"; }

HA_BASE="${HOMEASSISTANT_BASE_URL:-https://ha.nmteaco.com}"
HA_HOST="${HA_SSH_HOST:-}"
AI_HOST="${AI_BOX_SSH_HOST:-}"

# --- environment -------------------------------------------------------------
section "Environment"
missing=()
for v in HOMEASSISTANT_TOKEN HA_SSH_HOST HA_SSH_KEY_B64 CF_ACCESS_CLIENT_ID_HA CF_ACCESS_CLIENT_SECRET_HA \
         AI_BOX_SSH_HOST CF_ACCESS_CLIENT_ID_AI_BOX CF_ACCESS_CLIENT_SECRET_AI_BOX; do
  [ -n "${!v:-}" ] || missing+=("$v")
done
if [ ${#missing[@]} -eq 0 ]; then res PASS "secrets present" "all 8 set"
else res FAIL "secrets present" "missing: ${missing[*]}"; fi
if [ -n "${CF_ACCESS_CLIENT_ID:-}${CF_ACCESS_CLIENT_SECRET:-}" ]; then
  res WARN "legacy var names" "CF_ACCESS_CLIENT_ID/SECRET are set; nothing reads them — use the _HA names"
fi

# --- local tooling -----------------------------------------------------------
section "Local tooling"
alias_host() { ssh -G "$1" 2>/dev/null | awk '/^hostname /{print $2}'; }
tooling_ok() {
  command -v ssh >/dev/null && command -v cloudflared >/dev/null && [ -s "$HOME/.ssh/ha_ssh_key" ] \
    && [ "$(alias_host homeassistant)" = "$HA_HOST" ] && [ "$(alias_host aibox)" = "$AI_HOST" ]
}
if ! tooling_ok && [ -x "$HOOK" ]; then
  CLAUDE_CODE_REMOTE=true "$HOOK" >/dev/null 2>&1 || true
  res WARN "setup hook" "SSH setup was incomplete; re-ran session-start.sh"
fi
command -v ssh >/dev/null && res PASS "ssh" "$(ssh -V 2>&1 | cut -d, -f1)" || res FAIL "ssh" "not installed"
command -v cloudflared >/dev/null && res PASS "cloudflared" "$(cloudflared --version 2>/dev/null | awk '{print $3}')" \
  || res FAIL "cloudflared" "not installed"
[ -s "$HOME/.ssh/ha_ssh_key" ] && res PASS "ssh key" "~/.ssh/ha_ssh_key" || res FAIL "ssh key" "not materialized (HA_SSH_KEY_B64?)"
for a in homeassistant aibox; do
  want=$([ $a = homeassistant ] && echo "$HA_HOST" || echo "$AI_HOST")
  got=$(alias_host $a)
  if [ -n "$want" ] && [ "$got" = "$want" ]; then res PASS "alias '$a'" "$got"
  else res FAIL "alias '$a'" "points at '${got:-none}', expected '${want:-unset}'"; fi
done

# Connects with NO service token. Access must refuse before SSH is reached;
# reaching the SSH server at all means the hostname is outside any Access app.
access_guard() {
  local host=$1 out
  out=$(timeout 40 ssh -o BatchMode=yes -o PreferredAuthentications=none \
        -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30 \
        -o "ProxyCommand=${NOPROXY[*]} cloudflared access ssh --hostname %h --service-token-id '' --service-token-secret ''" \
        "probe@$host" true 2>&1)
  if grep -qiE 'bad handshake|403|forbidden' <<<"$out"; then
    res PASS "Access enforced" "$host refuses connections without the service token"
  elif grep -qiE 'no such host' <<<"$out"; then
    res FAIL "Access enforced" "$host has no DNS record (tunnel route missing?)"
  elif grep -qiE 'permission denied|authentication|disconnect' <<<"$out"; then
    res FAIL "Access enforced" "$host reaches SSH WITHOUT a token — add it to a Cloudflare Access application"
  else
    res WARN "Access enforced" "inconclusive: $(tail -1 <<<"$out")"
  fi
}

ssh_fail_reason() {
  local out=$1
  if grep -qi 'bad handshake' <<<"$out"; then echo "Access rejected the service token (stale/rotated secret, or tunnel down)"
  elif grep -qi 'no such host' <<<"$out"; then echo "hostname has no DNS record"
  elif grep -qi 'permission denied' <<<"$out"; then echo "box rejected the SSH key/user"
  elif [ -z "$out" ]; then echo "timed out"
  else tail -1 <<<"$out"; fi
}

# --- Home Assistant ------------------------------------------------------------
section "Home Assistant"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$HA_BASE/manifest.json")
case "$code" in
  200) res PASS "web ($HA_BASE)" "HTTP 200" ;;
  530) res FAIL "web ($HA_BASE)" "530 — tunnel has no connector (Cloudflared add-on down?)" ;;
  403) res FAIL "web ($HA_BASE)" "403 — this IP may be in ip_bans.yaml" ;;
  *)   res FAIL "web ($HA_BASE)" "HTTP $code" ;;
esac

if [ -z "${HOMEASSISTANT_TOKEN:-}" ]; then
  res SKIP "REST API" "HOMEASSISTANT_TOKEN not set"
elif [ "$code" != 200 ]; then
  res SKIP "REST API" "web is down; not spending a login attempt"
else
  body=$(curl -s --max-time 20 -w '\n%{http_code}' -H "Authorization: Bearer $HOMEASSISTANT_TOKEN" "$HA_BASE/api/config")
  acode=$(tail -1 <<<"$body")
  if [ "$acode" = 200 ]; then
    res PASS "REST API" "$(head -n -1 <<<"$body" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("HA", d["version"] + ",", d["state"])' 2>/dev/null)"
  else
    res FAIL "REST API" "HTTP $acode — token rejected; NOT retried (ip_ban counts each failure)"
  fi
fi

if [ -n "$HA_HOST" ]; then
  access_guard "$HA_HOST"
  out=$(timeout 60 ssh -o BatchMode=yes homeassistant '
    printf "core|%s\n" "$(ha core info --raw-json 2>/dev/null | jq -r "\"\(.data.version) (update: \(.data.update_available))\"")"
    ha apps --raw-json 2>/dev/null | jq -r ".data.addons[] | select(.name|test(\"cloudflared|ssh\";\"i\")) | \"app|\(.name)|\(.state)\""
    printf "disk|%s\n" "$(ha host info --raw-json 2>/dev/null | jq -r ".data.disk_free")"
  ' 2>&1)
  if grep -q '^core|' <<<"$out"; then
    res PASS "SSH login" "root@core-ssh"
    res PASS "HA core" "$(sed -n 's/^core|//p' <<<"$out")"
    while IFS='|' read -r _ name state; do
      [ "$state" = started ] && res PASS "app: $name" "$state" || res FAIL "app: $name" "$state"
    done < <(grep '^app|' <<<"$out")
    disk=$(sed -n 's/^disk|//p' <<<"$out")
    awk -v d="$disk" 'BEGIN{exit !(d+0 < 5)}' && res WARN "HA disk free" "${disk} GB" || res PASS "HA disk free" "${disk} GB"
  else
    res FAIL "SSH login" "$(ssh_fail_reason "$out")"
  fi
else
  res SKIP "SSH" "HA_SSH_HOST not set"
fi

# --- AI box --------------------------------------------------------------------
section "AI box"
if [ -n "$AI_HOST" ]; then
  access_guard "$AI_HOST"
  out=$(timeout 120 ssh -o BatchMode=yes aibox 'bash -s' 2>&1 <<'REMOTE'
r() { printf '%s|%s|%s\n' "$1" "$2" "$3"; }
r PASS "SSH login" "$(whoami)@$(hostname), $(uptime -p)"

g=$(nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader 2>/dev/null)
[ -n "$g" ] && r PASS "GPU" "$g" || r FAIL "GPU" "nvidia-smi failed"

for s in captains-transcribe imagegen cloudflared wan-failover nut-monitor; do
  st=$(systemctl is-active "$s.service" 2>/dev/null)
  [ "$st" = active ] && r PASS "service: $s" "active" || r FAIL "service: $s" "${st:-unknown}"
done

h=$(curl -s --max-time 5 http://127.0.0.1:8190/health)
[ -n "$h" ] && r PASS "Captain's Log trigger" "$h" || r FAIL "Captain's Log trigger" ":8190/health no answer"
c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8189/)
[ "$c" = 200 ] && r PASS "Chloe (local)" "HTTP 200" || r FAIL "Chloe (local)" "HTTP $c"

c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://192.168.22.254:8123/manifest.json)
[ "$c" = 200 ] && r PASS "LAN -> HA Green" "192.168.22.254:8123 HTTP 200" || r FAIL "LAN -> HA Green" "HTTP $c"

dev=$(ip route get 1.1.1.1 2>/dev/null | grep -oP 'dev \K\S+')
case "$dev" in
  enx*) r WARN "AI box egress" "failed over to the AT&T dongle ($dev)" ;;
  "")   r FAIL "AI box egress" "no route to the internet" ;;
  *)    r PASS "AI box egress" "primary LAN ($dev)" ;;
esac

ups=$(upsc cyberpower ups.status 2>/dev/null); bat=$(upsc cyberpower battery.charge 2>/dev/null)
case "$ups" in
  OL*) r PASS "UPS" "on line power, battery ${bat}%" ;;
  OB*) r WARN "UPS" "ON BATTERY at ${bat}% — store power is out" ;;
  *)   r FAIL "UPS" "upsc: ${ups:-no answer}" ;;
esac

if [ -r /etc/nmteaco/protect.env ]; then
  set -a; . /etc/nmteaco/protect.env; set +a
  v=$(curl -sk --max-time 10 -H "X-API-KEY: $PROTECT_API_KEY" "https://$PROTECT_HOST/proxy/protect/integration/v1/meta/info" \
      | python3 -c 'import sys,json; print(json.load(sys.stdin)["applicationVersion"])' 2>/dev/null)
  [ -n "$v" ] && r PASS "UniFi Protect API" "v$v" || r FAIL "UniFi Protect API" "no answer from $PROTECT_HOST"
  curl -sk --max-time 10 -H "X-API-KEY: $PROTECT_API_KEY" "https://$PROTECT_HOST/proxy/network/api/s/default/stat/health" \
  | python3 -c '
import sys, json
try:
    subs = {s["subsystem"]: s for s in json.load(sys.stdin)["data"]}
except Exception:
    print("FAIL|store WANs|UniFi Network API gave no answer"); sys.exit()
wan = subs.get("wan", {})
isp, ip = wan.get("isp_name", "?"), wan.get("wan_ip", "?")
www = subs.get("www", {}).get("status", "?")
print("PASS|active WAN|%s (%s), www %s" % (isp, ip, www))
for key, label in (("WAN", "WAN1 AT&T"), ("WAN2", "WAN2 T-Mobile")):
    st = wan.get("uptime_stats", {}).get(key)
    if not st:
        print("FAIL|%s|no monitor data (link down?)" % label)
        continue
    a = st.get("availability", 0)
    print("%s|%s|%.1f%% available, %s ms" % ("PASS" if a >= 99 else "WARN", label, a, st.get("latency_average", "?")))
'
else
  r SKIP "UniFi" "/etc/nmteaco/protect.env not readable"
fi

if [ -r /etc/nmteaco/captains.env ]; then
  tok=$(sed -n 's/^DATALOG_API_TOKEN=//p' /etc/nmteaco/captains.env | tr -d "\"'")
  base=$(sed -n 's/^DASHBOARD_BASE_URL=//p' /etc/nmteaco/captains.env | tr -d "\"'")
  base=${base:-https://dashboard.nmteaco.com}
  if [ -z "$tok" ]; then r FAIL "datalog API" "DATALOG_API_TOKEN empty in captains.env"
  else
    resp=$(curl -s --max-time 30 -w '\n%{http_code}' -H "Authorization: Bearer $tok" "${base%/}/tools/datalog/sales.php")
    code=$(tail -1 <<<"$resp")
    if [ "$code" = 200 ] && head -n -1 <<<"$resp" | grep -q '"ok": *true'; then r PASS "datalog API" "token accepted"
    else r FAIL "datalog API" "HTTP $code — business data will be missing from the Captain's Log"; fi
  fi
fi

latest=$(git -C ~/ha-captains-repo ls-tree --name-only HEAD captains_log/ 2>/dev/null \
         | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}\.md$' | sort | tail -1 | cut -c1-10)
today=$(TZ=America/Denver date +%F); yday=$(TZ=America/Denver date -d yesterday +%F)
hm=$(TZ=America/Denver date +%H%M)
if [ "$latest" = "$today" ] || { [ "$latest" = "$yday" ] && [ "$hm" -lt 2030 ]; }; then
  r PASS "Captain's Log" "latest day-file $latest"
else
  r WARN "Captain's Log" "latest day-file ${latest:-none} — a nightly run was missed (audio expires ~30 days)"
fi
REMOTE
  )
  if grep -q '^PASS|SSH login' <<<"$out"; then
    while IFS='|' read -r s n d; do res "$s" "$n" "$d"; done < <(grep -E '^(PASS|WARN|FAIL|SKIP)\|' <<<"$out")
  else
    res FAIL "SSH login" "$(ssh_fail_reason "$out")"
  fi
else
  res SKIP "SSH" "AI_BOX_SSH_HOST not set"
fi

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 https://aibox.nmteaco.com/)
[ "$code" = 200 ] && res PASS "Chloe (public)" "aibox.nmteaco.com HTTP 200" || res FAIL "Chloe (public)" "aibox.nmteaco.com HTTP $code"

printf '\n%d passed, %d warnings, %d failed\n' "$PASS" "$WARN" "$FAIL"
exit "$FAIL"
