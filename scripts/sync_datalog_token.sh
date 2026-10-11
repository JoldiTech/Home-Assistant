#!/bin/bash
# =============================================================================
# Push DATALOG_API_TOKEN from this environment's secret store to the AI box's
# /etc/nmteaco/captains.env — only when the box's current token is failing and
# the environment's token is verified to work. Used by the /test skill.
#
# The token travels over ssh stdin only: never on a command line, in output,
# or in the repo. The pipeline re-reads captains.env on every run, so no
# service restart is needed.
#
# Exit: 0 = box already fine or updated, 1 = nothing done / failed, 2 = env token rejected.
# =============================================================================
set -uo pipefail

if [ -z "${DATALOG_API_TOKEN:-}" ]; then
  echo "SKIP  DATALOG_API_TOKEN is not set in this environment (add it in environment settings, then start a new session)"
  exit 1
fi
case "$DATALOG_API_TOKEN" in *[[:space:]\"\'\\]*) echo "FAIL  DATALOG_API_TOKEN contains whitespace or quotes"; exit 1;; esac

REMOTE=$(cat <<'REMOTE_SCRIPT'
f=/etc/nmteaco/captains.env
read -r tok
[ -n "$tok" ] || { echo "FAIL  no token received"; exit 1; }
base=$(sed -n 's/^DASHBOARD_BASE_URL=//p' "$f" 2>/dev/null | tr -d "\"'")
base=${base:-https://dashboard.nmteaco.com}
check() {  # token on stdin -> header via stdin, never in argv
  local resp
  resp=$(printf 'Authorization: Bearer %s' "$1" | curl -s --max-time 30 -w '\n%{http_code}' -H @- "${base%/}/tools/datalog/sales.php")
  [ "$(tail -1 <<<"$resp")" = 200 ] && head -n -1 <<<"$resp" | grep -q '"ok": *true'
}
cur=$(sed -n 's/^DATALOG_API_TOKEN=//p' "$f" 2>/dev/null | tr -d "\"'")
if [ -n "$cur" ] && check "$cur"; then echo "PASS  box token already valid; nothing changed"; exit 0; fi
check "$tok" || { echo "FAIL  environment token rejected by the datalog API; box left unchanged"; exit 2; }
umask 077; tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT; printf '%s' "$tok" > "$tmp"
run=(); [ -w "$f" ] || run=(sudo -n)
"${run[@]}" python3 -c '
import sys
f, t = sys.argv[1], open(sys.argv[2]).read()
lines = open(f).read().splitlines()
out, done = [], False
for l in lines:
    if l.strip().startswith("DATALOG_API_TOKEN="):
        if not done: out.append("DATALOG_API_TOKEN=" + t)
        done = True
    else:
        out.append(l)
if not done: out.append("DATALOG_API_TOKEN=" + t)
open(f, "w").write("\n".join(out) + "\n")   # in place: keeps owner and mode
' "$f" "$tmp" || { echo "FAIL  could not write $f (needs write access or passwordless sudo for nmteaco)"; exit 1; }
cur=$(sed -n 's/^DATALOG_API_TOKEN=//p' "$f" | tr -d "\"'")
check "$cur" && echo "PASS  captains.env updated; datalog API accepts the token" || { echo "FAIL  wrote the file but the API still rejects it"; exit 1; }
REMOTE_SCRIPT
)
enc=$(printf '%s' "$REMOTE" | base64 -w0)
# The script goes in as an argument, not on stdin: stdin carries the token, and
# a script piped into bash would have its own `read` consume the script's next
# line instead of the token.
printf '%s\n' "$DATALOG_API_TOKEN" | timeout 120 ssh -o BatchMode=yes aibox "bash -c \"\$(echo $enc | base64 -d)\""
