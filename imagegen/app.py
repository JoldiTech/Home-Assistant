"""
Ephemeral, end-to-end-encrypted, three-mode generation tool:
  - conversation            (Gemma-4-12B-OBLITERATED, CPU inference)
  - conversation with images (same, + an auto-generated image every turn)
  - image only               (JuggernautXL Ragnarok / SDXL, GPU)

Design constraints (deliberate, do not "fix"):
  - Nothing is ever written to disk. All state - login sessions, chat
    history, generated images - lives only in this process's memory and
    is gone on restart, idle-timeout, or explicit reset.
  - The password is never transmitted, not even at login: both browser and
    server independently derive the same AES/HMAC key material from it
    (PBKDF2), so a login is a challenge/response proof, not a password
    submission. Every request/response body after that is an AES-GCM
    envelope encrypted with that key - Cloudflare's edge (or anything else
    on the path) only ever relays ciphertext it has no key for.
  - The browser never persists the derived key anywhere (no localStorage/
    sessionStorage) - only an in-memory JS variable - so a page reload
    requires the password again by construction, not by policy.
  - The image model (GPU) and the chat model (CPU-only, n_gpu_layers=0)
    were deliberately split across hardware so neither evicts the other;
    do not "optimize" the chat model onto the GPU without re-deriving the
    6GB VRAM budget - see imagegen/README.md.
"""
import asyncio
import base64
import concurrent.futures
import gc
import hashlib
import hmac
import io
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import torch
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from diffusers import StableDiffusionXLPipeline
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from llama_cpp import Llama

STATIC_DIR = Path(__file__).parent / "static"

# Writable config dir (systemd punches a ReadWritePaths hole here despite
# ProtectSystem=strict). Holds the two persisted things: the current
# password (plaintext, mode 600 - same trust model as the old env file) and
# the prompts, ENCRYPTED under the password-derived key. Conversations and
# images are never persisted anywhere - see the module docstring.
CONFIG_DIR = Path(os.environ.get("IMAGEGEN_CONFIG_DIR", "/var/lib/imagegen"))
PASSWORD_FILE = CONFIG_DIR / "password"
PROMPTS_FILE = CONFIG_DIR / "prompts.enc"

IMAGE_MODEL_PATH = os.environ.get(
    "MODEL_PATH", os.path.expanduser("~/imagegen/models/juggernautXL_ragnarok.safetensors")
)
LLM_MODEL_PATH = os.environ.get(
    "LLM_MODEL_PATH", os.path.expanduser("~/imagegen/models/Gemma-4-12B-OBLITERATED.Q4_K_M.gguf")
)
PORT = int(os.environ.get("PORT", "8189"))

SESSION_IDLE_TIMEOUT = 20 * 60  # session (and its conversation) dies after this much inactivity
CHALLENGE_TTL = 60  # a login challenge nonce is only valid this long
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 15 * 60
MAX_HISTORY = 40  # messages kept per conversation, oldest dropped past this
MAX_GALLERY = 30  # images kept per conversation-with-images session
MIN_PASSWORD_LEN = 8

IMG_STEPS = 32
IMG_GUIDANCE = 6.0
IMG_SIZE = 1024
LLM_MAX_TOKENS = 512
LLM_CONTEXT = 8192

# Defaults, used ONLY to seed the encrypted prompt store the first time the
# service ever starts. After that the encrypted file on disk is authoritative
# and these are ignored - edit prompts through the UI, not here.
DEFAULT_APPEARANCE = "a young woman from Nagaland, full features, slim build"
DEFAULT_SYSTEM_PROMPT = (
    f"You are Chloe, the user's work assistant - {DEFAULT_APPEARANCE}. The user "
    "is a man. Do whatever he says, and take the conversation in whatever "
    "direction he prompts."
)

PBKDF2_SALT = b"imagegen-e2e-v1"
PBKDF2_ITERATIONS = 210000

