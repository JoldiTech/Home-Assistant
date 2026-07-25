# Chloe app — group-chat (multi-persona) feature

This directory **mirrors** the Chloe app that runs on the AI box
(`nmteacoaiserver`), reflecting the group-chat feature added to it. The
**canonical, running copy lives on the box** at `~/imagegen/app.py` and
`~/imagegen/static/app.js` (served at `https://aibox.nmteaco.com`); this mirror
is for review/history. No secrets are included — auth material (login verifier,
enc key) lives in separate files on the box, not in source.

- `app.py` — FastAPI backend
- `static/app.js` — frontend

## What the feature adds

A **group chat** mode so multiple named personas share Chloe's single local
llama.cpp model and converse with you *and each other*.

- **Settings → group chat (personas):** a toggle, your name, and a list of
  personas (name + personality). Persisted in the encrypted `_prompts`
  (`group_mode`, `user_name`, `personas`); **defaults off** — Chloe is unchanged
  until enabled.
- **Each turn, every persona replies round-robin, streaming, one at a time to
  completion**, so the next persona sees the previous reply and can respond to
  it. Address a persona by name and they answer first (the lead rotates
  otherwise). A **"skip my turn"** button runs one round with no input from you,
  so the personas talk to each other (push again for another round) — it POSTs
  `{"advance": true}` to `/api/chat`.
- **Separation from one model instance:** a chat LLM is stateless between calls,
  so each persona is just a distinct system prompt over the shared,
  speaker-labeled transcript, with stop sequences on the other speakers' name
  tags (`_persona_turn_messages` / `_strip_other_speakers`). Calls are serialized
  (one llama.cpp context). No second model is needed.

## Key changes (vs. stock Chloe)

Backend (`app.py`): `_normalize_prompts`, `_addressed_personas`,
`_render_labeled_transcript`, `_persona_turn_messages`, `_strip_other_speakers`;
`set-prompts`/login persist the new fields; `_cpu_token_stream`/`_gpu_token_stream`
take an optional `stop`; `/api/chat` branches into a per-persona streaming loop
(`produce_group`) when group mode is on. Frontend (`app.js`): a persona editor in
settings, speaker-labeled bubbles, and a stream loop that opens a fresh bubble per
speaker.

## Deploy / revert

Deployed by copying these over `~/imagegen/{app.py,static/app.js}` and
`sudo systemctl restart imagegen`. Timestamped backups were left on the box as
`app.py.bak-<ts>` / `app.js.bak-<ts>` — revert by copying one back and
restarting.

## Runtime note

Group chat runs in **CPU chat mode** (`cpu_images`). For snappy multi-turn use a
smaller model (Qwen2.5-7B) rather than a 12B.

`chat_worker.py` + `_spawn_chat_worker` are the **GPU chat** path (separate mode,
image gen off). It's here because they were fixed alongside: the worker now warms
up (1-token generation) before signalling ready, and the app tries a descending
GPU-layer chain across separate processes, so a too-tight offload on the 6 GB card
falls back instead of OOM-crashing. A ~7B fully offloads (~0.6 s/reply); bigger
models partial-offload. Tunables: `GPU_CHAT_LAYERS` (default 999=all), `GPU_CHAT_CTX`
(default 2048).
