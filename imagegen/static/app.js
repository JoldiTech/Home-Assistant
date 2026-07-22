"use strict";
/*
 * All crypto happens here, in the browser. No key persists anywhere - not
 * localStorage/sessionStorage/IndexedDB; a page reload wipes memory, by
 * design. The server stores ONLY the auth half of the PBKDF2 output (a
 * login verifier that cannot decrypt anything). Every login runs an
 * ephemeral ECDH exchange; all traffic rides a session key bound to BOTH
 * the password and that one-time secret, so once this tab closes, recorded
 * ciphertext is undecryptable forever - even with the password (forward
 * secrecy). The prompt-store key crosses only wrapped under the session
 * key and lives in server RAM only.
 */

const PBKDF2_ITERATIONS = 210000;
const PBKDF2_SALT = new TextEncoder().encode("imagegen-e2e-v1");

// The only key kept after login: the per-session AES-GCM key, bound to the
// password AND an ephemeral ECDH exchange. It exists in this variable and the
// server's session table, nowhere else - when this tab closes, everything
// either end ever sent is undecryptable forever (forward secrecy).
let kSess = null;
let currentMode = null;
let imagesEnabled = true;   // false in GPU-chat mode (SDXL not loaded)

const $ = (id) => document.getElementById(id);

const CHAT_MODE_LABELS = {
  cpu_images: "CPU chat + images (chat on CPU, GPU runs image generation)",
  gpu_chat: "GPU chat, no images (fast chat on the GPU; image generation off)",
};

async function deriveKeys(password) {
  const passBytes = new TextEncoder().encode(password);
  const material = await crypto.subtle.importKey("raw", passBytes, "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: PBKDF2_SALT, iterations: PBKDF2_ITERATIONS, hash: "SHA-256" },
    material,
    512 // 64 bytes: first 32 = auth key, last 32 = enc key
  );
  const full = new Uint8Array(bits);
  const authBytes = full.slice(0, 32);
  const encBytes = full.slice(32, 64);
  const encKey = await crypto.subtle.importKey("raw", encBytes, "AES-GCM", false, ["encrypt", "decrypt"]);
  return { authBytes, encBytes, encKey };
}

// Ephemeral ECDH + password-bound session key:
//   kSess = HMAC(kAuth, "session-v1" || nonce || ECDH_shared)
// The DH share provides forward secrecy (both ephemeral privates die with
// the session); mixing kAuth authenticates the exchange, so a relay that
// doesn't know the password can't man-in-the-middle it.
async function deriveSessionKey(authBytes, nonceBytes, serverPubB64) {
  const myPair = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
  const serverPub = await crypto.subtle.importKey(
    "raw", b64d(serverPubB64), { name: "ECDH", namedCurve: "P-256" }, false, []);
  const sharedBits = await crypto.subtle.deriveBits({ name: "ECDH", public: serverPub }, myPair.privateKey, 256);
  const label = new TextEncoder().encode("session-v1");
  const shared = new Uint8Array(sharedBits);
  const input = new Uint8Array(label.length + nonceBytes.length + shared.length);
  input.set(label); input.set(nonceBytes, label.length); input.set(shared, label.length + nonceBytes.length);
  const kSessBytes = await hmacProof(authBytes, input);
  const sessKey = await crypto.subtle.importKey("raw", kSessBytes, "AES-GCM", false, ["encrypt", "decrypt"]);
  const myPubRaw = new Uint8Array(await crypto.subtle.exportKey("raw", myPair.publicKey));
  return { sessKey, clientPubB64: b64e(myPubRaw) };
}

// The prompt-store key travels wrapped under the session key.
async function wrapEncKey(sessKey, encBytes) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, sessKey, encBytes);
  return { ek_iv: b64e(iv), ek_ct: b64e(new Uint8Array(ct)) };
}

async function hmacProof(authBytes, nonceBytes) {
  const key = await crypto.subtle.importKey("raw", authBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, nonceBytes);
  return new Uint8Array(sig);
}

