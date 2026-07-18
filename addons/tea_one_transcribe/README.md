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
| `silence_threshold_db` | `-45` | ffmpeg silence-trim threshold. Only audio below this is treated as dead air. This room's ambient floor is ~−31 dB, so keep it well below that (e.g. `-45`) or real speech gets cut. |
| `log_path` | `/share/tea_one_transcript.log` | Searchable output log. |
| `ha_sensor` | `sensor.tea_one_transcript` | Entity to publish for the dashboard. Empty string disables it. |
| `recent_lines` | `30` | How many recent lines to keep in the log tail buffer / dashboard feed. |

## Reading the log

```bash
ssh homeassistant 'tail -f /share/tea_one_transcript.log'
ssh homeassistant 'grep -i "refund" /share/tea_one_transcript.log'
```

Each line is `timestamp (America/Denver) | transcribed text`. `/share` is also
browsable from the File Editor / VS Code / Samba add-ons.

## See it on the dashboard

The add-on also publishes an HA entity (`ha_sensor`, default
`sensor.tea_one_transcript`): the **state** is the latest line, and the **`lines`**
attribute is the recent rolling feed. No MQTT broker required — it's pushed
straight to Core's state machine.

Add a **Markdown card** (Edit dashboard → Add card → Manual) for a live feed,
newest first:

```yaml
type: markdown
title: Tea One — Live Transcript
content: |
  Last heard: **{{ state_attr('sensor.tea_one_transcript','last_spoken') }}**
  {% for l in (state_attr('sensor.tea_one_transcript','lines') or []) | reverse %}
  - {{ l }}
  {% endfor %}
```

Or a one-line **Entities card** showing just the most recent utterance:

```yaml
type: entities
entities:
  - entity: sensor.tea_one_transcript
    name: Last heard on Tea One
```

Note: API-pushed states don't survive a Core restart, but the add-on reseeds the
sensor from the log tail on startup, so the card repopulates on its own.

## Notes & caveats

- **Quality is camera-mic quality.** A ceiling camera in a room with music/HVAC
  is a hard STT target; `tiny.en` transcribes at the *gist* level — good enough to
  search, not verbatim — and will occasionally invent a phrase.
- **Model choice.** Benchmarked on this box: `tiny.en` runs at ~0.97× real time
  (keeps up), `base.en` at ~1.3× (falls behind continuous speech). Stay on
  `tiny.en` unless you move the workload to a stronger machine.
- **Privacy/consent.** This records audio of customers and staff. Confirm it's
  consistent with posted notice and local policy before running it permanently.
- **`switch.g6_dome_speaking_detection`** (Tea One Speaking detection) must be
  **on** for the gate to fire.
