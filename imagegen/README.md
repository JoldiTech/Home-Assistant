# Chloe — ephemeral, E2E-encrypted chat + image tool (AI box)

A single password-gated web app on the AI box, public at
`https://aibox.nmteaco.com`, with three modes after login:

- **conversation** — chat with a local LLM. Any `.gguf` in the models dir is
  selectable at Initialize (default NemoMix-Unleashed-12B). Each model is only
  offered the modes it can run in: one too large to sit beside SDXL (over
  `CPU_IMAGES_MAX_GB`, or listed in `CHAT_MODEL_PROFILES`) is GPU-chat only,
  no images.
- **conversation with images** — same chat, plus an image "from the assistant"
  auto-generated after every reply, flowing into a session sidebar.
- **image only** — type a prompt, get an SDXL image (any checkpoint in the
  models dir, selectable at Initialize). Optionally attach one headshot and the
  image comes back with that person's likeness (IP-Adapter FaceID).

See the repo `CLAUDE.md` → "Chloe / ephemeral generation tool (AI box)" for the
architecture and management commands. This README covers redeploy + the design
rules that must not be "fixed" away.

## The two hard guarantees

1. **End-to-end encrypted through Cloudflare.** The password never crosses the
   network and is never stored — not even server-side. The browser derives two
   independent halves from it with PBKDF2-SHA256 (210 000 iterations): an
   **auth key** and an **encryption key**. The server keeps only the auth half,
   a login verifier that can check a challenge/response proof but cannot
   decrypt anything. Login also runs an ephemeral ECDH (P-256) exchange and the
   session key is `HMAC(auth key, nonce ‖ DH-shared)` — mixing in the auth key
   authenticates the exchange, so a relay without the password can't sit in the
   middle, and the DH share gives forward secrecy: both ephemeral privates die
   with the session, so recorded traffic stays undecryptable even by someone
   who later learns the password. The encryption key arrives at login wrapped
   under that session key and lives in server RAM only. Every request/response
   body after login is an AES-GCM envelope under the session's own key.
   Cloudflare's edge terminates TLS but only ever sees ciphertext it has no key
   for. The browser holds its keys in plain JS variables — never
   localStorage/sessionStorage — so a reload requires the password again, by
   construction.
2. **The server keeps no conversation at all.** The chat history and both
   image galleries live in the browser tab's memory. Each request carries what
   it needs (the history for a chat turn, the last few messages for a scene
   image) inside the envelope; the server decrypts it, generates, encrypts the
   answer and drops it when the request ends. Close the tab and the only copy
   is gone: there is nothing left on the box to decrypt, with or without a
   key. The one exception is a chat image waiting to be polled, which waits
   *already encrypted* under the session key and is deleted on delivery.
   A session itself is just a key and a timestamp. It ends when the tab closes
   (a `pagehide` beacon to `/api/logout`), at the 20-minute idle reaper as the
   backstop for a crashed browser, or on restart. When the last session ends,
   the prompt-store key and the decrypted prompts are wiped from memory too.

   What this cannot cover: plaintext must be in this process's RAM *while a
   request is served* (the model reads it), and Python can't overwrite freed
   memory, so fragments may linger in RAM until reused. Hence the unit forbids
   swap (`MemorySwapMax=0`) and core dumps (`LimitCORE=0`, plus apport disabled
   on the box): nothing in this process's memory may ever reach the disk. RAM
   is all there is, and power-off clears it.

The **only** two things persisted to disk (in `/var/lib/imagegen/` — systemd's
`StateDirectory=imagegen`, mode 0700, both files mode 0600):

- **`k_auth`** — the auth half of the PBKDF2 output, base64. A login verifier
  and nothing more: PBKDF2's output halves are computationally independent, so
  it says nothing about the encryption key. Someone who reads this disk can
  impersonate the login check; they cannot read `prompts.enc`.
- **`prompts.enc`** — the editable prompts, AES-GCM encrypted under the
  encryption half, which is **never written here**. A fresh process starts
  locked out of its own prompt store and stays that way until a browser that
  knows the password logs in and hands the key over.

Seeding: the first start under this scheme derives the verifier from a legacy
plaintext `password` file if one is present (upgrade path) and **deletes it**,
otherwise from `IMAGEGEN_PASSWORD` (systemd
`EnvironmentFile=/etc/nmteaco/imagegen.env`). After that `k_auth` is
authoritative and neither source is read again. Password changes go through the
UI, not these files.

**Recovery.** Login succeeds only if the delivered encryption key actually
decrypts `prompts.enc`, so a `k_auth`/`prompts.enc` pair from *different*
passwords is a lockout with no way in. Restore both files together, or delete
them: removing `prompts.enc` re-seeds the prompt store from the defaults in
`app.py` at the next login, and removing `k_auth` too re-derives the verifier
from `IMAGEGEN_PASSWORD`.

## Settings (after login)

- **Editable prompts.** The assistant's system prompt and the image-prompt
  prefix are editable in the UI and stored encrypted. Changing the password
  re-encrypts them under the new key.