function b64e(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

function b64d(str) {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function encryptEnvelope(obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const plaintext = new TextEncoder().encode(JSON.stringify(obj));
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, kSess, plaintext);
  return { nonce: b64e(iv), ciphertext: b64e(new Uint8Array(ciphertext)) };
}

async function decryptEnvelope(envelope) {
  const iv = b64d(envelope.nonce);
  const ciphertext = b64d(envelope.ciphertext);
  const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, kSess, ciphertext);
  return JSON.parse(new TextDecoder().decode(plaintext));
}

async function apiCall(path, payload) {
  const body = payload === undefined ? "{}" : JSON.stringify(await encryptEnvelope(payload));
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (res.status === 401) {
    showLogin("session expired - enter password again");
    throw new Error("session expired");
  }
  const raw = await res.json();
  if (!res.ok) {
    // Error bodies (503 GPU-busy, 500) are plain JSON, not envelopes -
    // surface the server's actual reason instead of a generic failure.
    throw new Error(raw.error || `request failed (${res.status})`);
  }
  return decryptEnvelope(raw);
}

// --- login ------------------------------------------------------------------

function showLogin(error) {
  kSess = null;
  currentMode = null;
  $("app").innerHTML = `
    <h2>locked</h2>
    ${error ? `<p class="err">${escapeText(error)}</p>` : ""}
    <form id="login-form">
      <input type="password" id="login-password" placeholder="password" autofocus autocomplete="off" required>
      <button type="submit">enter</button>
    </form>`;
  $("login-form").addEventListener("submit", onLoginSubmit);
}

function escapeText(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function onLoginSubmit(e) {
  e.preventDefault();
  const password = $("login-password").value;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const { authBytes, encBytes } = await deriveKeys(password);
    const chRes = await fetch("/api/challenge", { method: "POST" });
    const { nonce, server_pub } = await chRes.json();
    const nonceBytes = b64d(nonce);
    const proof = await hmacProof(authBytes, nonceBytes);
    const { sessKey, clientPubB64 } = await deriveSessionKey(authBytes, nonceBytes, server_pub);
    const { ek_iv, ek_ct } = await wrapEncKey(sessKey, encBytes);
    const loginRes = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nonce, proof: b64e(proof), client_pub: clientPubB64, ek_iv, ek_ct }),
    });
    if (!loginRes.ok) {
      showLogin("incorrect password");
      return;
    }
    // Only the session key survives past this point - the password-derived
    // halves are dropped so nothing longer-lived than the session exists here.
    kSess = sessKey;
    showModePicker();
  } catch (err) {
    showLogin("something went wrong, try again");
  } finally {
    btn.disabled = false;
  }
}

// --- settings (editable prompts + change password) ----------------------------

async function renderSettings() {
  currentMode = null;
  $("app").innerHTML = `
    <p><a href="#" id="back">&larr; back</a></p>
    <h2>settings</h2>
    <h3>assistant prompts</h3>
    <label>system prompt (who the assistant is / how she behaves)</label>
    <textarea id="sys-prompt" rows="6"></textarea>
    <label>image prompt prefix (prepended to every conversation image)</label>
    <textarea id="img-prefix" rows="3"></textarea>
    <button id="save-prompts">save prompts</button>
    <p id="prompts-status"></p>
    <h3 style="margin-top:2rem">change password</h3>
    <p style="color:#888;font-size:.85rem">this password unlocks the app AND is the encryption key.
      changing it re-encrypts saved prompts and logs everyone out.</p>
    <input type="password" id="new-pass" placeholder="new password (min 8 chars)" autocomplete="off">
    <input type="password" id="new-pass2" placeholder="confirm new password" autocomplete="off">
    <button id="change-pass">change password</button>
    <p id="pass-status"></p>`;
  $("back").addEventListener("click", (e) => { e.preventDefault(); showModePicker(); });
  $("save-prompts").addEventListener("click", onSavePrompts);
  $("change-pass").addEventListener("click", onChangePassword);
  try {
    const p = await apiCall("/api/get-prompts", {});
    $("sys-prompt").value = p.system_prompt || "";
    $("img-prefix").value = p.image_prompt_prefix || "";
  } catch (err) {
    $("prompts-status").textContent = "couldn't load current prompts";
  }
}

