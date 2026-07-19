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
import glob
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

OPTS = json.load(open("/data/options.json"))
TOKEN = os.environ["SUPERVISOR_TOKEN"]
API = "http://supervisor/core/api"

CAM = OPTS["camera_entity"]
GATE = OPTS["gate_entity"]
MODEL_NAME = OPTS.get("model", "tiny.en")
MODEL_DIR = OPTS.get("model_dir", "/share/whisper_models")
MODEL = os.path.join(MODEL_DIR, "ggml-%s.bin" % MODEL_NAME)
LANG = OPTS.get("language", "en")
SEG = int(OPTS.get("segment_seconds", 20))
LOOKBACK = int(OPTS.get("lookback_seconds", 5))
THREADS = str(OPTS.get("threads", 3))
SIL_DB = int(OPTS.get("silence_threshold_db", -30))
LOG = OPTS.get("log_path", "/share/tea_one_transcript.log")
HA_SENSOR = (OPTS.get("ha_sensor") or "").strip()  # "" disables dashboard sensor
RECENT = int(OPTS.get("recent_lines", 30))
TZNAME = OPTS.get("timezone", "America/Denver")


def _tz():
    if ZoneInfo is not None:
        try:
            return ZoneInfo(TZNAME)
        except Exception:  # noqa: BLE001 - bad name / missing tzdata -> local
            pass
    return datetime.datetime.now().astimezone().tzinfo


ZONE = _tz()


def _now():
    return datetime.datetime.now(ZONE)

POLL = 1.5  # seconds between gate checks while idle
# Per-camera working dir so multiple instances (one add-on per camera) don't
# collide on the camera.record target. /media is allowlisted for the recorder.
WORKDIR = os.path.join("/media/transcribe_tmp", CAM.replace(".", "_"))
os.makedirs(WORKDIR, exist_ok=True)

_STOP = False
_last_size = -1  # last log size the sensor was pushed for (detects external truncation)


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
        # -mc 0: no cross-segment context -> fewer runaway repetition hallucinations.
        ["whisper-cli", "-m", MODEL, "-f", wav, "-l", LANG, "-t", THREADS,
         "-mc", "0", "-nt", "-otxt", "-of", base, "--no-speech-thold", "0.6"],
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
    stamp = _now().strftime("%Y-%m-%d %H:%M:%S %Z")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        for ln in lines:
            f.write("%s | %s\n" % (stamp, ln))
    log("+%d line(s):" % len(lines), " / ".join(lines)[:200])
    push_sensor()


_SENSOR_NAME = (HA_SENSOR.split(".", 1)[-1].replace("_", " ").title()
                if HA_SENSOR else "")


def _log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def _read_tail(n):
    """Last n parsed rows from the log as (full_stamp, hh_mm_ss, text)."""
    try:
        with open(LOG) as f:
            raw = f.readlines()
    except OSError:
        return []
    rows = []
    for line in raw:
        line = line.strip()
        if " | " not in line:
            continue
        stamp, _, text = line.partition(" | ")
        parts = stamp.split(" ")
        hm = parts[1] if len(parts) > 1 else stamp  # HH:MM:SS
        rows.append((stamp, hm, text))
    return rows[-n:]


def push_sensor():
    """Publish the CURRENT log tail to the HA sensor. The feed is a live view of
    the file, so when the nightly job truncates the log the dashboard clears
    itself — no restart needed."""
    global _last_size
    if not HA_SENSOR:
        return
    rows = _read_tail(RECENT)
    if rows:
        last_stamp, _hm, last_text = rows[-1]
        state = last_text[:250]
        last_spoken = last_stamp
        lines = ["%s | %s" % (hm, text) for (_s, hm, text) in rows]
    else:
        state = "idle"
        last_spoken = None
        lines = []
    try:
        api("POST", "/states/" + HA_SENSOR, {
            "state": state,
            "attributes": {
                "friendly_name": _SENSOR_NAME,
                "icon": "mdi:message-text",
                "last_spoken": last_spoken,
                "lines": lines,  # newest last
            },
        })
        _last_size = _log_size()
    except urllib.error.URLError as e:
        log("sensor push error:", e)


def cleanup(mp4):
    # glob catches .mp4, .wav, .txt and camera.record's leftover .mp4.tmp
    for f in glob.glob(mp4[:-4] + "*"):
        try:
            os.remove(f)
        except OSError:
            pass


def ensure_model():
    """Model lives on the persistent /share mount; fetch it if it's not there."""
    if os.path.exists(MODEL):
        return True
    os.makedirs(MODEL_DIR, exist_ok=True)
    url = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-%s.bin"
           % MODEL_NAME)
    log("model not on disk, downloading:", url)
    tmp = MODEL + ".part"
    try:
        with urllib.request.urlopen(url, timeout=600) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        os.replace(tmp, MODEL)
        log("model downloaded:", MODEL, "(%d bytes)" % os.path.getsize(MODEL))
        return True
    except Exception as e:  # noqa: BLE001 - report and fail cleanly
        log("model download failed:", e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def main():
    if not ensure_model():
        have = os.listdir(MODEL_DIR) if os.path.isdir(MODEL_DIR) else "(no dir)"
        log("FATAL: model unavailable:", MODEL, "- dir contains:", have)
        sys.exit(1)
    log("transcribe up: cam=%s gate=%s model=%s seg=%ss lookback=%ss threads=%s -> %s"
        % (CAM, GATE, MODEL, SEG, LOOKBACK, THREADS, LOG))
    if HA_SENSOR:
        log("dashboard sensor:", HA_SENSOR, "(name: %s)" % _SENSOR_NAME)
    push_sensor()
    # clear stale temp clips left by a previous interrupted run
    for f in glob.glob(os.path.join(WORKDIR, "seg_*")):
        try:
            os.remove(f)
        except OSError:
            pass

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
            # If the log changed out from under us (e.g. the nightly captain's-log
            # job truncated it), refresh so the dashboard mirrors the file.
            if HA_SENSOR and _log_size() != _last_size:
                push_sensor()
            time.sleep(POLL)

    log("stopping (signal received)")


if __name__ == "__main__":
    main()
