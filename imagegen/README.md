# Ephemeral image generation (AI box)

A minimal, single-purpose SDXL image generator: enter a prompt, get an image,
nothing is saved. Runs on the AI box's GPU, public at `https://aibox.nmteaco.com`.

See the repo `CLAUDE.md` → "Ephemeral image generation (AI box)" for the full
architecture writeup (auth, network path, VRAM constraints, management commands).
This README covers redeploying the two files here.

## Why it's stateless by design

Nothing is written to disk, ever:

- Generated PNGs live only as in-memory bytes for one request, base64-embedded
  directly into the HTML response.
- No database, no history, no login username — one shared password, compared
  with `hmac.compare_digest`, gates the app.
- Sessions are a plain in-process `dict` — they die on restart. There is
  nothing to migrate, back up, or accumulate.
- Zero client-side JavaScript (pure HTML forms), so the CSP can set
  `script-src 'none'`.

## Files

| File | Deployed to | Purpose |
| --- | --- | --- |
| `app.py` | `~/imagegen/app.py` on the AI box | The whole app (FastAPI, single file). |
| `imagegen.service` | `/etc/systemd/system/imagegen.service` | Hardened systemd unit. |

The model checkpoint (`juggernautXL_ragnarok.safetensors`, ~6.6 GB) and the
password (`/etc/nmteaco/imagegen.env`) are **not** in this repo — see CLAUDE.md
for how to re-fetch/re-generate them.

## Redeploy after an edit

```bash
# from a machine with `ssh aibox` configured (see repo CLAUDE.md)
scp imagegen/app.py aibox:~/imagegen/app.py
scp imagegen/imagegen.service aibox:~/imagegen.service
ssh aibox 'sudo cp ~/imagegen.service /etc/systemd/system/imagegen.service && \
           sudo systemctl daemon-reload && \
           sudo systemctl restart imagegen.service'
```

## Notes & caveats

- **6 GB VRAM (RTX 2060).** `enable_model_cpu_offload()` + `pipe.vae.enable_slicing()`
  are load-bearing, not optional — SDXL at 1024×1024 doesn't fit resident in
  6 GB otherwise. Costs ~30s/image; VRAM drops back to ~150 MB idle between runs.
- **Single GPU, single in-process lock** (`_gpu_lock` in `app.py`) — concurrent
  requests queue rather than racing for VRAM. Fine for a personal tool, not
  built for multi-user concurrency.
- **Caching:** the Cloudflare-side Cache Rule (bypass for `aibox.nmteaco.com`)
  is the real guarantee against the edge ever storing a response — the app's
  own `Cache-Control: no-store` is defense in depth, not the only layer.
- **Don't add persistence.** If a future change wants to save prompts/images,
  that's a deliberate policy change, not a bug fix — flag it explicitly rather
  than "helpfully" adding a save button or history list.
