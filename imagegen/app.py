"""
Ephemeral, end-to-end-encrypted, three-mode generation tool:
  - conversation            (Gemma-4-12B-OBLITERATED, CPU inference)
  - conversation with images (same, + an auto-generated image every turn)
  - image only               (JuggernautXL Ragnarok / SDXL, GPU)

Design constraints (deliberate, do not "fix"):
  - Nothing is ever written to disk. All state - login sessions, chat
    history, generated images - lives only in this process's memory and
    is gone on restart, idle-timeout, or explicit reset.
  - The password is never transmitted OR stored, not even server-side. The
    browser derives two independent PBKDF2 halves from it: an auth key and
    an encryption key. The server persists ONLY the auth half - a login
    verifier that can check a challenge/response proof but cannot decrypt
    anything. Each login also runs an ephemeral ECDH exchange; the session
    key is HMAC(auth_key, nonce || DH-shared), so every envelope after login
    is bound to BOTH the password and a one-time secret that dies with the
    session - recorded traffic stays undecryptable forever, even by someone
    who later learns the password (forward secrecy). The prompt-store key
    arrives wrapped under the session key and lives in server RAM only: a
    fresh process is locked out of the prompt store until someone who knows
    the password logs in. Cloudflare's edge (or anything else on the path,
    or anything reading this box's disk) only ever has ciphertext.
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
import contextvars
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

import logging
import warnings

import torch
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from diffusers import AutoencoderTiny, DPMSolverMultistepScheduler, StableDiffusionXLPipeline
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from llama_cpp import Llama

# --- log hygiene (security-critical) -----------------------------------------
# The ML stack logs conversation-DERIVED text to stderr, which journald then
# persists to /var/log/journal on disk. The worst offender is the CLIP
# tokenizer, which logs the *truncated portion of every image prompt* at
# WARNING - i.e. real conversation content, unencrypted, surviving restarts.
# That violates the "nothing conversational persists" guarantee, so silence
# these libraries to ERROR and drop warnings. Do NOT lower this.
warnings.filterwarnings("ignore")
for _name in ("transformers", "diffusers"):
    logging.getLogger(_name).setLevel(logging.ERROR)
try:
    import transformers

    transformers.logging.set_verbosity_error()
except Exception:
    pass
try:
    import diffusers

    diffusers.utils.logging.set_verbosity_error()
except Exception:
    pass

STATIC_DIR = Path(__file__).parent / "static"

# Writable config dir (systemd punches a ReadWritePaths hole here despite
# ProtectSystem=strict). Holds the two persisted things: the AUTH half of
# the PBKDF2 output (a login verifier - PBKDF2's output halves are
# computationally independent, so it cannot yield the encryption key) and
# the prompts, ENCRYPTED under the enc half, which is NEVER stored - the
# browser delivers it at login, wrapped, and it lives in RAM only. Nothing
# on this disk can decrypt anything. Conversations and images are never
# persisted anywhere - see the module docstring.
CONFIG_DIR = Path(os.environ.get("IMAGEGEN_CONFIG_DIR", "/var/lib/imagegen"))
KAUTH_FILE = CONFIG_DIR / "k_auth"
LEGACY_PASSWORD_FILE = CONFIG_DIR / "password"
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
MIN_PASSWORD_LEN = 8  # enforced client-side; the server never sees the password

IMG_STEPS = 24  # DPM++ 2M Karras converges well here (was 32 w/ default scheduler)
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
# K_AUTH (stored on disk) can only verify login proofs. K_ENC exists ONLY in
# this process's memory, and only after a successful login has delivered it:
# the browser derives both halves from the password, proves knowledge of
# K_AUTH against the challenge, and sends K_ENC wrapped under
# HMAC(K_AUTH, "wrap-enc-key" || nonce) - Cloudflare relays ciphertext it
# can never unwrap, and a fresh process cannot decrypt the prompts until
# someone who knows the password logs in.
_config_lock = threading.Lock()
K_AUTH = b""
K_ENC = b""
_aesgcm = None
_prompts = None  # {"system_prompt": str, "image_prompt_prefix": str} once unlocked


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
    """Load (or create) the login verifier. The encryption key is deliberately
    NOT recoverable here: a fresh process starts locked, and stays locked until
    a browser that knows the password logs in and delivers the enc key."""
    global K_AUTH, K_ENC, _aesgcm, _prompts
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if KAUTH_FILE.exists():
        K_AUTH = base64.b64decode(KAUTH_FILE.read_text().strip())
    else:
        # First start on this scheme. Derive the verifier from the legacy
        # plaintext password file (upgrade path) or IMAGEGEN_PASSWORD (fresh
        # install), store ONLY the auth half, and destroy the plaintext - the
        # enc half is discarded; prompts.enc stays valid because the same
        # password re-derives the same enc key at login.
        if LEGACY_PASSWORD_FILE.exists():
            password = LEGACY_PASSWORD_FILE.read_text().strip()
        else:
            password = os.environ.get("IMAGEGEN_PASSWORD", "").strip()
        if not password:
            raise RuntimeError("no k_auth file, no legacy password file, IMAGEGEN_PASSWORD unset")
        K_AUTH, _discard = _derive_keys(password)
        KAUTH_FILE.write_text(base64.b64encode(K_AUTH).decode())
        os.chmod(KAUTH_FILE, 0o600)
        LEGACY_PASSWORD_FILE.unlink(missing_ok=True)
    K_ENC = b""
    _aesgcm = None
    _prompts = None


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


# Envelopes are encrypted under the PER-SESSION key (password + ephemeral
# ECDH), set into this contextvar by _require_session for the current request
# task. Forward secrecy: the session key dies with the session on both ends,
# so recorded traffic is permanently undecryptable - even with the password.
_current_aesgcm: contextvars.ContextVar = contextvars.ContextVar("session_aesgcm", default=None)


def _encrypt(obj, aesgcm=None) -> dict:
    aesgcm = aesgcm or _current_aesgcm.get() or _aesgcm
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, json.dumps(obj).encode(), None)
    return {"nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(ciphertext).decode()}


def _decrypt(envelope: dict, aesgcm=None):
    aesgcm = aesgcm or _current_aesgcm.get() or _aesgcm
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


class SecurityHeadersMiddleware:
    """Pure-ASGI header injector. Deliberately NOT @app.middleware('http')
    (Starlette's BaseHTTPMiddleware), which buffers the whole response body and
    would break token streaming - it injects headers at response-start and
    passes the streamed body chunks straight through."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                for k, v in SECURITY_HEADERS.items():
                    headers.append((k.encode(), v.encode()))
            await send(message)

        await self.app(scope, receive, send_wrapper)


app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/challenge")
async def challenge():
    nonce = os.urandom(16)
    nonce_b64 = base64.b64encode(nonce).decode()
    # Fresh ephemeral ECDH pair per challenge - the private half lives only in
    # this dict entry and dies with the challenge/login. Mixed into the session
    # key, it gives every session forward secrecy.
    eph_priv = ec.generate_private_key(ec.SECP256R1())
    server_pub = eph_priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    now = time.time()
    with _state_lock:
        # opportunistic sweep of expired challenges and idle sessions - a
        # session's own idle check only runs when THAT token is reused, so
        # without this, sessions nobody ever revisits (e.g. every page
        # reload starts a fresh login here, by design - see app.js) would
        # sit in memory, galleries and all, until the process restarts.
        for k in [k for k, ch in _challenges.items() if ch["exp"] < now]:
            del _challenges[k]
        for tok in [tok for tok, s in _sessions.items() if now - s["last_seen"] > SESSION_IDLE_TIMEOUT]:
            del _sessions[tok]
        _challenges[nonce_b64] = {"exp": now + CHALLENGE_TTL, "priv": eph_priv}
    return {"nonce": nonce_b64, "server_pub": base64.b64encode(server_pub).decode()}


@app.post("/api/login")
async def login(request: Request):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return JSONResponse({"error": "too many attempts"}, status_code=429)

    body = await request.json()
    nonce_b64 = body.get("nonce", "")
    proof_b64 = body.get("proof", "")

    with _state_lock:
        ch = _challenges.pop(nonce_b64, None)
    if ch is None or ch["exp"] < time.time():
        _record_failure(ip)
        return JSONResponse({"error": "expired or invalid challenge"}, status_code=401)

    nonce = base64.b64decode(nonce_b64)
    expected = hmac.new(K_AUTH, nonce, hashlib.sha256).digest()
    given = base64.b64decode(proof_b64) if proof_b64 else b""
    if not hmac.compare_digest(expected, given):
        _record_failure(ip)
        return JSONResponse({"error": "incorrect password"}, status_code=401)

    # Session key = HMAC(K_AUTH, nonce || ECDH-shared). The DH share gives
    # forward secrecy (both ephemeral privates die with the session; recorded
    # traffic is undecryptable forever, password or not); mixing K_AUTH
    # authenticates the DH so a man-in-the-middle relay without the password
    # can't sit between the two ends. The browser's enc key arrives wrapped
    # under this session key, and login is only complete if that key actually
    # test-decrypts the prompt store - a stolen verifier can't fake that.
    global K_ENC, _aesgcm, _prompts
    try:
        client_pub = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), base64.b64decode(body.get("client_pub", ""))
        )
        shared = ch["priv"].exchange(ec.ECDH(), client_pub)
        k_sess = hmac.new(K_AUTH, b"session-v1" + nonce + shared, hashlib.sha256).digest()
        sess_aesgcm = AESGCM(k_sess)
        ek = sess_aesgcm.decrypt(
            base64.b64decode(body.get("ek_iv", "")),
            base64.b64decode(body.get("ek_ct", "")),
            None,
        )
        if len(ek) != 32:
            raise ValueError("bad key length")
    except Exception:
        _record_failure(ip)
        return JSONResponse({"error": "incorrect password"}, status_code=401)

    candidate = AESGCM(ek)
    with _config_lock:
        if PROMPTS_FILE.exists():
            try:
                raw = PROMPTS_FILE.read_bytes()
                prompts = json.loads(candidate.decrypt(raw[:12], raw[12:], None))
            except Exception:
                _record_failure(ip)
                return JSONResponse({"error": "incorrect password"}, status_code=401)
            K_ENC, _aesgcm, _prompts = ek, candidate, prompts
        else:
            # Very first login ever: seed the prompt store under this key.
            K_ENC, _aesgcm = ek, candidate
            _prompts = {
                "system_prompt": DEFAULT_SYSTEM_PROMPT,
                "image_prompt_prefix": DEFAULT_APPEARANCE,
            }
            _write_prompts_locked()

    token = base64.urlsafe_b64encode(os.urandom(32)).decode()
    with _state_lock:
        _sessions[token] = {"last_seen": time.time(), "aesgcm": sess_aesgcm,
                            **_new_conversation_state()}

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
    # Route this request's envelopes through the session's own key.
    _current_aesgcm.set(state.get("aesgcm"))
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
    # Request and ack ride THIS session's envelope key (which survives until
    # the session-clear below). The password itself never arrives: the browser
    # derives the new key pair and sends the halves (length policy is enforced
    # client-side; the server can't see length).
    old_aesgcm = _current_aesgcm.get() or _aesgcm
    body = _decrypt(await request.json(), aesgcm=old_aesgcm)
    try:
        new_k_auth = base64.b64decode(body.get("new_k_auth", ""))
        new_k_enc = base64.b64decode(body.get("new_k_enc", ""))
        if len(new_k_auth) != 32 or len(new_k_enc) != 32:
            raise ValueError("bad key length")
    except Exception:
        return JSONResponse(_encrypt({"error": "malformed key material"}, old_aesgcm))
    with _config_lock:
        K_AUTH = new_k_auth
        KAUTH_FILE.write_text(base64.b64encode(K_AUTH).decode())
        os.chmod(KAUTH_FILE, 0o600)
        K_ENC = new_k_enc
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
    """Non-streaming generation. llama.cpp's chat handler cleanly returns the
    final answer for this model, parsing out its internal 'thinking' channel.
    (Token streaming was tried but exposed that model's raw <|channel|> control
    tokens - which loop on some inputs - so it was reverted; re-enabling it
    needs channel-aware stream parsing specific to this checkpoint.)

    Sampling is set explicitly: llama.cpp's default temperature (0.2) is too
    greedy for this abliterated model and drives it into repetition loops
    (it latches onto a phrase and repeats forever). A higher temperature plus a
    repetition penalty keeps replies varied and loop-free."""
    messages = [{"role": "system", "content": _prompts["system_prompt"]}, *history]
    with _llm_lock:
        completion = llm.create_chat_completion(
            messages=messages,
            max_tokens=LLM_MAX_TOKENS,
            temperature=0.75,
            top_p=0.9,
            repeat_penalty=1.18,
        )
    return completion["choices"][0]["message"]["content"].strip()