async function onSavePrompts() {
  const btn = $("save-prompts");
  btn.disabled = true;
  $("prompts-status").textContent = "saving...";
  try {
    const res = await apiCall("/api/set-prompts", {
      system_prompt: $("sys-prompt").value,
      image_prompt_prefix: $("img-prefix").value,
    });
    $("prompts-status").textContent = res.error ? res.error : "saved (encrypted at rest)";
  } catch (err) {
    $("prompts-status").textContent = "save failed";
  } finally {
    btn.disabled = false;
  }
}

async function onChangePassword() {
  const p1 = $("new-pass").value;
  const p2 = $("new-pass2").value;
  if (p1 !== p2) { $("pass-status").textContent = "passwords don't match"; return; }
  if (p1.length < 8) { $("pass-status").textContent = "password must be at least 8 characters"; return; }
  const btn = $("change-pass");
  btn.disabled = true;
  $("pass-status").textContent = "changing...";
  try {
    // The password itself never crosses the network - derive the new key
    // pair here and send those (inside the current session's envelope).
    const nk = await deriveKeys(p1);
    const res = await apiCall("/api/change-password", {
      new_k_auth: b64e(nk.authBytes),
      new_k_enc: b64e(nk.encBytes),
    });
    if (res.error) {
      $("pass-status").textContent = res.error;
      btn.disabled = false;
      return;
    }
    // Success: server wiped all sessions. Force a fresh login under the new key.
    showLogin("password changed - log in with the new password");
  } catch (err) {
    $("pass-status").textContent = "change failed";
    btn.disabled = false;
  }
}

// --- mode picker --------------------------------------------------------------

function showModePicker() {
  currentMode = null;
  $("app").innerHTML = `
    <h2>generate</h2>
    <div id="init-area"></div>
    <div class="modes" id="mode-buttons" style="display:none">
      <button data-mode="chat">conversation</button>
      <button data-mode="image">image only</button>
    </div>
    <p style="margin-top:2rem"><a href="#" id="settings-link">settings</a></p>`;
  for (const btn of document.querySelectorAll("[data-mode]")) {
    btn.addEventListener("click", () => openMode(btn.dataset.mode));
  }
  $("settings-link").addEventListener("click", (e) => { e.preventDefault(); renderSettings(); });
  checkInit();
}

function renderInitState(st) {
  const area = $("init-area");
  const modeButtons = $("mode-buttons");
  if (st.status === "ready") {
    imagesEnabled = st.images !== false;
    const modeNote = imagesEnabled ? "CPU chat + images" : "GPU chat (images off)";
    area.innerHTML = `<p id="chat-status">chat model: ${escapeText(st.chat_model)} — ${modeNote}
      &mdash; <a href="#" id="unload-btn">shut down models</a>
      (frees the GPU and RAM; your chats stay until logout)</p>`;
    modeButtons.style.display = "flex";
    // "image only" is meaningless in GPU-chat mode (SDXL isn't loaded).
    const imgBtn = document.querySelector('[data-mode="image"]');
    if (imgBtn) imgBtn.style.display = imagesEnabled ? "" : "none";
    $("unload-btn").addEventListener("click", (e) => { e.preventDefault(); doUnload(); });
  } else if (st.status === "loading") {
    area.innerHTML = `<p id="chat-status">initializing models... (~30s)</p>`;
    modeButtons.style.display = "none";
  } else {
    // cold OR error - both are startable; the server ships the model + mode
    // lists in both. Build <option>s as DOM nodes (no attribute injection).
    modeButtons.style.display = "none";
    const errLine = st.status === "error" && st.error
      ? `<p class="err">last attempt failed: ${escapeText(st.error)} — try again</p>` : "";
    area.innerHTML = `
      <label for="chat-model-sel">chat model</label>
      <select id="chat-model-sel"></select>
      <label for="image-model-sel">image model (used in CPU chat + images)</label>
      <select id="image-model-sel"></select>
      <label for="chat-mode-sel">mode</label>
      <select id="chat-mode-sel"></select>
      <button id="init-btn">initialize system</button>
      ${errLine}
      <p id="chat-status">models aren't loaded yet - nothing uses memory until you start this</p>`;
    fillSelect($("chat-model-sel"), st.models || [st.chat_model], st.chat_model, (m) => m);
    fillSelect($("image-model-sel"), st.image_models || [st.image_model], st.image_model,
               (m) => m.replace(/\.safetensors$/, ""));
    fillSelect($("chat-mode-sel"), st.modes || ["cpu_images"], st.mode || "cpu_images",
               (m) => CHAT_MODE_LABELS[m] || m);
    $("init-btn").addEventListener("click", startInit);
  }
}