- **Change password.** The browser derives both halves of the new password
  (min 8 chars — enforced client-side, since the server never sees a password
  or even its length) and sends them over the current session's envelope. The
  server replaces `k_auth`, re-encrypts the prompts under the new encryption
  key, and clears every session so everyone re-logs-in.

## Files

| File | Deployed to | Purpose |
| --- | --- | --- |
| `app.py` | `~/imagegen/app.py` | The whole backend (FastAPI, single file). |
| `chat_worker.py` | `~/imagegen/chat_worker.py` | GPU-chat subprocess (`gpu_chat` mode); spawned as a sibling of `app.py`. |
| `static/index.html` | `~/imagegen/static/index.html` | HTML shell + CSS. |
| `static/app.js` | `~/imagegen/static/app.js` | All client crypto + UI (self-hosted, `script-src 'self'`). |
| `imagegen.service` | `/etc/systemd/system/imagegen.service` | Hardened systemd unit. |

Not in git: the model checkpoints (`~/imagegen/models/*.safetensors`, `*.gguf`),
the IP-Adapter FaceID weights (`~/imagegen/models/ip_adapter/`), and the
contents of `/var/lib/imagegen/`. systemd creates the state directory itself, so
a fresh box only needs `IMAGEGEN_PASSWORD` set — the verifier and the prompt
store build themselves from there. See CLAUDE.md for how to re-fetch the models.

## Redeploy after an edit

```bash
# from a machine with `ssh aibox` configured (see repo CLAUDE.md)
tar -C imagegen -czf - app.py chat_worker.py static/app.js static/index.html \
  | ssh aibox 'tar -C ~/imagegen -xzf -'
scp imagegen/imagegen.service aibox:~/imagegen.service
ssh aibox 'sudo cp ~/imagegen.service /etc/systemd/system/imagegen.service && \
           sudo systemctl daemon-reload && sudo systemctl restart imagegen.service'
```

## Models load on demand ("Initialize")

Nothing loads at process start — cold footprint is ~700 MB. **Initialize**
(`/api/initialize`) loads the models, and the chat model, image checkpoint and
mode are all chosen there. The two modes are mutually exclusive on a 6 GB card:

- **`cpu_images`** (default) — SDXL on the GPU + the chat model in-process on
  the CPU. Required for anything that generates images.
- **`gpu_chat`** — the chat model offloaded to the GPU, in a `chat_worker.py`
  subprocess run under `~/transcribe-env` (which has the CUDA llama build).
  SDXL is not loaded and image generation is disabled.

Steady state in `cpu_images` with both models loaded is ~17 GB RAM (one-time
CUDA/accelerate init on the first generation, then flat — see the memory notes
below). The systemd unit caps memory at 22 G/26 G as a safety net. "Shut down
models" (`/api/unload`) unloads, zeroes free VRAM and restarts the process to
wipe its memory (see "Second audit"); the conversation stays in the browser.

## Notes & caveats

- **6 GB VRAM (RTX 2060).** Normal generation fits via
  `enable_model_cpu_offload()` plus the TAESD tiny VAE (`AutoencoderTiny`) —
  the full VAE's decode spike is what used to OOM the card. ~20 s/image. The
  FaceID reference pipe can't fit that way, so it uses
  `enable_sequential_cpu_offload()` (~650 MB VRAM, ~50 s/image), and only
  **one** SDXL pipe is resident at a time: both pipes plus the 12B chat model
  peaked ~31 GB RSS and OOM'd. Alternating normal↔reference costs a ~15 s
  rebuild, which is the price of not OOMing.
- **Chat is CPU-only in the default mode** (`n_gpu_layers=0`), and imagegen-env
  deliberately carries the **CPU-only** llama-cpp-python build: the CUDA build
  reserves ~1 GB of VRAM even at zero offloaded layers, which is exactly enough
  to push SDXL's generation peak into OOM. GPU chat is the separate `gpu_chat`
  mode, in its own process, with SDXL unloaded — not a flag on this one.
- **All GPU work is pinned to one dedicated thread** (`_gpu_executor`,
  max_workers=1), likewise the LLM (`_llm_executor`). This is load-bearing:
  spreading generation calls across a thread pool leaked memory (confirmed OOM
  at 16 GB+ under live use) via per-thread CUDA/accelerate state. One persistent
  thread each avoids it. Do not switch these back to the default executor.
- **Unloading must call `close()` and `malloc_trim`.** Dropping the Python
  reference to the chat model frees nothing — llama-cpp-python holds the
  weights in C — and glibc keeps freed arenas resident afterwards. Without both
  steps the process reports "cold" while sitting at ~9.6 GB RSS until a
  restart. `_unload_models` logs `RSS before -> after` so a regression shows up
  instead of being inferred.
- **Conversation-with-images is async.** The reply returns immediately; the
  image generates in the background and the client polls `/api/image-status`
  (it keeps polling if you leave the view, since the server hands the image
  over once and keeps no copy),
  showing a shimmer placeholder meanwhile. This also keeps a single request
  under Cloudflare's ~100 s origin timeout (LLM + SDXL sequentially can exceed
  it).
