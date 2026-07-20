# Chloe — ephemeral, E2E-encrypted chat + image tool (AI box)

A single password-gated web app on the AI box, public at
`https://aibox.nmteaco.com`, with three modes after login:

- **conversation** — chat with a local LLM (Gemma-4-12B-OBLITERATED, CPU).
- **conversation with images** — same chat, plus an image "from the assistant"
  auto-generated after every reply, flowing into a session sidebar.
- **image only** — type a prompt, get an SDXL image (JuggernautXL Ragnarok, GPU).

See the repo `CLAUDE.md` → "Chloe / ephemeral generation tool (AI box)" for the
architecture and management commands. This README covers redeploy + the design
rules that must not be "fixed" away.

## The two hard guarantees

1. **End-to-end encrypted through Cloudflare.** The password never crosses the
   network (login is HMAC challenge/response). Both browser and server derive
   the same AES-GCM key from it via PBKDF2; every request/response body is an
   encrypted envelope. Cloudflare's edge terminates TLS but only ever sees
   ciphertext it has no key for. The browser holds the derived key in a plain JS
   variable only — never localStorage/sessionStorage — so a reload requires the
   password again, by construction.
2. **Nothing conversational is ever persisted.** Conversations, generated
   images, and sessions live only in process memory and are gone on restart,
   idle-timeout (20 min), explicit reset, or tab close (session cookie, no
   Max-Age). No database, no disk writes of content, no history.

The **only** two things persisted to disk (in `/var/lib/imagegen/`, mode 600):
the current **password** (plaintext — same trust model as before; the server is
the trusted endpoint and needs it to derive keys) and the **prompts**
(`prompts.enc`, AES-GCM encrypted under the password-derived key, decrypted only
at time of use).

## Settings (after login)

- **Editable prompts.** The assistant's system prompt and the image-prompt
  prefix are editable in the UI and stored encrypted. Changing the password
  re-encrypts them under the new key.
- **Change password.** Sets a new password (min 8 chars), rewrites the password
  file, re-derives keys, re-encrypts the prompts, and clears all sessions so
  everyone re-logs-in under the new key. The new password is the new encryption
  key until changed again.

## Files

| File | Deployed to | Purpose |
| --- | --- | --- |
| `app.py` | `~/imagegen/app.py` | The whole backend (FastAPI, single file). |
| `static/index.html` | `~/imagegen/static/index.html` | HTML shell + CSS. |
| `static/app.js` | `~/imagegen/static/app.js` | All client crypto + UI (self-hosted, `script-src 'self'`). |
| `imagegen.service` | `/etc/systemd/system/imagegen.service` | Hardened systemd unit. |

Not in git: the model checkpoints (`~/imagegen/models/*.safetensors`,
`*.gguf`), and `/var/lib/imagegen/` (password + encrypted prompts). See
CLAUDE.md for how to re-fetch/re-seed them.

## Redeploy after an edit

```bash
# from a machine with `ssh aibox` configured (see repo CLAUDE.md)
tar -C imagegen -czf - app.py static/app.js static/index.html | ssh aibox 'tar -C ~/imagegen -xzf -'
scp imagegen/imagegen.service aibox:~/imagegen.service
ssh aibox 'sudo cp ~/imagegen.service /etc/systemd/system/imagegen.service && \
           sudo systemctl daemon-reload && sudo systemctl restart imagegen.service'
```

## Models load on demand ("Initialize")

Nothing loads at process start — cold footprint is ~700 MB. The **Initialize**
button (or `/api/initialize`) loads both models on first use. Steady state with
both loaded is ~17 GB RAM (one-time CUDA/accelerate init on the first
generation, then flat — see the memory-leak note below). The systemd unit caps
memory at 22 G/26 G as a safety net.

## Notes & caveats

- **6 GB VRAM (RTX 2060).** SDXL needs `enable_model_cpu_offload()` +
  `pipe.vae.enable_slicing()` to fit. The chat model is deliberately **CPU-only**
  (`n_gpu_layers=0`) so the two never contend for VRAM. ~30 s/image; chat replies
  are slower than a GPU LLM would be.
- **All GPU work is pinned to one dedicated thread** (`_gpu_executor`,
  max_workers=1), likewise the LLM (`_llm_executor`). This is load-bearing:
  spreading generation calls across a thread pool leaked memory (confirmed OOM
  at 16 GB+ under live use) via per-thread CUDA/accelerate state. One persistent
  thread each avoids it. Do not switch these back to the default executor.
- **Conversation-with-images is async.** The reply returns immediately; the
  image generates in the background and the client polls `/api/image-status`,
  showing a shimmer placeholder meanwhile. This also keeps a single request
  under Cloudflare's ~100 s origin timeout (LLM + SDXL sequentially can exceed
  it).
- **Caching:** the Cloudflare Cache Rule (bypass for `aibox.nmteaco.com`) plus
  `Cache-Control: no-store` and a strict CSP are all defense-in-depth for the
  ephemerality guarantee.
- **Don't add persistence.** Saving prompts/images/history to disk is a
  deliberate policy violation here, not a missing feature — flag it, don't add
  it.

## Security audit (what was checked, and two fixes that came out of it)

Verified: responses carry `Cache-Control: no-store` and Cloudflare returns
`cf-cache-status: DYNAMIC` (edge not caching); the client uses **no**
localStorage/sessionStorage/IndexedDB (key lives in a JS variable only);
generated images are written to an in-memory `BytesIO`, never a file; the app
logs no message/prompt/reply content; the only disk state is the password and
the encrypted prompts.

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
