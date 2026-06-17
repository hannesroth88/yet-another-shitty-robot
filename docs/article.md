heard about mario zechners Robot build https://mariozechner.at/posts/2026-05-30-shitty-robot/
have also 2 kids and watned to have a fun project and of course learn more about intersection of robot and AI also learn about model inteference quantization and optimize on my hardware setup.
For robot live interaction its super important to have that human interaction. Listen when needed, answer when human is done speaking and so on.

Ordered 2 octobots, wnated to use my old Pixel smartphone. esp32 and a motor driver for the one motor in the octobot.

## Text-to-speech: making it fast and making it sound like *my* robot

The voice was the first thing that really annoyed me. I had Qwen3-TTS running,
but two problems: it took ~2 minutes before I heard anything, and it sounded
bad.

Turned out I was on the PyTorch/transformers path of Qwen3-TTS. On my M1 that
runs float32 on the Metal/MPS backend at a real-time factor of about 4 - meaning
4 seconds of compute for every 1 second of audio. I measured it: a 10s clip took
40s, plus ~10s to load the model every single run. On top of that I was running
the canned "ryan" speaker through a homemade robot DSP effect (bitcrush, ring
mod, the whole "tiny robot" preset), which is what made it sound rough.

Then I looked at how Mario actually did it in pibot. He does *not* use the slow
PyTorch path and he does *not* fake the robot sound with DSP. Instead he uses the
Qwen3-TTS **Base** model, which can clone a voice in-context (ICL) from a short
reference clip plus its transcript, and he runs it through native, quantized
runtimes (MLX 6-bit on Apple Silicon, or a C++/Metal GGUF build). His reference
voice is a clip he generated on ElevenLabs.

So I did the same thing, the Python/MLX variant:

- Made my own reference voice on elevenlabs.io (a friendly small teaching robot),
  exported the mp3 + the exact transcript.
- Converted it to a clean 24 kHz mono wav with ffmpeg.
- Installed `mlx-audio` (Apple-native, runs on Metal).
- Loaded the 6-bit Base model `mlx-community/Qwen3-TTS-12Hz-0.6B-Base-6bit` and
  voice-cloned my reference clip with `model.generate(text=..., ref_audio=...,
  ref_text=...)`.
- Dropped the robot DSP entirely - the voice now comes straight from my
  reference sample.

The difference: real-time factor went from ~4 down to **under 1 on longer text**
(0.85), roughly 4-5x faster, and it actually sounds like the voice I picked
instead of a bitcrushed "ryan". Soundcheck successful.

In the repo this is a new backend (`src/tts/qwen3_mlx_tts.py`) selected with
`TTS_BACKEND=qwen3-mlx`; the reference clip + transcript live in `voices/sample/`
and are wired through config (`QWEN3_REF_AUDIO` / `QWEN3_REF_TEXT_FILE`). The old
PyTorch path is still there for x86/CUDA fleet hosts.

### Two things that confused me

**"Why does it download something every time?"** Every run prints
`Fetching 12 files...` and a download bar. It's not actually re-downloading - the
weights are cached under `~/.cache/huggingface`. That line is Hugging Face
*resolving/verifying* the snapshot against the cache (notice `84449 it/s` and
`Download complete: 0.00B` - nothing is transferred). To skip the network check
entirely and force fully offline use, set `HF_HUB_OFFLINE=1` (and
`TRANSFORMERS_OFFLINE=1`).

**"The first short reply ("Hallo") I couldn't really hear."** Two reasons.
First, `tools/smoke.py` is a one-shot process: it loads the whole TTS model from
scratch every call (~7s) before it can speak, which is why a tiny "Hallo!" shows
`tts 8873ms` - that's mostly model load, not synthesis. Second, very short
utterances have a high fixed overhead (RTF ~1.3 for one word vs 0.85 for a full
sentence) and the clip is so short it's easy to miss.

Both of these go away with the client/server split (Phase 1): the TTS service
loads the model **once** at startup and stays resident, so per-turn latency is
just generation, and the persistent worker can stream audio chunks so you hear
the first words before the whole clip is done. So yes - this is a one-shot-CLI
artifact, not a real pipeline cost.

## Improvement: stream Qwen3-TTS audio instead of buffering

**Observed:** the 2026-06-17 benchmark run with a long German prompt (~70 words)
recorded **17,162 ms TTS** and a **20,320 ms total**. The model itself is fast
(RTF < 1 on Apple Silicon), but `qwen3_mlx_tts.py` does:

```python
results = list(model.generate(...))   # ← collects ALL chunks first
samples, sr = _collect_audio(results) # ← then writes one big WAV
```

That `list()` call is the problem — it blocks until the full audio is ready
before any sound plays. For a 70-word sentence that means ~17 s of silence
followed by the whole clip at once.

**Why Qwen3-TTS is especially good for robots:** `model.generate()` is already a
generator that yields audio chunks as they are synthesized. Streaming means the
pipeline can hand the first chunk (~100–200 ms of audio) to the speaker while
the rest is still being generated. The robot starts speaking almost immediately —
which is exactly the low-latency feel that makes a voice assistant feel alive.

**What needs to change:**

1. **`Qwen3MlxTTS`** — add a `synthesize_stream()` generator that yields
   `(samples: np.ndarray, sr: int)` chunks directly from `model.generate()`,
   instead of collecting them all with `list()`.
2. **`Pipeline.speak()`** — detect if the TTS backend supports streaming and, if
   so, start piping chunks to the audio output (sounddevice / pyaudio) while
   remaining chunks arrive.
3. **Benchmark** — add a new row with `tts_config = "qwen3-mlx (streaming)"` to
   compare first-audio latency vs the buffered 17 s baseline.

**Expected improvement:** first audio in **~1–2 s** (one synthesized chunk)
instead of 17 s, with the rest of the sentence following seamlessly. TOTAL
could drop from ~20 s to ~5 s for the same long German prompt.

**Tracked as:** Phase 1 / TTS streaming — prerequisite for the robot feeling
responsive. Implement alongside the client/server split so the TTS service stays
resident and streams over the wire.
