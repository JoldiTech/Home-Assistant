#!/usr/bin/env python3
"""
Duologue — a multi-persona chat orchestrator for the AI box.

One local llama.cpp model instance serves N named personas. You give each a
name and a personality in the settings page; when you send a message every
persona replies, round-robin, and each one sees the others' replies in the
same turn — so they respond to you *and* to each other. If you address someone
by name ("Ada, what do you think?"), that persona knows it was spoken to and
answers first.

Why one model instance is safe: a chat LLM is stateless between calls. Identity
and memory live entirely in the messages we build per call. We keep a single
neutral, speaker-labeled transcript and *render it per persona* at call time,
with a strong per-persona system prompt and stop sequences on the other
speakers' name tags. The model is called one persona at a time (a single
llama.cpp context is not safe to decode in parallel). So the personas never
cross-talk unless this orchestrator lets them — and it doesn't.

Stdlib only + llama_cpp. Run it with the box's imagegen-env interpreter:

    CUDA_VISIBLE_DEVICES="" ~/imagegen-env/bin/python duologue.py

Binds to 127.0.0.1:8477 by default (localhost only — reach it over an SSH
tunnel). Nothing here is exposed publicly.
"""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- paths / config ----------------------------------------------------------

HERE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DUOLOGUE_DATA", str(Path.home() / "duologue")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_PATH = DATA_DIR / "state.json"

HOST = os.environ.get("DUOLOGUE_HOST", "127.0.0.1")
PORT = int(os.environ.get("DUOLOGUE_PORT", "8477"))

# Where the box keeps its GGUF chat models (both dirs are scanned for the picker).
MODEL_DIRS = [
    Path.home() / "transcribe" / "models",
    Path.home() / "imagegen" / "models",
]
DEFAULT_MODEL = str(Path.home() / "transcribe" / "models" / "Qwen2.5-7B-Instruct-Q4_K_M.gguf")

# How many trailing transcript messages to feed the model (bounds prompt size).
HISTORY_WINDOW = 30

DEFAULT_STATE = {
    "config": {
        "user_name": "David",
        "model_path": DEFAULT_MODEL,
        "n_ctx": 4096,
        "n_threads": max(1, (os.cpu_count() or 4) // 2),
        "temperature": 0.8,
        "max_tokens": 320,
        "personas": [
            {
                "name": "Ada",
                "personality": (
                    "You are analytical, precise, and a little skeptical. You like to "
                    "pressure-test ideas, cite trade-offs, and ask sharp clarifying "
                    "questions. Dry wit, never rude."
                ),
            },
            {
                "name": "Bo",
                "personality": (
                    "You are warm, imaginative, and optimistic. You riff on ideas, "
                    "offer creative angles, and keep the energy up. You gently push "
                    "back on Ada when she's too gloomy."
                ),
            },
        ],
    },
    "transcript": [],   # [{"speaker": name, "text": str}]
    "turn_index": 0,
}


# --- persistence -------------------------------------------------------------

_state_lock = threading.Lock()


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            s = json.loads(STATE_PATH.read_text())
            # fill any missing keys from defaults (forward-compatible)
            base = json.loads(json.dumps(DEFAULT_STATE))
            base["config"].update(s.get("config", {}))
            base["transcript"] = s.get("transcript", [])
            base["turn_index"] = s.get("turn_index", 0)
            return base
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_PATH)


STATE = load_state()


def list_models() -> list[str]:
    found = []
    for d in MODEL_DIRS:
        if d.is_dir():
            found += [str(p) for p in sorted(d.glob("*.gguf"))]
    # make sure the currently-selected one is always in the list
    cur = STATE["config"].get("model_path")
    if cur and cur not in found:
        found.insert(0, cur)
    return found


# --- model manager (single instance, serialized) -----------------------------