function fillSelect(sel, values, selected, labelFn) {
  for (const v of values) {
    const opt = document.createElement("option");
    opt.value = v;              // safe: set as a property, never parsed as HTML
    opt.textContent = labelFn(v);
    if (v === selected) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function checkInit() {
  const st = await apiCall("/api/init-status", {});
  renderInitState(st);
  if (st.status === "loading") setTimeout(checkInit, 2000);
}

async function startInit() {
  const st = await apiCall("/api/initialize", {
    chat_model: $("chat-model-sel") ? $("chat-model-sel").value : undefined,
    image_model: $("image-model-sel") ? $("image-model-sel").value : undefined,
    chat_mode: $("chat-mode-sel") ? $("chat-mode-sel").value : undefined,
  });
  renderInitState(st);
  if (st.status === "loading") setTimeout(checkInit, 2000);
}

async function doUnload() {
  const st = await apiCall("/api/unload", {});
  renderInitState(st);
}

async function openMode(mode) {
  currentMode = mode;
  if (mode === "image") {
    renderImageOnly();
  } else {
    renderChat();
    await loadState(mode);
  }
}

// --- image-only mode ----------------------------------------------------------

function renderImageOnly() {
  $("app").innerHTML = `
    <p><a href="#" id="back">&larr; back</a> &nbsp; <a href="#" id="reset">clear session images</a></p>
    <h2>generate an image</h2>
    <form id="image-form">
      <textarea id="image-prompt" rows="3" placeholder="describe the image..." autofocus required></textarea>
      <textarea id="image-negative" rows="2" placeholder="avoid in the image (optional - a sensible default is applied if empty)"></textarea>
      <label><input type="checkbox" id="image-assist"> prompt assist - the local LLM adds artistic
        direction (composition, lighting, style) to your idea first (+~15s)</label>
      <label for="image-refs">reference face - optional. A clear, front-facing headshot puts
        that person's likeness into the image. Runs slower (~1 min, streams weights to fit
        the card) and needs a detectable face. Never stored.</label>
      <input type="file" id="image-refs" accept="image/*">
      <label for="image-ref-strength">reference strength: <span id="ref-strength-val">0.7</span>
        (higher = closer likeness, but the prompt steers less)</label>
      <input type="range" id="image-ref-strength" min="0.1" max="1.0" step="0.05" value="0.7">
      <label for="image-quality">quality</label>
      <select id="image-quality">
        <option value="quick">quick (~15s)</option>
        <option value="balanced" selected>balanced (~30s)</option>
        <option value="best">best (~55s)</option>
      </select>
      <button type="submit">generate</button>
    </form>
    <div id="used-prompt"></div>
    <div id="image-status"></div>
    <div id="gallery-panel" class="wide"><h3>images this session</h3><div id="gallery"></div></div>`;
  $("back").addEventListener("click", (e) => { e.preventDefault(); showModePicker(); });
  $("reset").addEventListener("click", async (e) => {
    e.preventDefault();
    await apiCall("/api/reset", { mode: "image" });
    $("gallery").innerHTML = "";
  });
  $("image-form").addEventListener("submit", onImageSubmit);
  $("image-ref-strength").addEventListener("input", (e) => {
    $("ref-strength-val").textContent = e.target.value;
  });
  loadState("image");
}

function fileToB64(file) {
  return new Promise((resolve, reject) => {
    if (file.size > 10 * 1024 * 1024) { reject(new Error("reference image over 10MB")); return; }
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1]);
    r.onerror = () => reject(new Error("could not read reference image"));
    r.readAsDataURL(file);
  });
}

