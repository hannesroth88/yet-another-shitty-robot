/* barge-in.js — client-side barge-in detector (ADR 0003, Phase C).
 *
 * Ported from badlogic/pibot (src/client/barge-in.ts). WebRTC echo cancellation
 * wasn't clean enough for STT, so instead we keep a ring buffer of the audio we
 * are *playing* (the TTS reference) and, for each mic frame, correlate the mic
 * signal against that reference at delays of 20–420 ms to estimate how much of
 * the mic energy is just the robot's own voice bleeding into the mic.
 *
 * Barge-in fires only when the mic RMS is above a threshold AND the unexplained
 * residual (mic energy the reference can't account for) is above a threshold,
 * for several consecutive frames — i.e. the user is really talking, not just
 * picking up the speaker. On trigger we hand back the buffered mic "preroll" so
 * the server transcribes the interrupting utterance from its true start.
 */

class BargeInDetector {
  constructor(targetSampleRate, micThreshold = 0.018, residualThreshold = 0.62, triggerFrames = 5) {
    this.micThreshold = micThreshold;
    // During the "thinking" phase the robot has no TTS playing yet, so there
    // is no playback reference to correlate against. Require a much higher raw
    // RMS to fire so background noise at 0.018 can't accidentally cancel the
    // LLM mid-generation. 0.08 ≈ deliberate loud voice, not room ambient.
    this.thinkingMicThreshold = 0.08;
    this.residualThreshold = residualThreshold;
    this.triggerFrames = triggerFrames;
    this.streaming = false;
    this.consecutiveFrames = 0;
    this.playbackReferenceSampleRate = 0;
    this.playbackReferenceWrite = 0;
    this.playbackReferenceSamples = 0;
    this.playbackReferenceRing = new Float32Array(48000 * 8);
    this.micBufferWrite = 0;
    this.micBufferSamples = 0;
    this.micBufferRing = new Int16Array(targetSampleRate);
  }

  resetStreaming() { this.streaming = false; this.consecutiveFrames = 0; }
  isStreaming() { return this.streaming; }

  // Phases where we stream mic straight to the server (normal listening).
  shouldStreamMicNormally(phase) { return phase === "listening" || phase === "hearing"; }
  // Phases where the robot is busy and we only watch for a real barge-in.
  shouldBufferForBargeIn(phase) { return phase === "thinking" || phase === "speaking"; }

  handlePlaybackAudio(samples, sampleRate) {
    if (this.playbackReferenceSampleRate !== sampleRate) {
      this.playbackReferenceSampleRate = sampleRate;
      this.playbackReferenceRing = new Float32Array(sampleRate * 8);
      this.playbackReferenceWrite = 0;
      this.playbackReferenceSamples = 0;
    }
    for (const sample of samples) {
      this.playbackReferenceRing[this.playbackReferenceWrite] = sample;
      this.playbackReferenceWrite = (this.playbackReferenceWrite + 1) % this.playbackReferenceRing.length;
      this.playbackReferenceSamples += 1;
    }
  }

  observeMic(input, sampleRate, pcm, phase) {
    this.appendMicBuffer(pcm);
    if (!this.shouldBufferForBargeIn(phase) || this.streaming) return { triggered: false };
    const rms = this.micRms(input);
    let triggered;
    let ratio = 0;
    if (phase === "thinking") {
      // No TTS reference during LLM generation: use a higher RMS-only gate so
      // background noise (fans, HVAC, 0.018 ambient) can't fire a false cancel.
      triggered = rms >= this.thinkingMicThreshold;
    } else {
      ratio = this.bargeResidualRatio(input, sampleRate);
      triggered = rms >= this.micThreshold && ratio >= this.residualThreshold;
    }
    this.consecutiveFrames = triggered ? this.consecutiveFrames + 1 : Math.max(0, this.consecutiveFrames - 1);
    if (this.consecutiveFrames < this.triggerFrames) return { triggered: false };
    this.streaming = true;
    this.consecutiveFrames = 0;
    return { triggered: true, preroll: this.bufferedMicPcm(), metrics: { micRms: rms, residualRatio: ratio } };
  }

  appendMicBuffer(pcm) {
    for (const sample of pcm) {
      this.micBufferRing[this.micBufferWrite] = sample;
      this.micBufferWrite = (this.micBufferWrite + 1) % this.micBufferRing.length;
      this.micBufferSamples = Math.min(this.micBufferSamples + 1, this.micBufferRing.length);
    }
  }

  bufferedMicPcm() {
    const out = new Int16Array(this.micBufferSamples);
    const start = (this.micBufferWrite - this.micBufferSamples + this.micBufferRing.length) % this.micBufferRing.length;
    for (let i = 0; i < this.micBufferSamples; i++) out[i] = this.micBufferRing[(start + i) % this.micBufferRing.length] ?? 0;
    return out;
  }

  micRms(input) {
    let e = 0;
    for (const s of input) e += s * s;
    return Math.sqrt(e / Math.max(1, input.length));
  }

  bargeResidualRatio(input, sampleRate) {
    if (this.playbackReferenceSampleRate !== sampleRate || this.playbackReferenceSamples < input.length) return 1;
    let micEnergy = 0;
    for (const s of input) micEnergy += s * s;
    micEnergy /= Math.max(1, input.length);
    if (micEnergy < 1e-7) return 0;
    let bestCorrelation = 0;
    // Bug fix: initialise to 0, not 1.  When all reference frames are silent
    // (referenceEnergy < 1e-7 for every delay), the loop body is skipped and
    // bestRatio must stay 0 — "I have no reference, so I can't declare a
    // barge-in based on correlation".  The old value of 1 meant the ratio was
    // always at maximum during the thinking phase (no TTS playing) and any mic
    // noise above 0.018 RMS would fire a false cancel.
    let bestRatio = 0;
    for (let delayMs = 20; delayMs <= 420; delayMs += 10) {
      const delaySamples = Math.round((delayMs / 1000) * sampleRate);
      const start = this.playbackReferenceSamples - delaySamples - input.length;
      let referenceEnergy = 0;
      let dot = 0;
      for (let i = 0; i < input.length; i++) {
        const ref = this.readPlaybackReference(start + i);
        referenceEnergy += ref * ref;
        dot += input[i] * ref;
      }
      referenceEnergy /= Math.max(1, input.length);
      dot /= Math.max(1, input.length);
      if (referenceEnergy < 1e-7) continue;
      const correlation = Math.abs(dot) / Math.sqrt(referenceEnergy * micEnergy);
      const explained = (dot * dot) / referenceEnergy;
      const ratio = Math.max(0, micEnergy - explained) / micEnergy;
      if (correlation > bestCorrelation) { bestCorrelation = correlation; bestRatio = ratio; }
    }
    return bestRatio;
  }

  readPlaybackReference(totalIndex) {
    if (totalIndex < 0 || totalIndex >= this.playbackReferenceSamples) return 0;
    return this.playbackReferenceRing[totalIndex % this.playbackReferenceRing.length] ?? 0;
  }
}