class ModelManager:
    """Lazily loads one llama.cpp model and serializes all generate calls.

    A single llama.cpp context is not safe to decode concurrently, so every
    persona turn runs under this lock — which is exactly what we want for a
    turn-taking conversation anyway.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._llm = None
        self._loaded_path = None
        self._loaded_ctx = None

    def _ensure(self, model_path: str, n_ctx: int, n_threads: int):
        if self._llm is not None and self._loaded_path == model_path and self._loaded_ctx == n_ctx:
            return self._llm
        # (re)load
        from llama_cpp import Llama  # imported lazily so the server starts fast
        if self._llm is not None:
            try:
                self._llm.close()
            except Exception:
                pass
            self._llm = None
        self._llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_gpu_layers=0,          # CPU only — leave the GPU for Chloe's imagegen
            verbose=False,
        )
        self._loaded_path = model_path
        self._loaded_ctx = n_ctx
        return self._llm

    def generate(self, messages: list[dict], *, model_path: str, n_ctx: int,
                 n_threads: int, temperature: float, max_tokens: int,
                 stop: list[str]) -> str:
        with self._lock:
            llm = self._ensure(model_path, n_ctx, n_threads)
            out = llm.create_chat_completion(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop or None,
            )
        return (out["choices"][0]["message"]["content"] or "").strip()


MODEL = ModelManager()


# --- orchestration -----------------------------------------------------------

def addressed_personas(text: str, personas: list[dict]) -> list[str]:
    """Names explicitly addressed in the user's message (word-boundary match)."""
    hits = []
    for p in personas:
        name = p["name"].strip()
        if not name:
            continue
        if re.search(rf"(^|[^\w]){re.escape(name)}([^\w]|$)", text, re.IGNORECASE):
            hits.append(name)
    return hits


def build_messages(persona: dict, state: dict, addressed: list[str]) -> tuple[list[dict], list[str]]:
    """Construct one persona's private view: strong identity system prompt +
    the shared transcript rendered as a single labeled user turn. Returns
    (messages, stop_sequences)."""
    cfg = state["config"]
    user_name = cfg["user_name"].strip() or "the user"
    me = persona["name"].strip()
    others = [user_name] + [p["name"].strip() for p in cfg["personas"] if p["name"].strip() != me]

    if me in addressed:
        addr_note = f"{user_name} is speaking directly to you right now. Respond to them."
    elif addressed:
        who = " and ".join(addressed)
        addr_note = (f"{user_name} is speaking to {who} right now, not you. Chime in briefly "
                     f"as {me} only if you have something worth adding; otherwise keep it short.")
    else:
        addr_note = f"{user_name} is addressing the whole group. Respond as {me}."

    system = (
        f"You are {me}.\n{persona['personality'].strip()}\n\n"
        f"You are one participant in a group chat. The others are: {', '.join(others)}.\n"
        "Rules:\n"
        f"- Speak ONLY as {me}. Write a single reply in your own voice.\n"
        f"- Never write words, actions, or narration for {user_name} or the other assistants.\n"
        f"- Do not prefix your reply with your name or a label — just say what {me} would say.\n"
        "- Keep replies conversational and reasonably brief.\n"
        f"{addr_note}"
    )

    # Render the (windowed) transcript as one labeled block.
    msgs = state["transcript"][-HISTORY_WINDOW:]
    lines = [f"{m['speaker']}: {m['text']}" for m in msgs]
    convo = "\n".join(lines) if lines else "(the conversation is just beginning)"
    user_block = (
        "Here is the conversation so far:\n\n"
        f"{convo}\n\n"
        f"Now write {me}'s next reply."
    )

    # Stop as soon as the model tries to speak for someone else, or labels itself.
    stop = []
    for nm in others + [me]:
        stop.append(f"\n{nm}:")
    stop.append(f"{me}:")

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_block},
    ], stop


