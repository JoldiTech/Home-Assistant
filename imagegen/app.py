"""
Ephemeral, single-purpose image generator (JuggernautXL Ragnarok / SDXL).

Design constraints (deliberate, do not "fix"):
  - Nothing is ever written to disk. Generated images live only as in-memory
    PNG bytes for the duration of one request, then are garbage collected.
  - No database, no history, no per-user state. GET / is always a blank form.
  - Session state (post-login only) lives in a plain in-process dict and is
    wiped on every restart - there is nothing to migrate or persist.
  - Zero client-side JavaScript, so there is nothing to XSS/inject into and
    the CSP can be maximally restrictive (script-src 'none').
"""
import base64
import hmac
import io
import os
import secrets
import threading
import time
from collections import defaultdict, deque

import torch
from diffusers import StableDiffusionXLPipeline
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

PASSWORD = os.environ["IMAGEGEN_PASSWORD"]
MODEL_PATH = os.environ.get(
    "MODEL_PATH", os.path.expanduser("~/imagegen/models/juggernautXL_ragnarok.safetensors")
)
PORT = int(os.environ.get("PORT", "8189"))
SESSION_TTL = int(os.environ.get("SESSION_TTL_SECONDS", str(2 * 60 * 60)))  # 2h

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60

STEPS = 32
GUIDANCE = 6.0
SIZE = 1024

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# --- in-memory-only state (dies with the process, by design) ----------------
_sessions: dict[str, float] = {}
_login_attempts: dict[str, deque] = defaultdict(deque)
_state_lock = threading.Lock()
_gpu_lock = threading.Lock()

pipe = None  # loaded at startup


def _client_ip(request: Request) -> str:
    # Traffic arrives via the Cloudflare Tunnel on loopback, so the real
    # visitor IP is in this header, not request.client.host.
    return request.headers.get("cf-connecting-ip", request.client.host)


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _state_lock:
        attempts = _login_attempts[ip]
        while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= LOGIN_MAX_ATTEMPTS


def _record_failure(ip: str) -> None:
    with _state_lock:
        _login_attempts[ip].append(time.time())


def _new_session() -> str:
    token = secrets.token_urlsafe(32)
    with _state_lock:
        _sessions[token] = time.time() + SESSION_TTL
    return token


def _valid_session(request: Request) -> bool:
    token = request.cookies.get("session")
    if not token:
        return False
    with _state_lock:
        expiry = _sessions.get(token)
        if expiry is None:
            return False
        if time.time() > expiry:
            del _sessions[token]
            return False
        return True


SECURITY_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; "
        "script-src 'none'; form-action 'self'; base-uri 'none'"
    ),
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v
    return response


PAGE_STYLE = """
body{font-family:system-ui,sans-serif;max-width:640px;margin:4rem auto;padding:0 1rem;
     background:#111;color:#eee}
input,textarea,button{font-size:1rem;padding:.6rem;width:100%;box-sizing:border-box;
     margin-top:.5rem;background:#222;color:#eee;border:1px solid #444;border-radius:6px}
button{background:#3a6;color:#fff;border:none;cursor:pointer;margin-top:1rem}
button:hover{background:#4b7}
.err{color:#f66}
img{max-width:100%;border-radius:8px;margin-top:1rem}
a{color:#6cf}
"""


def _login_page(error: str = "") -> str:
    err_html = f'<p class="err">{error}</p>' if error else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>locked</title><style>{PAGE_STYLE}</style></head><body>
<h2>locked</h2>{err_html}
<form method="post" action="/login">
<input type="password" name="password" placeholder="password" autofocus autocomplete="off" required>
<button type="submit">enter</button>
</form></body></html>"""


def _prompt_page() -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>generate</title><style>{PAGE_STYLE}</style></head><body>
<h2>generate</h2>
<form method="post" action="/generate">
<textarea name="prompt" rows="3" placeholder="describe the image..." autofocus required></textarea>
<button type="submit">generate</button>
</form></body></html>"""


def _result_page(b64png: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>generate</title><style>{PAGE_STYLE}</style></head><body>
<img src="data:image/png;base64,{b64png}" alt="generated image">
<p><a href="/">new prompt</a></p>
</body></html>"""


@app.get("/")
async def index(request: Request):
    if not _valid_session(request):
        return HTMLResponse(_login_page())
    return HTMLResponse(_prompt_page())


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return HTMLResponse(_login_page("too many attempts, try later"), status_code=429)
    if not hmac.compare_digest(password, PASSWORD):
        _record_failure(ip)
        return HTMLResponse(_login_page("incorrect password"), status_code=401)
    token = _new_session()
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "session", token, max_age=SESSION_TTL, httponly=True, secure=True, samesite="strict"
    )
    return resp


@app.post("/generate")
async def generate(request: Request, prompt: str = Form(...)):
    if not _valid_session(request):
        return RedirectResponse("/", status_code=303)

    prompt = prompt.strip()[:2000]
    if not prompt:
        return HTMLResponse(_prompt_page())

    def _run() -> bytes:
        with _gpu_lock:
            image = pipe(
                prompt=prompt,
                num_inference_steps=STEPS,
                guidance_scale=GUIDANCE,
                height=SIZE,
                width=SIZE,
            ).images[0]
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    import asyncio

    png_bytes = await asyncio.get_event_loop().run_in_executor(None, _run)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    del png_bytes  # nothing to keep around once encoded into the response
    return HTMLResponse(_result_page(b64))


@app.on_event("startup")
async def load_model():
    global pipe
    print("loading SDXL checkpoint...", flush=True)
    pipe = StableDiffusionXLPipeline.from_single_file(
        MODEL_PATH, torch_dtype=torch.float16, use_safetensors=True
    )
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_slicing()
    print("model ready", flush=True)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
