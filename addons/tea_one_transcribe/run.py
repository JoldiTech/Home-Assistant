#!/usr/bin/env python3
"""Tea One Live Transcribe.

Speech-gated, on-device transcription loop:
  * poll the camera's speaking-detection sensor (the "gate")
  * while the gate is on, pull a short clip via HA's camera.record
  * trim silence, run whisper.cpp, append transcript lines to a searchable log

Standard library only. Config comes from /data/options.json (add-on options);
the Supervisor token comes from $SUPERVISOR_TOKEN.
"""
import datetime
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

OPTS = json.load(open("/data/options.json"))
TOKEN = os.environ["SUPERVISOR_TOKEN"]
API = "http://supervisor/core/api"

CAM = OPTS["camera_entity"]
GATE = OPTS["gate_entity"]
MODEL = "/opt/models/ggml-%s.bin" % OPTS.get("model", "tiny.en")
LANG = OPTS.get("language", "en")
SEG = int(OPTS.get("segment_seconds", 20))
LOOKBACK = int(OPTS.get("lookback_seconds", 5))
THREADS = str(OPTS.get("threads", 3))
SIL_DB = int(OPTS.get("silence_threshold_db", -30))
LOG = OPTS.get("log_path", "/share/tea_one_transcript.log")

POLL = 1.5  # seconds between gate checks while idle
WORKDIR = "/media/tea_one_transcribe_tmp"  # camera.record target (allowlisted)
os.makedirs(WORKDIR, exist_ok=True)

_STOP = False


def _sig(_signum, _frame):
    global _STOP
    _STOP = True


signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT, _sig)


def log(*a):
    print(*a, flush=True)


def api(method, path, data=None, timeout=30):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        API + path,
        data=body,
        method=method,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def gate_on():
    try:
        st = api("GET", "/states/" + GATE)
        return bool(st) and st.get("state") == "on"
    except urllib.error.URLError as e:
        log("gate poll error:", e)
        return False


def record(path):
    # Blocks for ~SEG seconds while HA records; lookback grabs the pre-roll.
    api(
        "POST",
        "/services/camera/record",
        {"entity_id": CAM, "filename": path, "duration": SEG, "lookback": LOOKBACK},
        timeout=SEG + 45,
    )


def transcribe(mp4):
    """mp4 -> list of transcript lines (may be empty)."""
    base = mp4[:-4]
    wav = base + ".wav"
    # Extract 16k mono and drop silence (cuts Whisper hallucination + runtime).
    sr = (
        "silenceremove=start_periods=1:stop_periods=-1:"
        "start_threshold=%ddB:stop_threshold=%ddB:"
        "start_silence=0.3:stop_silence=0.5" % (SIL_DB, SIL_DB)
    )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", mp4,
         "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-af", sr, wav],
        check=True,
    )
    # Nothing (or almost nothing) survived the silence trim -> no speech.
    if not os.path.exists(wav) or os.path.getsize(wav) < 16000:  # < ~0.5s pcm16
        return []

    subprocess.run(
        ["whisper-cli", "-m", MODEL, "-f", wav, "-l", LANG, "-t", THREADS,
         "-nt", "-otxt", "-of", base, "--no-speech-thold", "0.6"],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    lines = []
    txt = base + ".txt"
    if os.path.exists(txt):
        for ln in open(txt):
            ln = ln.strip()
            if ln and not _is_noise(ln):
                lines.append(ln)
    return lines


# Common whisper.cpp hallucinations on near-silence / noise.
_NOISE = {
    "[blank_audio]", "(blank_audio)", "[silence]", "[music]", "(music)",
    "you", ".", "thank you.", "thanks for watching!",
}


def _is_noise(line):
    return line.lower().strip() in _NOISE


def append_log(lines):
    if not lines:
        return
    ts = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        for ln in lines:
            f.write("%s | %s\n" % (ts, ln))
    log("+%d line(s):" % len(lines), " / ".join(lines)[:200])


def cleanup(mp4):
    base = mp4[:-4]
    for ext in (".mp4", ".wav", ".txt"):
        try:
            os.remove(base + ext)
        except OSError:
            pass


def main():
    if not os.path.exists(MODEL):
        log("FATAL: model missing:", MODEL)
        sys.exit(1)
    log("tea_one_transcribe up: cam=%s gate=%s model=%s seg=%ss lookback=%ss threads=%s -> %s"
        % (CAM, GATE, MODEL, SEG, LOOKBACK, THREADS, LOG))

    was_on = False
    while not _STOP:
        if gate_on():
            if not was_on:
                log("gate OPEN -> capturing")
            was_on = True
            mp4 = os.path.join(WORKDIR, "seg_%d.mp4" % int(time.time()))
            try:
                record(mp4)
                if os.path.exists(mp4):
                    append_log(transcribe(mp4))
            except urllib.error.URLError as e:
                log("record/api error:", e)
                time.sleep(2)
            except subprocess.CalledProcessError as e:
                log("processing error:", e)
            finally:
                cleanup(mp4)
        else:
            if was_on:
                log("gate CLOSED -> idle")
            was_on = False
            time.sleep(POLL)

    log("stopping (signal received)")


if __name__ == "__main__":
    main()