def clean_reply(text: str, persona_name: str, all_names: list[str]) -> str:
    """Strip a leaked leading self-label and anything after a stray speaker tag."""
    text = text.strip()
    # drop a leading "Name:" the model may have added despite instructions
    text = re.sub(rf"^\s*{re.escape(persona_name)}\s*:\s*", "", text, flags=re.IGNORECASE)
    # if it started narrating another speaker's turn, cut it off there
    for nm in all_names:
        if nm == persona_name:
            continue
        m = re.search(rf"\n{re.escape(nm)}\s*:", text)
        if m:
            text = text[: m.start()].rstrip()
    return text.strip()


def run_turn(user_text: str) -> dict:
    """Append the user's message, then have every persona reply round-robin."""
    with _state_lock:
        cfg = STATE["config"]
        personas = [p for p in cfg["personas"] if p["name"].strip()]
        user_name = cfg["user_name"].strip() or "You"
        STATE["transcript"].append({"speaker": user_name, "text": user_text})

        addressed = addressed_personas(user_text, personas)
        all_names = [user_name] + [p["name"].strip() for p in personas]

        # Order: whoever was addressed leads; otherwise rotate for fairness.
        n = len(personas)
        rot = STATE["turn_index"] % n if n else 0
        order = personas[rot:] + personas[:rot]
        if addressed:
            order.sort(key=lambda p: 0 if p["name"].strip() in addressed else 1)

        new_msgs = []
        for persona in order:
            messages, stop = build_messages(persona, STATE, addressed)
            try:
                raw = MODEL.generate(
                    messages,
                    model_path=cfg["model_path"],
                    n_ctx=int(cfg["n_ctx"]),
                    n_threads=int(cfg["n_threads"]),
                    temperature=float(cfg["temperature"]),
                    max_tokens=int(cfg["max_tokens"]),
                    stop=stop,
                )
                reply = clean_reply(raw, persona["name"].strip(), all_names) or "…"
            except Exception as e:  # surface model errors in-chat rather than 500
                reply = f"[error generating reply: {e}]"
            msg = {"speaker": persona["name"].strip(), "text": reply}
            STATE["transcript"].append(msg)   # next persona in this round sees it
            new_msgs.append(msg)

        STATE["turn_index"] += 1
        save_state(STATE)
        return {"messages": new_msgs, "transcript": STATE["transcript"]}


# --- HTTP --------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "duologue/1.0"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html = (HERE / "index.html").read_bytes()
            return self._send(200, html, "text/html; charset=utf-8")
        if self.path == "/api/state":
            with _state_lock:
                return self._json({
                    "config": STATE["config"],
                    "transcript": STATE["transcript"],
                    "models": list_models(),
                })
        return self._send(404, b"not found", "text/plain")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        try:
            if self.path == "/api/config":
                body = self._read_json()
                with _state_lock:
                    cfg = STATE["config"]
                    for k in ("user_name", "model_path"):
                        if k in body:
                            cfg[k] = str(body[k])
                    for k in ("n_ctx", "n_threads", "max_tokens"):
                        if k in body:
                            cfg[k] = int(body[k])
                    if "temperature" in body:
                        cfg["temperature"] = float(body["temperature"])
                    if "personas" in body and isinstance(body["personas"], list):
                        cfg["personas"] = [
                            {"name": str(p.get("name", "")).strip(),
                             "personality": str(p.get("personality", "")).strip()}
                            for p in body["personas"] if str(p.get("name", "")).strip()
                        ]
                    save_state(STATE)
                return self._json({"ok": True, "config": STATE["config"]})

            if self.path == "/api/say":
                body = self._read_json()
                text = str(body.get("text", "")).strip()
                if not text:
                    return self._json({"error": "empty message"}, 400)
                return self._json(run_turn(text))

            if self.path == "/api/reset":
                with _state_lock:
                    STATE["transcript"] = []
                    STATE["turn_index"] = 0
                    save_state(STATE)
                return self._json({"ok": True})

            return self._send(404, b"not found", "text/plain")
        except Exception as e:
            return self._json({"error": str(e)}, 500)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"duologue listening on http://{HOST}:{PORT}  (data: {DATA_DIR})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