def _build_image_prompt_from_message(message: str) -> str:
    """Image prompt from what the USER just said (no LLM call), so image
    generation can start immediately and run on the GPU in parallel with the
    reply on the CPU. No LLM here on purpose - an LLM call would contend with
    the reply on the single LLM thread and re-serialize the two. Lead with the
    configured look, then the user's line as the scene; cap under CLIP's limit."""
    appearance = _prompts["image_prompt_prefix"]
    scene = message.strip().replace("\n", " ")[:180]
    return f"{appearance}. {scene}"[:240]


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


def _run_image_job(job_id: str, prompt: str, mode_state: dict):
    """Generate an image and store it into mode_state['jobs'][job_id] + gallery.
    Runs on the GPU executor; the client polls /api/image-status for it."""
    jobs = mode_state["jobs"]
    try:
        image_b64 = _run_image(prompt)
        with _state_lock:
            jobs[job_id] = {"status": "done", "image": image_b64}
            gallery = mode_state["gallery"]
            gallery.append(image_b64)
            del gallery[:-MAX_GALLERY]
    except Exception:
        with _state_lock:
            jobs[job_id] = {"status": "error", "image": None}


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

    # Kick the image off the USER's message FIRST, so it renders on the GPU in
    # PARALLEL with the reply generating on the CPU (different hardware, genuine
    # overlap). The image reflects what the user said - the reply doesn't exist
    # yet when the image starts, which is the inherent cost of parallelism. The
    # client polls /api/image-status on the returned job_id.
    job_id = secrets.token_urlsafe(8)
    jobs = mode_state["jobs"]
    jobs[job_id] = {"status": "pending", "image": None}
    for old_id in list(jobs)[:-10]:
        del jobs[old_id]
    image_prompt = _build_image_prompt_from_message(message)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(_gpu_executor, _run_image_job, job_id, image_prompt, mode_state)

    reply = await loop.run_in_executor(_llm_executor, _run_llm, history[-MAX_HISTORY:])
    history.append({"role": "assistant", "content": reply})
    del history[:-MAX_HISTORY]
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

    try:
        image_b64 = await asyncio.get_event_loop().run_in_executor(_gpu_executor, _run_image, prompt)
    except RuntimeError:
        # Almost always CUDA OOM - the nightly Captain's Log transcription
        # holds ~4GB of the 6GB card while it runs.
        return JSONResponse(
            {"error": "GPU is busy (likely the nightly Captain's Log run) - try again in ~30 minutes"},
            status_code=503,
        )
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
        # Speed, no model change: DPM++ 2M Karras converges in fewer steps than
        # the default scheduler, and TAESD (a tiny distilled VAE) decodes far
        # faster than the full SDXL VAE and removes the VAE-decode VRAM spike
        # that used to flirt with OOM on the 6GB card. Set the VAE BEFORE
        # enable_model_cpu_offload so the offload hooks attach to it.
        image_pipe.scheduler = DPMSolverMultistepScheduler.from_config(
            image_pipe.scheduler.config, use_karras_sigmas=True, algorithm_type="dpmsolver++"
        )
        image_pipe.vae = AutoencoderTiny.from_pretrained("madebyollin/taesdxl", torch_dtype=torch.float16)
        image_pipe.enable_model_cpu_offload()
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


