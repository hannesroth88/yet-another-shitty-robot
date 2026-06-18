/* conversation.js — hands-free conversation engine (ADR 0003, Phases A–C).
 *
 * Owns a single AudioContext used for BOTH mic capture and TTS playback (so the
 * sample rates match and the barge-in correlation is valid). It:
 *   - streams 16 kHz PCM16 mic frames to the server over the WS (binary),
 *   - schedules TTS PCM frames from the server gaplessly with the Web Audio API,
 *   - runs the BargeInDetector to interrupt the robot when the user talks over it.
 *
 * The server runs VAD + ASR and decides turn boundaries; this client only
 * decides *when to stream the mic* (always while listening/hearing; only on a
 * real barge-in while the robot is thinking/speaking) and *how to play* the PCM.
 */

class Conversation {
  constructor(opts) {
    this.send = opts.send;             // (objJSON) -> void
    this.sendBinary = opts.sendBinary; // (ArrayBuffer) -> void
    this.onPhase = opts.onPhase || (() => {});
    this.onTalking = opts.onTalking || (() => {});
    this.active = false;
    this.serverPhase = "listening";
    this.ctx = null;
    this.micStream = null;
    this.micProc = null;
    this.playbackTap = null;
    this.silent = null;
    this.detector = null;
    this.ttsSampleRate = 24000;
    this.nextPlayTime = 0;
    this.ttsSpeaking = false;
    this.activeSources = [];
    this.PREBUFFER = 0.08; // 80 ms jitter buffer before playback starts
    this.TAIL_MS = 600;    // keep mic muted this long after playback drains
    this._lastPlayingAt = 0;
  }

  isActive() { return this.active; }

  async start() {
    if (this.active) return true;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        // Echo cancellation ON: the phone speaker is loud and close to the mic;
        // without AEC the robot hears itself and talks to itself. The barge-in
        // detector then keys on the residual energy the AEC can't remove.
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch (e) { return e; }
    this.micStream = stream;
    this.ctx = new AudioContext();
    await this.ctx.resume();
    const rate = this.ctx.sampleRate;
    this.detector = new BargeInDetector(16000);

    // Playback tap: pass-through ScriptProcessor that also feeds the barge-in
    // reference with the ACTUAL output samples at the context rate.
    this.playbackTap = this.ctx.createScriptProcessor(1024, 1, 1);
    this.playbackTap.onaudioprocess = (e) => {
      const inb = e.inputBuffer.getChannelData(0);
      e.outputBuffer.getChannelData(0).set(inb);
      this.detector.handlePlaybackAudio(inb, rate);
    };
    this.playbackTap.connect(this.ctx.destination);

    // Mic capture: pull a ScriptProcessor via a muted gain node (no feedback).
    const src = this.ctx.createMediaStreamSource(stream);
    this.micProc = this.ctx.createScriptProcessor(1024, 1, 1);
    this.micProc.onaudioprocess = (e) => this._onMicFrame(e.inputBuffer.getChannelData(0), rate);
    this.silent = this.ctx.createGain();
    this.silent.gain.value = 0;
    src.connect(this.micProc);
    this.micProc.connect(this.silent);
    this.silent.connect(this.ctx.destination);

    this.active = true;
    this.send({ type: "audio_start", sampleRate: 16000, format: "pcm16le" });
    return true;
  }

  stop() {
    if (!this.active) return;
    this.active = false;
    this.send({ type: "audio_stop" });
    this._stopPlayback();
    try { this.micStream.getTracks().forEach((t) => t.stop()); } catch (e) {}
    try { this.micProc.disconnect(); this.playbackTap.disconnect(); this.silent.disconnect(); } catch (e) {}
    try { this.ctx.close(); } catch (e) {}
    this.ctx = null;
  }

  setServerPhase(phase) {
    this.serverPhase = phase;
    // Drop the latched barge-streaming flag once the user's interruption is
    // over: either we are back to plain listening, or the robot has begun a new
    // turn ("thinking"). Otherwise the flag stays stuck and the client keeps
    // streaming the robot's own voice back to the server.
    if (this.detector && (phase === "listening" || phase === "thinking")) this.detector.resetStreaming();
  }

