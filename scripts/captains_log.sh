#!/usr/bin/env bash
# captains_log.sh — box-side mechanics for the daily "captain's log" pipeline.
#
# GENERIC over all cameras: any add-on that writes /share/<camera>_transcript.log
# is picked up automatically — no per-camera hard-coding. Add a camera, and the
# next nightly run just includes it.
#
# The nightly summarizer (Opus, fired by a Routine at 7pm MT) drives the order:
#   1) rotate           -> freeze every camera's transcript and print them all,
#                          each under a "===== CAMERA: <name> =====" header
#   2) (Opus reads all of them and writes ONE combined captains_log/<date>.md,
#       de-duping/reconstructing across cameras as it summarizes, then commits)
#   3) discard <date>   -> delete all of that day's staged transcripts (ONLY after push)
#
# Rotating (truncate-in-place) means each add-on immediately keeps appending to a
# fresh log, so speech during summarization isn't lost, and the day's data is frozen.
set -euo pipefail

SHARE="${SHARE_DIR:-/share}"
STAGE="${STAGE_DIR:-/share/captains_log_staging}"

cmd="${1:-}"
case "$cmd" in
  rotate)
    d="$(date +%F)"                     # box-local date (America/Denver)
    dst="$STAGE/$d"
    mkdir -p "$dst"
    for L in "$SHARE"/*_transcript.log; do
      [ -s "$L" ] || continue           # skips the literal glob when nothing matches
      cam="$(basename "$L" _transcript.log)"
      cat "$L" >> "$dst/$cam.log"
      : > "$L"                          # truncate in place; add-on keeps appending
    done
    echo "LOGDATE=$d"
    for f in "$dst"/*.log; do
      [ -e "$f" ] || continue
      echo "===== CAMERA: $(basename "$f" .log) ====="
      cat "$f"
    done
    ;;
  discard)
    d="${2:?usage: captains_log.sh discard <YYYY-MM-DD>}"
    rm -rf "${STAGE:?}/$d"
    echo "discarded $STAGE/$d"
    ;;
  *)
    echo "usage: $0 {rotate|discard <YYYY-MM-DD>}" >&2
    exit 2
    ;;
esac