def _unload_models():
    """Free the GPU (and the chat model's RAM) without killing anything user-
    visible: sessions, conversations, and galleries live in _sessions, not in
    the model objects, so everything picks up where it left off after the next
    Initialize. Runs on _gpu_executor so teardown happens on the same thread
    (and CUDA context) that loaded the models."""
    global image_pipe, llm, _model_status
    with _gpu_lock:
        image_pipe = None
        llm = None
        gc.collect()
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    with _model_status_lock:
        _model_status = "cold"
    print("models unloaded, GPU released", flush=True)


@app.post("/api/unload")
async def unload(request: Request):
    result = _require_session(request)
    if isinstance(result, JSONResponse):
        return result
    with _model_status_lock:
        busy = _model_status == "loading"
    if not busy and _model_status != "cold":
        await asyncio.get_event_loop().run_in_executor(_gpu_executor, _unload_models)
    return JSONResponse(_encrypt({"status": _model_status}))


@app.post("/api/release-gpu")
async def release_gpu(request: Request):
    # Internal coordination endpoint for the nightly Captain's Log pipeline:
    # it asks Chloe to vacate the GPU before transcription. Requests proxied
    # through the Cloudflare tunnel always carry CF-Connecting-IP (Cloudflare
    # overwrites any client-supplied value), so its absence == a genuinely
    # local caller. No session, no envelope - this endpoint holds no data.
    if request.headers.get("cf-connecting-ip"):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    with _model_status_lock:
        status = _model_status
    released = False
    if status == "ready" or status == "error":
        await asyncio.get_event_loop().run_in_executor(_gpu_executor, _unload_models)
        released = True
    with _model_status_lock:
        status = _model_status
    return JSONResponse({"status": status, "released": released})


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