  /* ---- inbound TTS PCM (binary frames) ---- */
  onTtsStart(sampleRate) {
    this.ttsSampleRate = sampleRate || 24000;
    this.nextPlayTime = 0;
    this.ttsSpeaking = true;
    this.onTalking(true);
  }
  onPcm(buf) {
    if (!this.ctx || !this.ttsSpeaking) return;
    const n = Math.floor(buf.byteLength / 2);
    if (n === 0) return;
    const view = new DataView(buf);
    const ab = this.ctx.createBuffer(1, n, this.ttsSampleRate);
    const ch = ab.getChannelData(0);
    for (let i = 0; i < n; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
    const now = this.ctx.currentTime;
    if (this.nextPlayTime < now + 0.01) this.nextPlayTime = now + this.PREBUFFER;
    const s = this.ctx.createBufferSource();
    s.buffer = ab;
    s.connect(this.playbackTap);
    s.start(this.nextPlayTime);
    this.nextPlayTime += ab.duration;
    this.activeSources.push(s);
    s.onended = () => {
      const i = this.activeSources.indexOf(s);
      if (i >= 0) this.activeSources.splice(i, 1);
      if (!this.ttsSpeaking && this.activeSources.length === 0) this.onTalking(false);
    };
  }
  onTtsDone() {
    this.ttsSpeaking = false;
    if (this.activeSources.length === 0) this.onTalking(false);
  }

  _stopPlayback() {
    this.ttsSpeaking = false;
    for (const s of this.activeSources) { try { s.stop(); } catch (e) {} }
    this.activeSources = [];
    this.nextPlayTime = 0;
    this.onTalking(false);
  }

  /* ---- is the robot's own audio still coming out of THIS phone? ---- */
  _isPlaying() {
    if (!this.ctx) return false;
    if (this.ttsSpeaking) return true;
    if (this.activeSources.length > 0) return true;
    if (this.nextPlayTime > this.ctx.currentTime + 0.02) return true;
    return false;
  }

  /* ---- outbound mic ---- */
  _onMicFrame(input, rate) {
    if (!this.active) return;
    const pcm = this._resampleToPcm16(input, rate, 16000);

    // "Busy" = the robot is producing or playing audio (locally or per server),
    // plus a short acoustic tail. While busy we must NOT stream the mic to the
    // server's VAD -- it would capture the robot's own voice -> self-talk. The
    // ONLY way audio leaves during busy is an explicit barge-in.
    const now = performance.now();
    const playing = this._isPlaying();
    if (playing) this._lastPlayingAt = now;
    const inTail = now - this._lastPlayingAt < this.TAIL_MS;
    const serverBusy = this.serverPhase === "thinking" || this.serverPhase === "speaking";
    const busy = playing || inTail || serverBusy;

    const barge = this.detector.observeMic(input, rate, pcm, busy ? "speaking" : this.serverPhase);
    if (barge.triggered) {
      this._stopPlayback();
      this._lastPlayingAt = 0;
      this.send({ type: "barge_in" });
      if (barge.preroll && barge.preroll.length) this.sendBinary(barge.preroll.buffer.slice(0));
    }

    let shouldStream;
    if (busy) {
      shouldStream = this.detector.isStreaming(); // only after a real barge-in
    } else {
      shouldStream = this.serverPhase === "listening" || this.serverPhase === "hearing";
    }
    if (shouldStream) this.sendBinary(pcm.buffer.slice(0));
  }

  _resampleToPcm16(input, inRate, outRate) {
    const ratio = outRate / inRate;
    const outLen = Math.max(1, Math.round(input.length * ratio));
    const out = new Int16Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const pos = i / ratio;
      const i0 = Math.floor(pos);
      const frac = pos - i0;
      const s0 = input[i0] || 0;
      const s1 = i0 + 1 < input.length ? input[i0 + 1] : s0;
      const v = s0 + (s1 - s0) * frac;
      out[i] = Math.max(-32768, Math.min(32767, Math.round(v * 32768)));
    }
    return out;
  }
}
