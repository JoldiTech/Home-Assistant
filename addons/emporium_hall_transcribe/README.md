# Emporium Hall Live Transcribe (Home Assistant local add-on)

A **second** capture instance running alongside **Tea One Live Transcribe**.
Emporium Hall is adjacent to Tea One, so the two cameras overhear the same
conversations — which is what lets Opus test **multi-camera fusion** (dedupe +
cross-reconstruct across the two transcripts by timestamp/content).

It shares the exact same `run.py`, `Dockerfile`, and `/share/whisper_models`
models as `tea_one_transcribe`; only the options differ:

| Option | Value |
| --- | --- |
| `camera_entity` | `camera.emporium_hall_high_resolution_channel` |
| `gate_entity` | `binary_sensor.emporium_hall_speaking_detected` |
| `log_path` | `/share/emporium_hall_transcript.log` |
| `ha_sensor` | `sensor.emporium_hall_transcript` |
| `threads` | `2` (so both cameras can transcribe a shared conversation without oversubscribing the Green's 4 cores) |

Each instance uses a per-camera temp dir (`/media/transcribe_tmp/<camera>`), so
the two never collide.

See `../tea_one_transcribe/README.md` for the full design, efficiency notes, and
dashboard card. This is a temporary two-instance setup for the overlap test; the
long-term multi-camera design is a single service on the GPU box.