async function onImageSubmit(e) {
  e.preventDefault();
  const prompt = $("image-prompt").value.trim();
  if (!prompt) return;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  const quality = $("image-quality").value;
  const assist = $("image-assist").checked;
  const negRaw = $("image-negative").value.trim();
  const eta = { quick: "~15s", balanced: "~30s", best: "~55s" }[quality] || "";
  $("image-status").textContent = `generating (${quality}${assist ? " + assist" : ""})... ${eta}`;
  try {
    const payload = { prompt, quality, assist };
    if (negRaw) payload.negative = negRaw;
    const refFiles = Array.from($("image-refs").files || []).slice(0, 1);
    if (refFiles.length) {
      payload.refs = await Promise.all(refFiles.map(fileToB64));
      payload.ref_strength = parseFloat($("image-ref-strength").value);
    }
    const { image, used_prompt } = await apiCall("/api/image", payload);
    appendGalleryImage(image);
    $("used-prompt").textContent = used_prompt ? `assist used: ${used_prompt}` : "";
    $("image-status").textContent = "";
  } catch (err) {
    $("image-status").textContent = err.message || "generation failed";
  } finally {
    btn.disabled = false;
  }
}

// --- chat / chat+images modes --------------------------------------------------

function renderChat() {
  const imgControls = imagesEnabled
    ? `<button type="button" id="get-image-btn" title="render the current moment of the conversation">get image</button>`
    : "";
  const gallery = imagesEnabled
    ? `<div id="gallery-panel"><h3>images this session</h3><div id="gallery"></div></div>` : "";
  $("app").innerHTML = `
    <p><a href="#" id="back">&larr; back</a> &nbsp; <a href="#" id="reset">new conversation</a></p>
    <h2>conversation</h2>
    <div class="chat-layout">
      <div class="chat-main">
        <div id="transcript"></div>
        <form id="chat-form">
          <textarea id="chat-message" rows="2" placeholder="say something..." autofocus required></textarea>
          <button type="submit" id="send-btn">send</button>
          <button type="button" id="stop-btn" style="display:none">stop</button>
          ${imgControls}
        </form>
        <div id="chat-status"></div>
      </div>
      ${gallery}
    </div>`;
  $("back").addEventListener("click", (e) => { e.preventDefault(); showModePicker(); });
  $("reset").addEventListener("click", async (e) => {
    e.preventDefault();
    await apiCall("/api/reset", { mode: currentMode });
    $("transcript").innerHTML = "";
    if ($("gallery")) $("gallery").innerHTML = "";
  });
  $("chat-form").addEventListener("submit", onChatSubmit);
  $("stop-btn").addEventListener("click", onChatStop);
  if ($("get-image-btn")) $("get-image-btn").addEventListener("click", onGetImage);
}

async function onChatStop() {
  $("stop-btn").disabled = true;
  try { await apiCall("/api/chat-stop", {}); } catch (e) { /* stream ends on its own */ }
}

async function onGetImage() {
  const btn = $("get-image-btn");
  btn.disabled = true;
  $("chat-status").textContent = "picturing the scene... (~45s: the LLM reads the conversation, then the image renders)";
  try {
    const { job_id } = await apiCall("/api/chat-image", {});
    if (job_id) pollImageJob(job_id, (errMsg) => { $("chat-status").textContent = errMsg || ""; });
    else $("chat-status").textContent = "";
  } catch (err) {
    $("chat-status").textContent = err.message || "image failed";
  } finally {
    btn.disabled = false;
  }
}

