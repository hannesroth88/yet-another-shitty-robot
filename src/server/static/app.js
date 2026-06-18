/* app.js — wires the avatar to the control server.
 *
 * - Opens the broadcast WebSocket and maps events -> face expressions.
 * - Plays TTS audio segments as they stream in (and twitches the mouth).
 * - Push-to-talk mic: records via MediaRecorder, ships the blob over the WS as
 *   a binary frame; the server transcribes + runs the turn.
 * - Optional webcam preview (local only for now; a hook for future vision).
 */

const face = new RobotFace(document.getElementById("face"));
const phaseEl = document.getElementById("phase");
const cfgEl = document.getElementById("cfg");
const capEl = document.getElementById("caption");
const latEl = document.getElementById("lat");
const promptEl = document.getElementById("prompt");

let ws, wsReady = false, reconnectT = null;
let botText = "";
let convMode = false;
let conv = null;
// "thinking" timer: from STT text (or a typed prompt) until the first sound
// actually plays -- the wait the user perceives.
let thinkAnchor = 0, thinkShown = false, lastThink = 0;

// The face expression must not drop out of "speaking" while local audio is
// still playing: the server flips the phase back to inactive/listening as soon
// as the LAST sentence is *synthesized*, but the browser is still playing that
// segment out of the queue. Defer the non-speaking expression until playback
// actually drains so the avatar stays in sync with what you hear.
let pendingFacePhase = null;

function audioBusy() {
  if (playing || audioQ.length) return true;          // WAV segment queue
  if (conv && conv._isPlaying()) return true;          // streamed PCM (conv mode)
  return false;
}

function applyPendingPhase() {
  if (pendingFacePhase !== null && !audioBusy()) {
    face.setPhase(pendingFacePhase);
    pendingFacePhase = null;
  }
}

function setPhase(p) {
  phaseEl.textContent = p; phaseEl.dataset.p = p;
  if (conv) conv.setServerPhase(p);
  if (p !== "speaking" && audioBusy()) {
    pendingFacePhase = p;  // hold the face in "speaking" until playback drains
    return;
  }
  pendingFacePhase = null;
  face.setPhase(p);
}

