"use strict";
/*
 * All crypto happens here, in the browser. The derived keys never persist
 * anywhere - not localStorage/sessionStorage/IndexedDB; a page reload wipes
 * them from memory, by design. The server stores ONLY the auth half of the
 * PBKDF2 output (a login verifier that cannot decrypt anything); the
 * encryption half is delivered to it at login, wrapped under a key derived
 * from the auth half + the challenge nonce, so Cloudflare relays only
 * ciphertext it can never unwrap and the server holds the encryption key
 * in RAM only. Nothing stored on the server's disk can decrypt the prompts.
 */

const PBKDF2_ITERATIONS = 210000;
const PBKDF2_SALT = new TextEncoder().encode("imagegen-e2e-v1");

let kAuth = null; // raw bytes, HMAC key for the login proof only
let kEnc = null; // CryptoKey, AES-GCM key for every request/response after login
let currentMode = null;

const $ = (id) => document.getElementById(id);

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

// Wrap the raw enc key for transit: AES-GCM under HMAC(kAuth, "wrap-enc-key" || nonce).
// Cloudflare never sees kAuth, so it can't unwrap; the server (which stores
// kAuth as its login verifier) can.
async function wrapEncKey(authBytes, nonceBytes, encBytes) {
  const label = new TextEncoder().encode("wrap-enc-key");
  const input = new Uint8Array(label.length + nonceBytes.length);
  input.set(label); input.set(nonceBytes, label.length);
  const wrapBytes = await hmacProof(authBytes, input);
  const wrapKey = await crypto.subtle.importKey("raw", wrapBytes, "AES-GCM", false, ["encrypt"]);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, wrapKey, encBytes);
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
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, kEnc, plaintext);
  return { nonce: b64e(iv), ciphertext: b64e(new Uint8Array(ciphertext)) };
}

async function decryptEnvelope(envelope) {
  const iv = b64d(envelope.nonce);
  const ciphertext = b64d(envelope.ciphertext);
  const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, kEnc, ciphertext);
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
  const envelope = await res.json();
  return decryptEnvelope(envelope);
}

// --- login ------------------------------------------------------------------

function showLogin(error) {
  kAuth = null;
  kEnc = null;
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
    const { authBytes, encBytes, encKey } = await deriveKeys(password);
    const chRes = await fetch("/api/challenge", { method: "POST" });
    const { nonce } = await chRes.json();
    const nonceBytes = b64d(nonce);
    const proof = await hmacProof(authBytes, nonceBytes);
    const { ek_iv, ek_ct } = await wrapEncKey(authBytes, nonceBytes, encBytes);
    const loginRes = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nonce, proof: b64e(proof), ek_iv, ek_ct }),
    });
    if (!loginRes.ok) {
      showLogin("incorrect password");
      return;
    }
    kAuth = authBytes;
    kEnc = encKey;
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
      <button data-mode="chat_images">conversation with images</button>
      <button data-mode="image">image only</button>
    </div>
    <p style="margin-top:2rem"><a href="#" id="settings-link">settings</a></p>`;
  for (const btn of document.querySelectorAll("[data-mode]")) {
    btn.addEventListener("click", () => openMode(btn.dataset.mode));
  }
  $("settings-link").addEventListener("click", (e) => { e.preventDefault(); renderSettings(); });
  checkInit();
}

function renderInitState(status) {
  const area = $("init-area");
  const modeButtons = $("mode-buttons");
  if (status === "ready") {
    area.innerHTML = `<p id="chat-status"><a href="#" id="unload-btn">shut down models</a>
      &mdash; frees the GPU and RAM; your chats &amp; images stay until logout</p>`;
    modeButtons.style.display = "flex";
    $("unload-btn").addEventListener("click", (e) => { e.preventDefault(); doUnload(); });
  } else if (status === "loading") {
    area.innerHTML = `<p id="chat-status">initializing models... (~20s)</p>`;
    modeButtons.style.display = "none";
  } else {
    area.innerHTML = `<button id="init-btn">initialize system</button>
      <p id="chat-status">models aren't loaded yet - nothing uses memory until you start this</p>`;
    modeButtons.style.display = "none";
    $("init-btn").addEventListener("click", startInit);
  }
}

async function checkInit() {
  const { status } = await apiCall("/api/init-status", {});
  renderInitState(status);
  if (status === "loading") setTimeout(checkInit, 2000);
}

