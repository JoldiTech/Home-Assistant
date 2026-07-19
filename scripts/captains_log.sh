#!/usr/bin/env bash
# captains_log.sh — box-side mechanics for the daily "captain's log" pipeline.
#
# Run these over SSH on the Home Assistant box (they touch /share). The daily
# summarizer (Opus, fired by a Routine at 7pm MT) drives the order:
#
#   1) rotate           -> atomically move the day's transcript aside and print it
#   2) (Opus summarizes, writes captains_log/<date>.md, commits + pushes to git)
#   3) discard <date>   -> delete the staged raw transcript  (ONLY after push)
#
# Rotating (not just reading) means the add-on immediately starts a fresh live
# log, so speech during summarization isn't lost, and the day's data is frozen.
set -euo pipefail

LOG="${TRANSCRIPT_LOG:-/share/tea_one_transcript.log}"
STAGE="${STAGE_DIR:-/share/captains_log_staging}"

cmd="${1:-}"
case "$cmd" in
  rotate)
    mkdir -p "$STAGE"
    d="$(date +%F)"                       # box-local date (America/Denver)
    # Fold any already-staged content for today back in, then take the live log.
    tmp="$STAGE/$d.log"
    if [ -s "$LOG" ]; then
      cat "$LOG" >> "$tmp"
      : > "$LOG"                           # truncate in place; add-on keeps appending
    fi
    [ -f "$tmp" ] && cat "$tmp" || true    # emit the day's transcript to stdout
    ;;
  discard)
    d="${2:?usage: captains_log.sh discard <YYYY-MM-DD>}"
    rm -f "$STAGE/$d.log"
    echo "discarded $STAGE/$d.log"
    ;;
  *)
    echo "usage: $0 {rotate|discard <YYYY-MM-DD>}" >&2
    exit 2
    ;;
esac