function connect() {
  const wsScheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${wsScheme}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { wsReady = true; };
  ws.onclose = () => { wsReady = false; scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
  ws.onmessage = (e) => {
    if (e.data instanceof ArrayBuffer) { if (conv) conv.onPcm(e.data); return; }
    let m; try { m = JSON.parse(e.data); } catch (_) { return; }
    handle(m);
  };
}
function scheduleReconnect() {
  if (reconnectT) return;
  reconnectT = setTimeout(() => { reconnectT = null; connect(); }, 1200);
}

function handle(m) {
  switch (m.type) {
    case "ready":
      cfgEl.textContent = `${m.llm_model || ""} · tts:${m.tts_backend || ""}`;
      convMode = !!m.conversation_mode;
      setupConversationUi();
      break;
    case "phase":
      setPhase(m.phase);
      if (m.phase === "thinking") { botText = ""; capEl.textContent = "…"; }
      break;
    case "heard_text":
      capEl.textContent = m.text ? `“${m.text}”` : "";
      // start the thinking clock the moment STT has produced text
      thinkAnchor = performance.now(); thinkShown = false; lastThink = 0;
      break;
    case "interim":
      capEl.textContent = m.text ? `“${m.text}”…` : "…";
      break;
    case "assistant_delta":
      botText += m.text; capEl.textContent = botText;
      break;
    case "assistant_speak":
      // first audio about to stream; mark think->speak latency
      if (!thinkShown && thinkAnchor) {
        lastThink = Math.round(performance.now() - thinkAnchor);
        thinkShown = true;
        latEl.innerHTML = `🤔 think→speak <b>${lastThink}ms</b>`;
        latEl.classList.add("on");
      }
      break;
    case "assistant_end":
      botText = m.text || botText; capEl.textContent = botText;
      break;
    case "tts_start":
      if (conv) conv.onTtsStart(m.sample_rate);
      break;
    case "tts_done":
      if (conv) conv.onTtsDone();
      break;
    case "tts_audio":
      playAudio(m.wav_path);
      break;
    case "latency":
      showLatency(m);
      break;
    case "busy":
      flash(capEl, m.message);
      break;
    case "error":
      setPhase("error"); flash(capEl, "⚠ " + (m.message || "error"));
      break;
  }
}

/* ---- TTS playback queue (segments arrive in order) ---- */
const audioQ = [];
let playing = false;
function playAudio(path) {
  audioQ.push(`/audio?path=${encodeURIComponent(path)}`);
  if (!playing) nextAudio();
}
function nextAudio() {
  const url = audioQ.shift();
  if (!url) { playing = false; face.setTalking(false); applyPendingPhase(); return; }
  playing = true;
  const a = new Audio(url);
  // Start the mouth exactly when sound starts (keeps the face in sync); stop
  // it when the whole queue has drained (the `if (!url)` branch above).
  a.onplay = () => {
    face.setTalking(true);
    if (!thinkShown && thinkAnchor) {
      lastThink = Math.round(performance.now() - thinkAnchor);
      thinkShown = true;
      latEl.innerHTML = `🤔 think→speak <b>${lastThink}ms</b>`;
      latEl.classList.add("on");
    }
  };
  a.onended = a.onerror = () => nextAudio();
  a.play().catch(() => nextAudio());
}

function showLatency(m) {
  const s = m.stages || {}, i = m.info || {};
  const ms = (v) => v ? Math.round(v) + "ms" : "—";
  latEl.innerHTML =
    (lastThink ? `🤔 <b>${lastThink}ms</b> · ` : "") +
    `STT <b>${ms(s.stt)}</b> · LLM <b>${ms(s.llm)}</b> · TTS <b>${ms(s.tts)}</b>` +
    (i.first_audio ? ` · 1st audio <b>${ms(i.first_audio)}</b>` : "") +
    ` · TOTAL <b>${m.total}ms</b>`;
  latEl.classList.add("on");
}

let flashT = null;
function flash(el, txt) {
  el.textContent = txt;
  clearTimeout(flashT); flashT = setTimeout(() => { el.textContent = botText || ""; }, 2500);
}

/* ---- text prompt ---- */
function sendPrompt() {
  const t = promptEl.value.trim();
  if (!t || !wsReady) return;
  ws.send(JSON.stringify({ type: "prompt", text: t }));
  capEl.textContent = `“${t}”`;
  // typed prompts have no STT step; start the thinking clock from send
  thinkAnchor = performance.now(); thinkShown = false; lastThink = 0;
  promptEl.value = "";
}
document.getElementById("send").addEventListener("click", sendPrompt);
promptEl.addEventListener("keydown", (e) => { if (e.key === "Enter") sendPrompt(); });

/* ---- hands-free conversation (ADR 0003) ---- */
function send(obj) { if (wsReady) ws.send(JSON.stringify(obj)); }
function sendBinary(buf) { if (wsReady) ws.send(buf); }

function setupConversationUi() {
  if (!convMode) return;
  micBtn.title = "Gespräch starten/stoppen";
  micBtn.textContent = "🗣️";
}

async function toggleConversation() {
  if (!conv) {
    conv = new Conversation({
      send, sendBinary,
      onTalking: (on) => { face.setTalking(on); if (!on) applyPendingPhase(); },
    });
  }
  if (conv.isActive()) {
    conv.stop();
    micBtn.classList.remove("rec");
    setPhase("inactive");
    return;
  }
  const res = await conv.start();
  if (res !== true) {
    const msg = mediaError("\uD83C\uDFA4 mic", res);
    flash(capEl, msg); return;
  }
  micBtn.classList.add("rec");
  setPhase("listening");
}

/* ---- push-to-talk mic ---- */
let mediaRec = null, micChunks = [], micStream = null;
const micBtn = document.getElementById("mic");

function releaseMic() {
  // Close the mic between turns. Holding it open puts the browser/OS in
  // "communication" mode (echo-cancellation), which ducks the speaker so you
  // can't hear the robot's own TTS. Push-to-talk re-acquires on next press.
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
}

function mediaError(kind, e) {
  // Translate the opaque getUserMedia failures into something actionable.
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return window.isSecureContext
      ? `${kind}: no media API in this browser`
      : `${kind} needs HTTPS or localhost — you're on ${location.origin}`;
  }
  const map = {
    NotAllowedError: `${kind} permission denied — allow it for this site (and grant Chrome mic/cam access in macOS System Settings ▸ Privacy)`,
    NotFoundError: `no ${kind} device found`,
    NotReadableError: `${kind} is busy in another app`,
    OverconstrainedError: `${kind} constraints unmet`,
    SecurityError: `${kind} blocked — needs HTTPS or localhost`,
  };
  return map[e && e.name] || `${kind} error: ${e ? e.name + " " + e.message : "unknown"}`;
}

