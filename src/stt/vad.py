"""Voice-activity detection gate (ADR 0003, Phase A).

Turns a stream of 16-bit PCM frames into speech/no-speech decisions so the
:class:`~src.stt.streaming.StreamingSTT` can detect utterance boundaries without
a push-to-talk button.

Two backends behind one tiny interface (``is_speech(frame_int16) -> bool``):

* ``energy`` (default, **zero new deps**): RMS threshold. Crude but works for a
  close-talking phone mic and adds nothing to install.
* ``silero`` (opt-in, ``VAD_BACKEND=silero``): the Silero VAD ONNX model via
  ``onnxruntime`` -- far more robust to background noise. Falls back to energy
  with a logged warning if the model/onnxruntime is unavailable.

The hangover / start-frame logic lives in :class:`StreamingSTT`, not here; this
module only answers "is this single frame speech?".
"""
from __future__ import annotations

import logging

import numpy as np

from ..config import config

log = logging.getLogger("robot.vad")


class EnergyVad:
    """RMS-threshold VAD. ``frame`` is float32 in [-1, 1]."""

    def __init__(self, threshold: float) -> None:
        self.threshold = threshold

    def is_speech(self, frame: np.ndarray) -> bool:
        if frame.size == 0:
            return False
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        return rms >= self.threshold

    def reset(self) -> None:  # noqa: D401 - stateless
        pass


class SileroVad:
    """Silero VAD (ONNX). Returns per-frame speech probability >= 0.5.

    Silero expects 30+ ms windows at 16 kHz; we feed it whatever frame the
    pipeline uses (32 ms by default) and keep its recurrent state across calls.
    """

    def __init__(self, model_path: str, sample_rate: int) -> None:
        import onnxruntime as ort  # lazy: optional dep

        self._sr = sample_rate
        self._sess = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        # Silero v4 state: (2, 1, 128). v5 uses a single 'state' input; we handle
        # the common v4 layout and degrade gracefully if names differ.
        self._h = np.zeros((2, 1, 128), dtype=np.float32)
        self._c = np.zeros((2, 1, 128), dtype=np.float32)

    def is_speech(self, frame: np.ndarray) -> bool:
        x = frame.astype(np.float32).reshape(1, -1)
        sr = np.array(self._sr, dtype=np.int64)
        try:
            out = self._sess.run(None, {"input": x, "sr": sr, "h": self._h, "c": self._c})
            prob, self._h, self._c = out[0], out[1], out[2]
        except Exception:
            # Model signature mismatch -> single-shot probability, no state.
            out = self._sess.run(None, {"input": x, "sr": sr})
            prob = out[0]
        return float(np.asarray(prob).reshape(-1)[0]) >= 0.5


def make_vad(sample_rate: int):
    """Build the configured VAD, falling back to energy on any failure."""
    if config.vad_backend == "silero":
        try:
            return SileroVad(config.vad_silero_model, sample_rate)
        except Exception as exc:  # noqa: BLE001
            log.warning("Silero VAD unavailable (%s); falling back to energy VAD", exc)
    return EnergyVad(config.vad_threshold)
