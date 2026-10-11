"""Exercise imagegen/app.py's real request handling with the ML stack stubbed.

Checks the stateless design: the server keeps no conversation, a tab's close
beacon ends only its own session, the prompt-store key is wiped when the last
session ends, and chat images wait server-side only as ciphertext.
"""
import base64, hashlib, hmac, json, os, sys, tempfile, time, types

# --- stub the heavy ML modules before app.py imports them -------------------
def stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

class _Any:
    def __init__(self, *a, **k): pass
    def __call__(self, *a, **k): return _Any()
    def __getattr__(self, n): return _Any()

torch = stub("torch", float16="f16", cuda=_Any(), from_numpy=_Any(), stack=_Any(), cat=_Any(), zeros_like=_Any())
stub("diffusers", AutoencoderTiny=_Any, DPMSolverMultistepScheduler=_Any, StableDiffusionXLPipeline=_Any,
     utils=_Any())
stub("diffusers.image_processor", IPAdapterMaskProcessor=_Any)
pil = stub("PIL"); stub("PIL.Image", open=_Any()); pil.Image = sys.modules["PIL.Image"]

class FakeLlama:
    seen = []  # every messages list the model was shown
    def __init__(self, *a, **k): pass
    def create_chat_completion(self, messages, stream=False, **k):
        FakeLlama.seen.append(messages)
        reply = "reply#%d" % len(FakeLlama.seen)
        if stream:
            return iter([{"choices": [{"delta": {"content": reply[:3]}}]},
                         {"choices": [{"delta": {"content": reply[3:]}}]}])
        return {"choices": [{"message": {"content": "a scene"}}]}
    def close(self): pass
stub("llama_cpp", Llama=FakeLlama)

cfg = tempfile.mkdtemp()
os.environ["IMAGEGEN_CONFIG_DIR"] = cfg
os.environ["IMAGEGEN_PASSWORD"] = "correct horse battery"
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import app as A  # noqa: E402

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

# Pretend models are loaded; image generation returns a fixed PNG-ish payload.
A.llm = FakeLlama()
A.image_pipe = object()
A._model_status = "ready"
A._run_image = lambda prompt, *a, **k: base64.b64encode(b"PNG:" + prompt.encode()).decode()

PW = "correct horse battery"


class Tab:
    """One browser tab: its own key, sharing the client's cookie jar."""
    def __init__(self, client):
        self.c = client
        d = hashlib.pbkdf2_hmac("sha256", PW.encode(), b"imagegen-e2e-v1", 210000, dklen=64)
        auth, enc = d[:32], d[32:]
        ch = self.c.post("/api/challenge").json()
        nonce = base64.b64decode(ch["nonce"])
        priv = ec.generate_private_key(ec.SECP256R1())
        spub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(ch["server_pub"]))
        shared = priv.exchange(ec.ECDH(), spub)
        self.k = AESGCM(hmac.new(auth, b"session-v1" + nonce + shared, hashlib.sha256).digest())
        iv = os.urandom(12)
        r = self.c.post("/api/login", json={
            "nonce": ch["nonce"], "proof": base64.b64encode(hmac.new(auth, nonce, hashlib.sha256).digest()).decode(),
            "client_pub": base64.b64encode(priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)).decode(),
            "ek_iv": base64.b64encode(iv).decode(), "ek_ct": base64.b64encode(self.k.encrypt(iv, enc, None)).decode()})
        assert r.status_code == 200, r.text
        self.logout_env = json.dumps(self.env({"logout": True}))

    def env(self, obj):
        n = os.urandom(12)
        return {"nonce": base64.b64encode(n).decode(),
                "ciphertext": base64.b64encode(self.k.encrypt(n, json.dumps(obj).encode(), None)).decode()}

    def dec(self, e):
        return json.loads(self.k.decrypt(base64.b64decode(e["nonce"]), base64.b64decode(e["ciphertext"]), None))

    def call(self, path, obj):
        r = self.c.post(path, json=self.env(obj))
        return r.status_code, (self.dec(r.json()) if r.status_code == 200 else r.json())

    def chat(self, obj):
        r = self.c.post("/api/chat", json=self.env(obj))
        assert r.status_code == 200, r.text
        return [self.dec(json.loads(l)) for l in r.text.splitlines() if l.strip()]

    def close(self):
        return self.c.post("/api/logout", content=self.logout_env, headers={"Content-Type": "text/plain"})


def no_content_on_server(*needles):
    """Search every session dict on the server for conversation text."""
    blob = repr({t: {k: v for k, v in s.items() if k != "aesgcm"} for t, s in A._sessions.items()})
    for n in needles:
        assert n not in blob, f"server session state holds {n!r}"


ok = lambda m: print("PASS", m)