function openLightbox(b64png) {
  const box = $("lightbox");
  box.innerHTML = "";
  const img = document.createElement("img");
  img.src = "data:image/png;base64," + b64png;
  box.appendChild(img);
  box.classList.add("open");
}

function appendMessage(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const label = document.createElement("b");
  label.textContent = role === "user" ? "you: " : "assistant: ";
  div.appendChild(label);
  const span = document.createElement("span");
  span.textContent = text;
  div.appendChild(span);
  $("transcript").appendChild(div);
  div.scrollIntoView({ block: "end" });
  return span; // caller can stream more text into it
}

function appendGalleryImage(b64png) {
  const img = document.createElement("img");
  img.src = "data:image/png;base64," + b64png;
  img.addEventListener("click", () => openLightbox(b64png));
  $("gallery").appendChild(img);
  img.scrollIntoView({ block: "end" });
}

async function loadState(mode) {
  try {
    const { history, gallery } = await apiCall("/api/state", { mode });
    for (const m of history) appendMessage(m.role, m.content);
    if (gallery) for (const img of gallery) appendGalleryImage(img);
  } catch (err) {
    // fresh conversation, nothing to load
  }
}

async function onChatSubmit(e) {
  e.preventDefault();
  const message = $("chat-message").value.trim();
  if (!message) return;
  $("chat-message").value = "";
  appendMessage("user", message);
  const btn = $("send-btn");
  btn.disabled = true;
  const stopBtn = $("stop-btn");
  stopBtn.style.display = "";
  stopBtn.disabled = false;
  $("chat-status").textContent = "thinking...";
  const span = appendMessage("assistant", "");
  try {
    // The reply streams as newline-delimited encrypted envelopes: each line
    // decrypts to {delta} to append, {replace} to retract-and-rewrite (the
    // server's live channel-token filter), {error}, or {done}.
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(await encryptEnvelope({ message })),
    });
    if (res.status === 401) {
      showLogin("session expired - enter password again");
      throw new Error("session expired");
    }
    if (!res.ok) {
      const raw = await res.json();
      throw new Error(raw.error || `request failed (${res.status})`);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "", text = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        const obj = await decryptEnvelope(JSON.parse(line));
        if (obj.delta) { text += obj.delta; span.textContent = text; }
        else if (obj.replace !== undefined) { text = obj.replace; span.textContent = text; }
        else if (obj.error) throw new Error(obj.error);
      }
      span.parentElement.scrollIntoView({ block: "end" });
    }
    if (!text) span.parentElement.remove();
    $("chat-status").textContent = "";
  } catch (err) {
    if (!span.textContent) span.parentElement.remove();
    $("chat-status").textContent = err.message || "failed to get a reply";
  } finally {
    btn.disabled = false;
    if ($("stop-btn")) $("stop-btn").style.display = "none";
  }
}

function appendPlaceholder() {
  const div = document.createElement("div");
  div.className = "img-placeholder";
  $("gallery").appendChild(div);
  div.scrollIntoView({ block: "end" });
  return div;
}

async function pollImageJob(jobId, onDone) {
  const mode = currentMode;
  const placeholder = appendPlaceholder();
  const poll = async () => {
    if (currentMode !== mode) return; // navigated away, stop polling
    let result;
    try {
      result = await apiCall("/api/image-status", { mode, job_id: jobId });
    } catch (err) {
      placeholder.remove();
      if (onDone) onDone(err.message || "image failed");
      return;
    }
    if (result.status === "pending") {
      setTimeout(poll, 2000);
    } else if (result.status === "done" && result.image) {
      const img = document.createElement("img");
      img.src = "data:image/png;base64," + result.image;
      img.addEventListener("click", () => openLightbox(result.image));
      placeholder.replaceWith(img);
      if (onDone) onDone();
    } else {
      placeholder.remove();
      if (onDone) onDone(result.error || "image generation failed");
    }
  };
  setTimeout(poll, 2000);
}

// --- boot -----------------------------------------------------------------

$("lightbox").addEventListener("click", () => $("lightbox").classList.remove("open"));
showLogin();