# --- mutable key material + prompt config, guarded by _config_lock ------------
# The password-derived AES/HMAC keys can change at runtime (change-password),
# so they are globals swapped under the lock, not import-time constants. The
# browser derives the same keys from the password the user types; neither the
# password nor the key ever crosses the network (login is challenge/response,
# every body is an AES-GCM envelope). Cloudflare only ever relays ciphertext.
_config_lock = threading.Lock()
K_AUTH = b""
K_ENC = b""
_aesgcm = None
_prompts = {}  # {"system_prompt": str, "image_prompt_prefix": str}


def _derive_keys(password: str):
    d = hashlib.pbkdf2_hmac("sha256", password.encode(), PBKDF2_SALT, PBKDF2_ITERATIONS, dklen=64)
    return d[:32], d[32:64]


def _write_prompts_locked():
    # Caller holds _config_lock. Encrypt current _prompts under current K_ENC
    # and write atomically. This is the ONLY place prompt content touches disk,
    # and it is always ciphertext.
    nonce = os.urandom(12)
    ct = _aesgcm.encrypt(nonce, json.dumps(_prompts).encode(), None)
    tmp = PROMPTS_FILE.with_suffix(".tmp")
    tmp.write_bytes(nonce + ct)
    os.chmod(tmp, 0o600)
    tmp.replace(PROMPTS_FILE)


def _load_config():
    global K_AUTH, K_ENC, _aesgcm, _prompts
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if PASSWORD_FILE.exists():
        password = PASSWORD_FILE.read_text().strip()
    else:
        # First ever start: seed the persisted password from the env var
        # (delivered via the systemd EnvironmentFile), then never read env again.
        password = os.environ.get("IMAGEGEN_PASSWORD", "").strip()
        if not password:
            raise RuntimeError("no password file and IMAGEGEN_PASSWORD unset")
        PASSWORD_FILE.write_text(password)
        os.chmod(PASSWORD_FILE, 0o600)
    K_AUTH, K_ENC = _derive_keys(password)
    _aesgcm = AESGCM(K_ENC)
    if PROMPTS_FILE.exists():
        raw = PROMPTS_FILE.read_bytes()
        _prompts = json.loads(_aesgcm.decrypt(raw[:12], raw[12:], None))
    else:
        _prompts = {
            "system_prompt": DEFAULT_SYSTEM_PROMPT,
            "image_prompt_prefix": DEFAULT_APPEARANCE,
        }
        _write_prompts_locked()


_load_config()

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# --- in-memory-only state (dies with the process, by design) ----------------
_sessions: dict[str, dict] = {}
_challenges: dict[str, float] = {}
_login_attempts: dict[str, deque] = defaultdict(deque)
_state_lock = threading.Lock()
_gpu_lock = threading.Lock()
_llm_lock = threading.Lock()
# All GPU work (and, for consistency, all LLM work) is pinned to ONE
# dedicated worker thread each, for the life of the process - not the
# default shared executor, which spreads calls across a pool of threads.
# Repeated generation calls landing on different pool threads was
# implicated in real, confirmed memory growth (16GB+ under moderate live
# use) - almost certainly per-thread CUDA/accelerate initialization state
# that never gets torn down. A single persistent thread avoids that
# entirely instead of trying to clean it up after the fact.
_gpu_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_llm_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

image_pipe = None
llm = None
_model_status_lock = threading.Lock()
_model_status = "cold"  # cold -> loading -> ready -> error


def _client_ip(request: Request) -> str:
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


def _new_conversation_state() -> dict:
    return {
        "chat": {"history": []},
        "chat_images": {"history": [], "gallery": [], "jobs": {}},
        "image": {"gallery": []},
    }


def _touch_session(token: str) -> dict | None:
    """Validate + refresh a session's idle timer. Returns its state, or None if invalid/expired."""
    now = time.time()
    with _state_lock:
        s = _sessions.get(token)
        if s is None:
            return None
        if now - s["last_seen"] > SESSION_IDLE_TIMEOUT:
            del _sessions[token]
            return None
        s["last_seen"] = now
        return s