with TestClient(A.app, base_url="https://testserver") as client:
    t1 = Tab(client)
    assert A._prompts is not None and A.K_ENC, "login should unlock the prompt store"
    ok("login unlocks the prompt store")

    # Turn 1: no history.
    ev = t1.chat({"message": "secret-message-one", "history": [], "turn": 0})
    finals = [e["final"] for e in ev if "final" in e]
    assert finals == ["reply#1"] and ev[-1] == {"done": True}, ev
    shown = FakeLlama.seen[-1]
    assert shown[0]["role"] == "system" and shown[-1] == {"role": "user", "content": "secret-message-one"}
    no_content_on_server("secret-message-one", "reply#1")
    ok("chat turn: model sees the message, reply comes back as {final}, server keeps neither")

    # Turn 2: the browser supplies history; the model must see all of it.
    hist = [{"role": "user", "content": "secret-message-one"}, {"role": "assistant", "content": "reply#1"}]
    t1.chat({"message": "secret-two", "history": hist, "turn": 1})
    assert [m["content"] for m in FakeLlama.seen[-1][1:]] == ["secret-message-one", "reply#1", "secret-two"]
    no_content_on_server("secret-two")
    ok("turn 2: model conditions on the browser-sent history")

    # Client-sent history is validated: no system role injection, size bounds.
    t1.chat({"message": "x", "history": [{"role": "system", "content": "evil"}, "junk",
                                         {"role": "user", "content": "a" * 20000}], "turn": 2})
    shown = FakeLlama.seen[-1]
    assert [m["role"] for m in shown] == ["system", "user", "user"], shown
    assert shown[0]["content"] != "evil" and len(shown[1]["content"]) == A.MAX_MESSAGE_CHARS
    ok("client history: system role dropped, junk dropped, messages capped")

    # Chat image: waits server-side ONLY as an envelope, delivered once.
    st, r = t1.call("/api/chat-image", {"history": hist})
    assert st == 200, r
    job = r["job_id"]
    deadline = time.time() + 5
    while time.time() < deadline:
        st, r = t1.call("/api/image-status", {"job_id": job})
        if r["status"] != "pending":
            break
        stored = next(iter(A._sessions.values()))["jobs"].get(job)
        if stored is not None:  # finished, not yet fetched: must be ciphertext
            assert set(stored) == {"nonce", "ciphertext"}, stored
        time.sleep(0.05)
    assert r["status"] == "done" and base64.b64decode(r["image"]).startswith(b"PNG:"), r
    assert job not in next(iter(A._sessions.values()))["jobs"]
    st, r = t1.call("/api/image-status", {"job_id": job})
    assert r["status"] == "error", "a delivered image must be gone"
    ok("chat image: stored encrypted, deleted on delivery, second fetch finds nothing")

    # Image-only: nothing kept.
    st, r = t1.call("/api/image", {"prompt": "secret-picture"})
    assert st == 200 and base64.b64decode(r["image"]) == b"PNG:secret-picture"
    no_content_on_server("secret-picture", r["image"])
    ok("image-only: returned, not kept")

    # Prompt assist: the model's style fragments are appended; the user's
    # prompt is never cut, however long.
    long_prompt = "secret-" + "x" * 900
    st, r = t1.call("/api/image", {"prompt": long_prompt, "assist": True})
    assert st == 200, r
    rendered = base64.b64decode(r["image"]).decode()[len("PNG:"):]
    assert rendered == long_prompt + ", a scene", rendered[-40:]
    assert r["used_prompt"] == rendered
    assert FakeLlama.seen[-1][0]["content"] == A.PROMPT_ASSIST_SYSTEM
    assert FakeLlama.seen[-1][1]["content"] == long_prompt
    no_content_on_server("secret-xxxx")
    ok("prompt assist: appends style, keeps the whole user prompt")

    # Removed endpoints are gone.
    for p in ("/api/reset", "/api/chat-edit", "/api/chat-delete"):
        assert client.post(p, json=t1.env({})).status_code in (404, 405), p
    ok("reset/chat-edit/chat-delete endpoints removed")

    # A second tab logs in: the cookie now belongs to tab 2.
    t2 = Tab(client)
    assert len(A._sessions) == 2
    # Closing the STALE tab 1 must not end tab 2's session.
    t1.close()
    assert len(A._sessions) == 2, "stale tab's beacon ended someone else's session"
    st, _ = t2.call("/api/init-status", {})
    assert st == 200
    ok("closing a stale tab does not log out the newer tab")

    # Garbage / missing beacon bodies are harmless.
    assert client.post("/api/logout", content=b"not json").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert len(A._sessions) == 2
    ok("malformed logout bodies are ignored")

    # Closing tab 2 ends its session immediately.
    t2.close()
    assert len(A._sessions) == 1
    st, _ = t2.call("/api/init-status", {})
    assert st == 401
    assert A.K_ENC, "tab 1's session still exists, key must stay"
    ok("closing a tab ends its session at once (401 afterwards)")

    # Reaper: the last (orphaned) session idles out -> keys wiped.
    for s in A._sessions.values():
        s["last_seen"] -= A.SESSION_IDLE_TIMEOUT + 1
    with A._state_lock:
        A._sweep_expired_locked(time.time())
    assert not A._sessions and A.K_ENC == b"" and A._aesgcm is None and A._prompts is None
    ok("last session gone -> prompt-store key and prompts wiped from memory")

    # A fresh login re-unlocks the (encrypted, on-disk) prompt store.
    t3 = Tab(client)
    assert A._prompts and A._prompts["system_prompt"]
    st, r = t3.call("/api/get-prompts", {})
    assert st == 200 and r["system_prompt"]
    ok("next login re-unlocks the prompt store from disk")

    # A pending image job whose session ends discards its result.
    jobs = A._sessions[next(iter(A._sessions))]["jobs"]
    jobs["late"] = None
    t3.close()
    A._run_image_job("late", "p", jobs, A.AESGCM(os.urandom(32)))
    assert "late" not in jobs
    ok("image finishing after logout is discarded")

    # Group mode: personas see each other's replies within the request; turn rotates lead.
    t4 = Tab(client)
    with A._config_lock:
        A._prompts.update(group_mode=True, personas=[{"name": "Ada", "personality": ""},
                                                       {"name": "Bo", "personality": ""}])
    ev = t4.chat({"message": "hi all", "history": [], "turn": 1})
    finals = [(e["speaker"], e["final"]) for e in ev if "final" in e]
    assert [s for s, _ in finals] == ["Bo", "Ada"], finals   # turn 1 -> Bo leads
    assert finals[0][1] in FakeLlama.seen[-1][1]["content"], "second persona should see the first reply"
    no_content_on_server("hi all")
    st, r = t4.call("/api/state", {})
    assert r == {"group_mode": True}
    ok("group chat: lead rotates by client turn, personas see prior replies, server keeps none")

    # "Shut down models": unload, scrub VRAM, then end the process (here
    # intercepted) only AFTER the answer went out; the answer names this
    # process's boot id so the browser can wait for a new one.
    terminated = []
    A._terminate = lambda: terminated.append(True)
    st, r = t4.call("/api/unload", {})
    assert st == 200 and r["restarting"] is True and r["boot"] == A.BOOT_ID, r
    assert r["status"] == "cold" and A.llm is None
    assert terminated == [True], "the process must exit to wipe its memory"
    assert client.get("/").headers["X-Chloe-Boot"] == A.BOOT_ID
    ok("unload: models dropped, response sent, then the process exits for a clean restart")

