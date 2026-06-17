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

function setPhase(p) { phaseEl.textContent = p; phaseEl.dataset.p = p; face.setPhase(p); }

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { wsReady = true; };
  ws.onclose = () => { wsReady = false; scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
  ws.onmessage = (e) => {
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
      break;
    case "phase":
      setPhase(m.phase);
      if (m.phase === "thinking") { botText = ""; capEl.textContent = "…"; }
      break;
    case "heard_text":
      capEl.textContent = m.text ? `“${m.text}”` : "";
      break;
    case "assistant_delta":
      botText += m.text; capEl.textContent = botText;
      break;
    case "assistant_end":
      botText = m.text || botText; capEl.textContent = botText;
      break;
    case "tts_audio":
      face.pulseMouth();
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
  if (!url) { playing = false; return; }
  playing = true;
  const a = new Audio(url);
  a.onended = a.onerror = () => nextAudio();
  a.play().catch(() => nextAudio());
}

function showLatency(m) {
  const s = m.stages || {}, i = m.info || {};
  const ms = (v) => v ? Math.round(v) + "ms" : "—";
  latEl.innerHTML =
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
  promptEl.value = "";
}
document.getElementById("send").addEventListener("click", sendPrompt);
promptEl.addEventListener("keydown", (e) => { if (e.key === "Enter") sendPrompt(); });

/* ---- push-to-talk mic ---- */
let mediaRec = null, micChunks = [], micStream = null;
const micBtn = document.getElementById("mic");
async function toggleMic() {
  if (mediaRec && mediaRec.state === "recording") { mediaRec.stop(); return; }
  try {
    micStream = micStream || await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) { flash(capEl, "🎤 mic denied"); return; }
  micChunks = [];
  const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
    ? "audio/webm;codecs=opus" : "";
  mediaRec = new MediaRecorder(micStream, mime ? { mimeType: mime } : undefined);
  mediaRec.ondataavailable = (e) => { if (e.data.size) micChunks.push(e.data); };
  mediaRec.onstop = async () => {
    micBtn.classList.remove("rec");
    const blob = new Blob(micChunks, { type: micChunks[0]?.type || "audio/webm" });
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
  } catch (e) { flash(capEl, "📷 camera denied"); }
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