- **A stale envelope key is a 401, not a 500.** Session cookies are per-host,
  not per-tab, so after a restart or password change an older tab sends a valid
  cookie holding the previous session key. `_decrypt` raises `StaleSessionKey`
  and the handler answers 401, which the client already treats as "session
  expired". Returning a plain-text 500 here is what made this look like broken
  crypto (`Unexpected token 'I', "Internal S"...` in the browser).
- **Caching:** the Cloudflare Cache Rule (bypass for `aibox.nmteaco.com`) plus
  `Cache-Control: no-store` and a strict CSP are all defense-in-depth for the
  ephemerality guarantee.
- **Don't add persistence.** The two files above are the entire disk story.
  Saving conversations, images or history is a deliberate policy violation
  here, not a missing feature — flag it, don't add it. That includes writing
  the encryption key or the password anywhere: the server being unable to read
  its own prompt store until someone logs in is the design, not an oversight.

## Security audit (what was checked, and two fixes that came out of it)

Verified: responses carry `Cache-Control: no-store` and Cloudflare returns
`cf-cache-status: DYNAMIC` (edge not caching); the client uses **no**
localStorage/sessionStorage/IndexedDB (keys live in JS variables only);
generated images are written to an in-memory `BytesIO`, never a file; the app
logs no message/prompt/reply content; the only disk state is the login verifier
and the prompt ciphertext, and nothing on that disk can decrypt anything.

Two leaks were found and fixed — both mattered for "mechanically unrecoverable":

1. **ML libraries logged prompt text to persistent journald.** The CLIP
   tokenizer logs the truncated tail of every image prompt at WARNING →
   `/var/log/journal` on disk (real conversation-derived content, unencrypted,
   surviving restarts). Fixed by forcing `transformers`/`diffusers` logging to
   ERROR at startup (see the "log hygiene" block in `app.py`) — verified a
   >77-token prompt now leaves nothing in the journal. Do not lower this.
2. **Service memory was swappable.** `MemorySwapMax=infinity` meant the ~17 GB
   of plaintext conversations + in-memory images could be paged to the on-disk
   swap. Fixed with `MemorySwapMax=0` in the unit — under pressure the process
   is OOM-killed and restarted (memory cleared) rather than swapping secrets to
   disk.

Residual note: journald is persistent (`/var/log/journal`), but after fix #1 it
only holds request *paths* (`POST /api/chat`), never bodies. System-wide swap
is still enabled for other processes; only this service is barred from it,
which is the correct scope.

### Second audit (2026-10-11): conversations moved out of the server

Asked: "once the tab is closed, can anyone recover a prompt, conversation or
image, even with the physical box?" Under the old design, yes:

1. **A crash dump.** `/var/crash` held a ~500 MB apport report with the full
   memory image of an `imagegen-env` Python process (Jul 21, SIGABRT).
   `MemorySwapMax=0` does nothing for core dumps, and the unit had
   `LimitCORE=infinity`. Deleted (along with the whole journal, at the owner's
   request); `LimitCORE=0` added and apport disabled.
2. **The server held every conversation in plaintext**, keyed to a session it
   kept after the tab closed - and the deployed build (older than `main`) only
   swept abandoned sessions when someone next logged in, so a conversation
   could sit in RAM indefinitely. The browser's key protected the *traffic*,
   not what the server stored. Fixed structurally: the browser now holds the
   conversation (see guarantee 2), and closing the tab ends the session.
3. **The prompt-store key never left memory** once anyone had logged in. It is
   now wiped when the last session ends.

Still true and worth knowing: the AI box has no disk encryption and Secure
Boot is off, so anything that *does* reach its disk is readable by whoever
holds the box; `prompts.enc` sits next to `k_auth`, so with the disk in hand
its protection is the password's strength against an offline guessing attack
(PBKDF2-SHA256, 210k iterations, fixed salt). Use a long passphrase.

**"Shut down models" wipes memory physically.** The leftover fragments are
*plaintext* (decrypted requests, replies, the model's KV cache, image pixels),
not ciphertext - decryption has to happen before the model can read anything.
So the button does three things: unload the models, zero-fill every free byte
of VRAM (`_scrub_vram`; the driver doesn't promise to clear freed GPU memory),
then exit the process - after the response is sent - so the kernel reclaims
every page it held. The kernel runs with **`init_on_free=1`**
(`/etc/default/grub.d/90-init-on-free.cfg`), so reclaimed pages are zeroed at
once instead of keeping their contents until reuse (`init_on_alloc` was
already on, which only stops *other processes* reading them). systemd
(`Restart=always`) brings Chloe back cold in a few seconds; every session ends
with it, and the browser waits for a response carrying a new `X-Chloe-Boot`
id, then asks for the password again, keeping the tab's conversation.
`/api/release-gpu` (the Captain's Log asking for the card) unloads without the
restart, so it never logs out someone mid-conversation.

`tests/test_stateless.py` exercises the server side of all this with the ML
stack stubbed out (`python3 imagegen/tests/test_stateless.py`; needs
`fastapi`, `httpx`, `cryptography`).