def _encrypt(obj, aesgcm=None) -> dict:
    aesgcm = aesgcm or _aesgcm
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, json.dumps(obj).encode(), None)
    return {"nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(ciphertext).decode()}


def _decrypt(envelope: dict, aesgcm=None):
    aesgcm = aesgcm or _aesgcm
    nonce = base64.b64decode(envelope["nonce"])
    ciphertext = base64.b64decode(envelope["ciphertext"])
    return json.loads(aesgcm.decrypt(nonce, ciphertext, None))


SECURITY_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/challenge")
async def challenge():
    nonce = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce).decode()
    now = time.time()
    with _state_lock:
        # opportunistic sweep of expired challenges and idle sessions - a
        # session's own idle check only runs when THAT token is reused, so
        # without this, sessions nobody ever revisits (e.g. every page
        # reload starts a fresh login here, by design - see app.js) would
        # sit in memory, galleries and all, until the process restarts.
        for k in [k for k, exp in _challenges.items() if exp < now]:
            del _challenges[k]
        for tok in [tok for tok, s in _sessions.items() if now - s["last_seen"] > SESSION_IDLE_TIMEOUT]:
            del _sessions[tok]
        _challenges[nonce_b64] = now + CHALLENGE_TTL
    return {"nonce": nonce_b64}


@app.post("/api/login")
async def login(request: Request):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse({"error": "too many attempts"}, status_code=429)

    body = await request.json()
    nonce_b64 = body.get("nonce", "")
    proof_b64 = body.get("proof", "")

    with _state_lock:
        expiry = _challenges.pop(nonce_b64, None)
    if expiry is None or expiry < time.time():
        _record_failure(ip)
        return JSONResponse({"error": "expired or invalid challenge"}, status_code=401)

    nonce = base64.b64decode(nonce_b64)
    expected = hmac.new(K_AUTH, nonce, hashlib.sha256).digest()
    given = base64.b64decode(proof_b64) if proof_b64 else b""
    if not hmac.compare_digest(expected, given):
        _record_failure(ip)
        return JSONResponse({"error": "incorrect password"}, status_code=401)

    token = base64.urlsafe_b64encode(os.urandom(32)).decode()
    with _state_lock:
        _sessions[token] = {"last_seen": time.time(), **_new_conversation_state()}

    resp = JSONResponse({"ok": True})
    # Session cookie only - no Max-Age, so the browser drops it when it closes.
    # The idle timeout below is the practical backstop, since a server can't
    # get a hard signal the instant a tab actually closes.
    resp.set_cookie("session", token, httponly=True, secure=True, samesite="strict")
    return resp