async function toggleMic() {
  if (convMode) { await toggleConversation(); return; }
  if (mediaRec && mediaRec.state === "recording") { mediaRec.stop(); return; }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    const msg = mediaError("\uD83C\uDFA4 mic", null);
    console.warn(msg); flash(capEl, msg); return;
  }
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    const msg = mediaError("\uD83C\uDFA4 mic", e);
    console.warn("getUserMedia(audio) failed:", e); flash(capEl, msg); return;
  }
  micChunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus" : "";
  mediaRec = new MediaRecorder(micStream, mime ? { mimeType: mime } : undefined);
  mediaRec.ondataavailable = (e) => { if (e.data.size) micChunks.push(e.data); };
  mediaRec.onstop = async () => {
    micBtn.classList.remove("rec");
    const blob = new Blob(micChunks, { type: micChunks[0]?.type || "audio/webm" });
    releaseMic();  // close the mic so TTS playback isn't ducked by AEC
    if (blob.size && wsReady) ws.send(await blob.arrayBuffer());
  };
  mediaRec.start();
  micBtn.classList.add("rec");
}
micBtn.addEventListener("click", toggleMic);

/* ---- optional webcam preview ---- */
const cam = document.getElementById("cam");
const camBtn = document.getElementById("camBtn");
let camStream = null;
camBtn.addEventListener("click", async () => {
  if (camStream) {
    camStream.getTracks().forEach((t) => t.stop());
    camStream = null; cam.srcObject = null;
    cam.classList.remove("on"); camBtn.classList.remove("on");
    return;
  }
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
    cam.srcObject = camStream; cam.classList.add("on"); camBtn.classList.add("on");
  } catch (e) {
    const msg = mediaError("\uD83D\uDCF7 camera", e);
    console.warn("getUserMedia(video) failed:", e); flash(capEl, msg);
  }
});

/* ---- kiosk: keep screen awake + tap face for fullscreen ---- */
let wakeLock = null;
async function acquireWakeLock() {
  try {
    if ("wakeLock" in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    }
  } catch (e) { /* not supported / denied */ }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") acquireWakeLock();
});

const faceEl = document.getElementById("face");
faceEl.addEventListener("click", async () => {
  acquireWakeLock();
  const el = document.documentElement;
  try {
    if (!document.fullscreenElement) await el.requestFullscreen({ navigationUI: "hide" });
    else await document.exitFullscreen();
  } catch (e) { /* iOS Safari: use Add-to-Home-Screen instead */ }
});

setPhase("inactive");
connect();
// acquire on first user gesture (autoplay/wake-lock policies need one)
window.addEventListener("pointerdown", acquireWakeLock, { once: true });