async function startInit() {
  const { status } = await apiCall("/api/initialize", {});
  renderInitState(status);
  if (status === "loading") setTimeout(checkInit, 2000);
}

async function doUnload() {
  const { status } = await apiCall("/api/unload", {});
  renderInitState(status);
}

async function openMode(mode) {
  currentMode = mode;
  if (mode === "image") {
    renderImageOnly();
  } else {
    renderChat(mode === "chat_images");
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
      <button type="submit">generate</button>
    </form>
    <div id="image-status"></div>
    <div id="gallery-panel" class="wide"><h3>images this session</h3><div id="gallery"></div></div>`;
  $("back").addEventListener("click", (e) => { e.preventDefault(); showModePicker(); });
  $("reset").addEventListener("click", async (e) => {
    e.preventDefault();
    await apiCall("/api/reset", { mode: "image" });
    $("gallery").innerHTML = "";
  });
  $("image-form").addEventListener("submit", onImageSubmit);
  loadState("image");
}

async function onImageSubmit(e) {
  e.preventDefault();
  const prompt = $("image-prompt").value.trim();
  if (!prompt) return;
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("image-status").textContent = "generating... (~30s)";
  try {
    const { image } = await apiCall("/api/image", { prompt });
    appendGalleryImage(image);
    $("image-status").textContent = "";
  } catch (err) {
    $("image-status").textContent = "generation failed";
  } finally {
    btn.disabled = false;
  }
}

// --- chat / chat+images modes --------------------------------------------------

function renderChat(withImages) {
  $("app").innerHTML = `
    <p><a href="#" id="back">&larr; back</a> &nbsp; <a href="#" id="reset">new conversation</a></p>
    <h2>${withImages ? "conversation with images" : "conversation"}</h2>
    <div class="chat-layout">
      <div class="chat-main">
        <div id="transcript"></div>
        <form id="chat-form">
          <textarea id="chat-message" rows="2" placeholder="say something..." autofocus required></textarea>
          <button type="submit">send</button>
        </form>
        <div id="chat-status"></div>
      </div>
      ${withImages ? '<div id="gallery-panel"><h3>images this session</h3><div id="gallery"></div></div>' : ""}
    </div>`;
  $("back").addEventListener("click", (e) => { e.preventDefault(); showModePicker(); });
  $("reset").addEventListener("click", async (e) => {
    e.preventDefault();
    await apiCall("/api/reset", { mode: currentMode });
    $("transcript").innerHTML = "";
    if (withImages) $("gallery").innerHTML = "";
  });
  $("chat-form").addEventListener("submit", (e) => onChatSubmit(e, withImages));
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

async function onChatSubmit(e, withImages) {
  e.preventDefault();
  const message = $("chat-message").value.trim();
  if (!message) return;
  $("chat-message").value = "";
  appendMessage("user", message);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("chat-status").textContent = "thinking...";
  try {
    const path = withImages ? "/api/chat-images" : "/api/chat";
    const result = await apiCall(path, { message });
    // The image (chat-images) is already rendering on the GPU in parallel;
    // start polling for it right away.
    if (withImages && result.job_id) pollImageJob(result.job_id);
    appendMessage("assistant", result.reply);
    $("chat-status").textContent = "";
  } catch (err) {
    $("chat-status").textContent = "failed to get a reply";
  } finally {
    btn.disabled = false;
  }
}

function appendPlaceholder() {
  const div = document.createElement("div");
  div.className = "img-placeholder";
  $("gallery").appendChild(div);
  div.scrollIntoView({ block: "end" });
  return div;
}

async function pollImageJob(jobId) {
  const mode = currentMode;
  const placeholder = appendPlaceholder();
  const poll = async () => {
    if (currentMode !== mode) return; // navigated away, stop polling
    let result;
    try {
      result = await apiCall("/api/image-status", { mode, job_id: jobId });
    } catch (err) {
      placeholder.remove();
      return;
    }
    if (result.status === "pending") {
      setTimeout(poll, 2000);
    } else if (result.status === "done" && result.image) {
      const img = document.createElement("img");
      img.src = "data:image/png;base64," + result.image;
      img.addEventListener("click", () => openLightbox(result.image));
      placeholder.replaceWith(img);
    } else {
      placeholder.remove();
    }
  };
  setTimeout(poll, 2000);
}

// --- boot -----------------------------------------------------------------

$("lightbox").addEventListener("click", () => $("lightbox").classList.remove("open"));
showLogin();
