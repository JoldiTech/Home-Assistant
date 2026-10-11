#!/bin/sh
# ippeveprinter's print command: "prints" one sheet by bumping the shared page
# counter. $SHARED/mode: "hang" never finishes, "slow" takes 8 s, "fail" fails.
mode=$(cat "$SHARED/mode" 2>/dev/null)
echo "$(date +%s) $(basename "$1")" >> "$SHARED/printed.log"
case "$mode" in
  hang) sleep 100000 ;;
  slow) sleep 8 ;;
  fail) exit 1 ;;
esac
sleep 2
n=$(cat "$SHARED/count" 2>/dev/null || echo 0)
echo $((n + 1)) > "$SHARED/count"
