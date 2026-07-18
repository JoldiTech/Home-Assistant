# Tea One Live Transcribe (Home Assistant local add-on)

On-device speech-to-text for the **Tea One** camera's microphone, written to a
searchable log. Everything runs locally on the Home Assistant Green — no cloud
STT, no audio leaves the box.

## Why it's efficient

Three gates keep the expensive step (Whisper) idle unless there is real speech:

1. **Camera AI gate (free, always-on).** UniFi Protect's own on-device
   *speaking detection* decides "is someone talking?" We watch
   `binary_sensor.g6_dome_speaking_detected` (friendly name *Tea One Speaking
   detected*). No people talking → nothing runs, not even ffmpeg.
2. **Silence trim.** When the gate opens we capture a short clip and drop the
   quiet parts with ffmpeg `silenceremove` before Whisper sees it.
3. **Small quantized model.** `whisper.cpp` `tiny.en` (or `base.en`), compiled
   natively for aarch64/NEON, int-quantized GGML — a few hundred MB RAM, faster
   than real time on the Green's 4×A55.

Audio is pulled through Home Assistant's own stream (`camera.record`), which
already delivers the Tea One mic as **16 kHz mono AAC** — Whisper's native input
format. That means no UniFi credentials, no RTSP tokens, and no separate
go2rtc/RTSP plumbing to maintain.

## Flow

```
binary_sensor.g6_dome_speaking_detected == on
        │  (poll every ~1.5s)
        ▼
camera.record  ──►  /media/<tmp>/seg.mp4   (duration + lookback pre-roll)
        │
        ▼  ffmpeg: extract 16k mono, trim silence
   seg.wav
        │
        ▼  whisper-cli -m ggml-tiny.en ...
   transcript lines
        │
        ▼  append "YYYY-MM-DD HH:MM:SS TZ | <text>"
   /share/tea_one_transcript.log
```

The `lookback` pre-roll captures the couple of seconds *before* the camera
flagged speech, so the start of an utterance isn't clipped.

## Install / deploy

This add-on is a **local add-on**. It lives in this repo as the source of
truth and is deployed to the box under `/addons`:

```bash
# from a machine with `ssh homeassistant` configured (see repo CLAUDE.md)
ssh homeassistant 'mkdir -p /addons/tea_one_transcribe'
scp -r addons/tea_one_transcribe/* homeassistant:/addons/tea_one_transcribe/
# then in HA: Settings → Add-ons → ⟳ (reload) → "Tea One Live Transcribe" → Install → Start
```

Or drive it entirely from the `ha` CLI over SSH (see the repo runbook).

First install compiles whisper.cpp inside the image (a few minutes on the
Green); subsequent starts are instant.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `camera_entity` | `camera.tea_one_low_resolution_channel` | Camera to record. |
| `gate_entity` | `binary_sensor.g6_dome_speaking_detected` | Speech gate; capture only while `on`. |
| `model` | `tiny.en` | `tiny.en` (fastest), `base.en`, or `small.en`. |
| `language` | `en` | Whisper language hint. |
| `segment_seconds` | `20` | Length of each recorded clip while the gate is open. |
| `lookback_seconds` | `5` | Pre-roll captured before the trigger. |
| `threads` | `3` | Whisper CPU threads (leave ≥1 core for HA). |
| `silence_threshold_db` | `-30` | ffmpeg silence-trim threshold. Higher (e.g. `-25`) trims more aggressively in a noisy room. |
| `log_path` | `/share/tea_one_transcript.log` | Searchable output log. |

## Reading the log

```bash
ssh homeassistant 'tail -f /share/tea_one_transcript.log'
ssh homeassistant 'grep -i "refund" /share/tea_one_transcript.log'
```

Each line is `timestamp (America/Denver) | transcribed text`.

## Notes & caveats

- **Quality is camera-mic quality.** A ceiling camera in a room with music/HVAC
  is a hard STT target; `tiny.en` will miss words and occasionally invent them.
  Bump `model` to `base.en` if the box has headroom, and raise
  `silence_threshold_db` toward `-25` in a noisy room.
- **Privacy/consent.** This records audio of customers and staff. Confirm it's
  consistent with posted notice and local policy before running it permanently.
- **`switch.g6_dome_speaking_detection`** (Tea One Speaking detection) must be
  **on** for the gate to fire.