# Per-model modes: a model too big to sit beside SDXL is GPU-chat only, a
# deleted default falls back to a model that exists, and a request for a mode
# the model can't run is overridden.
mdir = tempfile.mkdtemp()
A.GGUF_DIR = __import__("pathlib").Path(mdir)
for name, gb in (("Small-12B.Q4_K_M.gguf", 7.5), ("Big-26B-A4B.Q4_K_M.gguf", 16.8)):
    with open(os.path.join(mdir, name), "wb") as f:
        f.truncate(int(gb * 1e9))  # sparse: size without the disk
assert A._chat_model_modes("Small-12B.Q4_K_M.gguf") == ["cpu_images", "gpu_chat"]
assert A._chat_model_modes("Big-26B-A4B.Q4_K_M.gguf") == ["gpu_chat"]
assert A._chat_model_modes("G4-MeroMero-26B-A4B.Q4_K_M.gguf") == ["gpu_chat"]  # profile
A._chat_selection.update(model="Deleted.gguf", mode="cpu_images")
A._settle_chat_selection()
assert A._chat_selection == {"model": "Big-26B-A4B.Q4_K_M.gguf", "mode": "gpu_chat"}, A._chat_selection
A._chat_selection.update(model="Small-12B.Q4_K_M.gguf", mode="cpu_images")
A._settle_chat_selection()
assert A._chat_selection["mode"] == "cpu_images"
A._chat_selection["model"] = "G4-MeroMero-26B-A4B.Q4_K_M.gguf"
assert A._gpu_layer_chain() == [8], A._gpu_layer_chain()
A._model_status = "cold"
p = A._init_payload()
assert p["model_modes"] == {"Big-26B-A4B.Q4_K_M.gguf": ["gpu_chat"],
                            "Small-12B.Q4_K_M.gguf": ["cpu_images", "gpu_chat"]}, p
ok("per-model modes: big models chat-only, deleted default falls back, offload start per profile")

print("ALL PASS")
