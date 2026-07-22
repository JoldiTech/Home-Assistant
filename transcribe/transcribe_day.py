#!/usr/bin/env python3
"""Pull one day of Tea One audio straight from UniFi Protect and transcribe it
with faster-whisper large-v3 on the AI box's GPU.

This is the STANDING source for the store's Captain's Log. It replaces the old
local whisper add-ons on the HA box: the nightly Captain's Log job SSHes to the
AI box and runs this, then summarizes + de-identifies the output.

  ssh aibox 'source ~/transcribe-env/bin/activate && \
             python3 ~/transcribe/transcribe_day.py 2026-07-20'

Reads UniFi Protect creds from /etc/nmteaco/protect.env (never hardcoded):
  PROTECT_HOST, PROTECT_LOCAL_USER, PROTECT_LOCAL_PASS

Writes the transcript to  ~/captains_transcripts/tea_one_<date>.log  and also
prints it to stdout (the caller reads either). Exit code is 0 even for an empty
day (store closed / no speech) - the caller decides what to do with zero lines.

Hallucination-loop fix carried over from validation: condition_on_previous_text
is OFF and VAD is stricter, so ambiguous audio can't spiral into repeated
phrases. Timestamps stay mapped to the original (untrimmed) audio.
"""
import argparse
import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from uiprotect import ProtectApiClient
from faster_whisper import WhisperModel

TZ = ZoneInfo("America/Denver")
CAM_ID = "6a59918c01154803e4003451"  # Tea One
CHANNEL = 2  # low-res 640x360 - we only need the audio track
# Business-day envelope. VAD produces nothing during closed/silent stretches, so
# a wide window is safe; the end is also capped at "now" so we never ask Protect
# for footage that doesn't exist yet (the nightly job runs ~7pm).
START_HOUR = int(os.environ.get("TRANSCRIBE_START_HOUR", "8"))
END_HOUR = int(os.environ.get("TRANSCRIBE_END_HOUR", "20"))

MAX_REPEAT = 2  # collapse a line repeated more than this many times in a row
VAD_PARAMS = dict(threshold=0.6, min_silence_duration_ms=500)
# Domain vocabulary injected into every decode window (hotwords work with
# condition_on_previous_text=False; initial_prompt would only reach the first
# window). Whisper mishears tea terms it doesn't know - "pu-erh" became "poire",
# "Monk's" became "Munch's". Kept deliberately small: overstuffed hotwords
# degrade decoding.
HOTWORDS = (
    "pu-erh, rooibos, honeybush, oolong, genmaicha, sencha, gyokuro, matcha, "
    "hojicha, tulsi, yerba mate, guayusa, Nilgiri, Darjeeling, Assam, Keemun, "
    "Ceylon, lapsang souchong, Earl Grey, Monk's Blend, silver needle, "
    "white peony, Tieguanyin, masala chai, hibiscus, chamomile, elderberry, "
    "lemongrass, tisane, Wenshan, tulsi Krishna, Rama, Vana"
)
_NOISE = {"[blank_audio]", "(blank_audio)", "[silence]", "[music]", "(music)", "you", "."}

OUT_DIR = Path.home() / "captains_transcripts"
TMP = Path("/tmp/transcribe_day")


def _load_creds():
    # /etc/nmteaco/protect.env is `KEY=value` lines (mode 600). Parse without a
    # shell so a child process doesn't need it exported.
    creds = {}
    with open("/etc/nmteaco/protect.env") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
    return creds


def _now_denver():
    # Date.now() is fine here (real script on the box, not a workflow sandbox).
    return datetime.now(TZ)


def _windows(day: datetime):
    start = day.replace(hour=START_HOUR, minute=0, second=0, microsecond=0)
    end = day.replace(hour=END_HOUR, minute=0, second=0, microsecond=0)
    end = min(end, _now_denver())  # never pull future footage
    cur = start
    while cur < end:
        nxt = min(cur + timedelta(hours=1), end)
        yield cur, nxt
        cur = nxt


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date", nargs="?", help="YYYY-MM-DD (America/Denver); default today")
    args = ap.parse_args()
    day = (
        datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=TZ)
        if args.date
        else _now_denver()
    )
    date_str = day.strftime("%Y-%m-%d")

    creds = _load_creds()
    OUT_DIR.mkdir(exist_ok=True)
    TMP.mkdir(exist_ok=True)
    out_log = OUT_DIR / f"tea_one_{date_str}.log"
    out_log.write_text("")

    client = ProtectApiClient(
        creds["PROTECT_HOST"], 443, creds["PROTECT_LOCAL_USER"], creds["PROTECT_LOCAL_PASS"],
        verify_ssl=False,
    )
    await client.update()
    print(f"[transcribe_day] {date_str}: Protect login OK", file=sys.stderr, flush=True)
    print("[transcribe_day] loading large-v3 on GPU...", file=sys.stderr, flush=True)
    model = WhisperModel("large-v3", device="cuda", compute_type="float16")

    total = 0
    for i, (start, end) in enumerate(_windows(day)):
        mp4 = TMP / f"chunk_{i}.mp4"
        wav = TMP / f"chunk_{i}.wav"
        t0 = time.time()
        try:
            await client.get_camera_video(CAM_ID, start, end, channel_index=CHANNEL, output_file=mp4)
        except Exception as e:
            print(f"[transcribe_day] chunk {i} export failed: {e}", file=sys.stderr, flush=True)
            continue
        if not mp4.exists() or mp4.stat().st_size == 0:
            continue
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(mp4),
             "-map", "0:a:0", "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
        )
        segs, _ = model.transcribe(
            str(wav), language="en", vad_filter=True, vad_parameters=VAD_PARAMS,
            condition_on_previous_text=False, hotwords=HOTWORDS,
        )
        lines = []
        prev, run = None, 0
        for s in segs:
            text = s.text.strip()
            if not text or text.lower() in _NOISE:
                continue
            if text == prev:
                run += 1
                if run > MAX_REPEAT:
                    continue
            else:
                prev, run = text, 1
            ts = start + timedelta(seconds=s.start)
            lines.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S %Z')} | {text}")
        with open(out_log, "a") as f:
            for ln in lines:
                f.write(ln + "\n")
        total += len(lines)
        print(
            f"[transcribe_day] {start:%H:%M}-{end:%H:%M} "
            f"{time.time()-t0:.0f}s -> {len(lines)} lines (total {total})",
            file=sys.stderr, flush=True,
        )
        mp4.unlink(missing_ok=True)
        wav.unlink(missing_ok=True)

    await client.close_session()
    print(f"[transcribe_day] DONE {date_str}: {total} lines -> {out_log}", file=sys.stderr, flush=True)
    # transcript to stdout for the caller
    sys.stdout.write(out_log.read_text())


if __name__ == "__main__":
    asyncio.run(main())