def _require_session(request: Request) -> tuple[str, dict] | JSONResponse:
    token = request.cookies.get("session")
    if not token:
        return JSONResponse({"error": "no session"}, status_code=401)
    state = _touch_session(token)
    if state is None:
        return JSONResponse({"error": "session expired"}, status_code=401)
    return token, state


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get("session")
    if token:
        with _state_lock:
            _sessions.pop(token, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("session")
    return resp


@app.post("/api/reset")
async def reset(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    token, state = result
    body = _decrypt(await request.json())
    mode = body.get("mode")
    with _state_lock:
        if mode in ("chat", "chat_images", "image"):
            state[mode] = _new_conversation_state()[mode]
    return JSONResponse(_encrypt({"ok": True}))


@app.post("/api/get-prompts")
async def get_prompts(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    with _config_lock:
        payload = dict(_prompts)
    return JSONResponse(_encrypt(payload))


@app.post("/api/set-prompts")
async def set_prompts(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    body = _decrypt(await request.json())
    system_prompt = (body.get("system_prompt") or "").strip()[:8000]
    image_prompt_prefix = (body.get("image_prompt_prefix") or "").strip()[:2000]
    if not system_prompt or not image_prompt_prefix:
        return JSONResponse(_encrypt({"error": "both prompts are required"}))
    with _config_lock:
        _prompts["system_prompt"] = system_prompt
        _prompts["image_prompt_prefix"] = image_prompt_prefix
        _write_prompts_locked()  # re-encrypt to disk under the current key
    return JSONResponse(_encrypt({"ok": True}))


@app.post("/api/change-password")
async def change_password(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    global K_AUTH, K_ENC, _aesgcm
    # The request is encrypted under the CURRENT (old) key; capture it so the
    # ack can be encrypted under it too - the browser is still holding the old
    # key and won't re-derive until it re-logs-in.
    old_aesgcm = _aesgcm
    body = _decrypt(await request.json(), aesgcm=old_aesgcm)
    new_password = (body.get("new_password") or "").strip()
    if len(new_password) < MIN_PASSWORD_LEN:
        return JSONResponse(
            _encrypt({"error": f"password must be at least {MIN_PASSWORD_LEN} characters"}, old_aesgcm)
        )
    with _config_lock:
        PASSWORD_FILE.write_text(new_password)
        os.chmod(PASSWORD_FILE, 0o600)
        K_AUTH, K_ENC = _derive_keys(new_password)
        _aesgcm = AESGCM(K_ENC)
        _write_prompts_locked()  # re-encrypt prompts under the NEW key
    # Force everyone (including this browser) to re-login with the new password.
    with _state_lock:
        _sessions.clear()
    return JSONResponse(_encrypt({"ok": True}, old_aesgcm))


@app.post("/api/state")
async def get_state(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    token, state = result
    body = _decrypt(await request.json())
    mode = body.get("mode")
    mode_state = state.get(mode, {})
    return JSONResponse(
        _encrypt({"history": mode_state.get("history", []), "gallery": mode_state.get("gallery")})
    )


def _run_llm(history: list[dict]) -> str:
    messages = [{"role": "system", "content": _prompts["system_prompt"]}, *history]
    with _llm_lock:
        completion = llm.create_chat_completion(messages=messages, max_tokens=LLM_MAX_TOKENS)
    return completion["choices"][0]["message"]["content"].strip()


def _run_image(prompt: str) -> str:
    with _gpu_lock:
        image = image_pipe(
            prompt=prompt, num_inference_steps=IMG_STEPS, guidance_scale=IMG_GUIDANCE,
            height=IMG_SIZE, width=IMG_SIZE,
        ).images[0]
        # 6GB is a tight budget for SDXL's VAE-decode memory spike specifically -
        # release cached (but unused) allocator blocks between calls rather than
        # letting them accumulate/fragment across requests.
        torch.cuda.empty_cache()
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    result = base64.b64encode(buf.getvalue()).decode("ascii")
    del image, buf
    gc.collect()
    return result


@app.post("/api/chat")
async def chat(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    if _model_status != "ready":
        return JSONResponse({"error": "not initialized"}, status_code=503)
    token, state = result
    body = _decrypt(await request.json())
    message = (body.get("message") or "").strip()[:4000]
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    history = state["chat"]["history"]
    history.append({"role": "user", "content": message})
    reply = await asyncio.get_event_loop().run_in_executor(_llm_executor, _run_llm, history[-MAX_HISTORY:])
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]

    return JSONResponse(_encrypt({"reply": reply}))


@app.post("/api/chat-images")
async def chat_images(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    if _model_status != "ready":
        return JSONResponse({"error": "not initialized"}, status_code=503)
    token, state = result
    body = _decrypt(await request.json())
    message = (body.get("message") or "").strip()[:4000]
    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    mode_state = state["chat_images"]
    history = mode_state["history"]
    history.append({"role": "user", "content": message})
    reply = await asyncio.get_event_loop().run_in_executor(_llm_executor, _run_llm, history[-MAX_HISTORY:])
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]

    # Image generation is deliberately NOT awaited here - it runs in the
    # background and the client polls /api/image-status for it. Doing both
    # in one request risks exceeding Cloudflare's ~100s origin timeout
    # (LLM CPU inference + ~30s SDXL can add up), and the reply shouldn't
    # wait on the image anyway.
    # Every image depicts Chloe herself, consistently, as though she's the
    # one sending it - not an unrelated scene derived from the topic.
    context = f"{message}. {reply}"[:300]
    image_prompt = f"{_prompts['image_prompt_prefix']}, {context}"
    job_id = secrets.token_urlsafe(8)
    jobs = mode_state["jobs"]
    jobs[job_id] = {"status": "pending", "image": None}
    for old_id in list(jobs)[:-10]:
        del jobs[old_id]

    def _generate_and_store():
        try:
            image_b64 = _run_image(image_prompt)
            with _state_lock:
                jobs[job_id] = {"status": "done", "image": image_b64}
                gallery = mode_state["gallery"]
                gallery.append(image_b64)
                del gallery[:-MAX_GALLERY]
        except Exception:
            with _state_lock:
                jobs[job_id] = {"status": "error", "image": None}

    # Fire-and-forget on the shared default executor - NOT a raw
    # threading.Thread(...).start(), which spins up a brand-new OS thread
    # per call that's used exactly once. Under repeated chat-images turns
    # that pattern was implicated in a real memory leak (the service got
    # OOM-killed under moderate live use); the shared executor reuses a
    # bounded pool of threads instead.
    asyncio.get_event_loop().run_in_executor(_gpu_executor, _generate_and_store)

    return JSONResponse(_encrypt({"reply": reply, "job_id": job_id}))


@app.post("/api/image-status")
async def image_status(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    token, state = result
    body = _decrypt(await request.json())
    mode = body.get("mode")
    job_id = body.get("job_id")
    jobs = state.get(mode, {}).get("jobs", {})
    job = jobs.get(job_id, {"status": "error", "image": None})
    return JSONResponse(_encrypt(job))


@app.post("/api/image")
async def image_only(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    if _model_status != "ready":
        return JSONResponse({"error": "not initialized"}, status_code=503)
    token, state = result
    body = _decrypt(await request.json())
    prompt = (body.get("prompt") or "").strip()[:2000]
    if not prompt:
        return JSONResponse({"error": "empty prompt"}, status_code=400)

    image_b64 = await asyncio.get_event_loop().run_in_executor(_gpu_executor, _run_image, prompt)
    gallery = state["image"]["gallery"]
    gallery.append(image_b64)
    del gallery[:-MAX_GALLERY]
    return JSONResponse(_encrypt({"image": image_b64}))


def _load_models():
    # Runs on the shared executor, kicked off by /api/initialize - nothing
    # loads at process startup, so the idle process footprint is near-zero
    # until someone actually asks for it (see the "Initialize" button in the
    # UI). Both models are loaded together since either mode can be opened
    # once ready; there is currently no auto-unload - they stay resident
    # until the process restarts.
    global image_pipe, llm, _model_status
    try:
        print("loading SDXL checkpoint (GPU)...", flush=True)
        image_pipe = StableDiffusionXLPipeline.from_single_file(
            IMAGE_MODEL_PATH, torch_dtype=torch.float16, use_safetensors=True
        )
        image_pipe.enable_model_cpu_offload()
        image_pipe.vae.enable_slicing()
        print("image model ready", flush=True)

        print("loading Gemma-4-12B-OBLITERATED (CPU)...", flush=True)
        llm = Llama(
            model_path=LLM_MODEL_PATH,
            n_ctx=LLM_CONTEXT,
            n_threads=8,
            n_gpu_layers=0,  # deliberately CPU-only - see module docstring
            verbose=False,
        )
        print("chat model ready", flush=True)
        with _model_status_lock:
            _model_status = "ready"
    except Exception as e:
        print(f"model load failed: {e}", flush=True)
        with _model_status_lock:
            _model_status = "error"


@app.post("/api/initialize")
async def initialize(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    global _model_status
    with _model_status_lock:
        if _model_status == "cold":
            _model_status = "loading"
            # Load on _gpu_executor's thread specifically, so the CUDA
            # context SDXL initializes here is the same one every later
            # _run_image call reuses - not a different thread's context.
            asyncio.get_event_loop().run_in_executor(_gpu_executor, _load_models)
        status = _model_status
    return JSONResponse(_encrypt({"status": status}))


@app.post("/api/init-status")
async def init_status(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(_encrypt({"status": _model_status}))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
