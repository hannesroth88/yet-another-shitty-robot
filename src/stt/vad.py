"""Voice-activity detection gate (ADR 0003, Phase A).

Turns a stream of 16-bit PCM frames into speech/no-speech decisions so the
:class:`~src.stt.streaming.StreamingSTT` can detect utterance boundaries without
a push-to-talk button.

Two backends behind one tiny interface (``is_speech(frame_int16) -> bool``):

* ``energy`` (default, **zero new deps**): Adaptive RMS-threshold VAD.  It
  maintains a slowly-updated noise-floor estimate (EWMA over silent frames) and
  fires only when the current frame's RMS is ``vad_snr_ratio`` × above that
  floor *and* above the hard minimum ``vad_threshold``.  This makes it
  significantly more robust to varying ambient-noise levels than a bare fixed
  threshold.
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
    """Adaptive RMS-threshold VAD.

    Maintains a noise-floor estimate via an exponential moving average (EWMA)
    over frames classified as silent.  A frame is declared speech when its RMS
    exceeds *both*:

    * the hard minimum ``threshold`` (absolute floor, prevents spurious triggers
      in a perfectly quiet room where the noise-floor EWMA is near zero), and
    * ``snr_ratio`` × the current noise-floor estimate (relative gate, adapts
      to varying ambient levels — fans, HVAC, traffic, etc.).

    The noise floor updates only on *silent* frames so that loud speech does not
    raise the floor and cause clipping at the end of an utterance.

    ``frame`` is float32 in [-1, 1].
    """

    # EWMA time constant: how fast the noise floor adapts.
    # ~200 frames at 32 ms each ≈ 6 seconds to track a slow noise change.
    _ALPHA = 0.005

    def __init__(self, threshold: float, snr_ratio: float = 2.5) -> None:
        self.threshold = threshold
        self.snr_ratio = snr_ratio
        self._noise_floor: float = threshold  # start at the hard floor

    def is_speech(self, frame: np.ndarray) -> bool:
        if frame.size == 0:
            return False
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        adaptive_gate = max(self.threshold, self._noise_floor * self.snr_ratio)
        is_speech = rms >= adaptive_gate
        if not is_speech:
            # Update noise floor only during silence so loud speech doesn't
            # inflate the estimate and mask the end of an utterance.
            self._noise_floor = (
                self._ALPHA * rms + (1.0 - self._ALPHA) * self._noise_floor
            )
        return is_speech

    def reset(self) -> None:
        # Keep the learned noise floor across utterances — it reflects the
        # room, not the utterance.
        pass


class SileroVad:
    """Silero VAD using the official ``silero-vad`` PyTorch JIT model.

    The raw ONNX models shipped by the Silero repo have compatibility issues
    with recent onnxruntime versions (near-zero probabilities for real speech).
    The official ``silero-vad`` Python package ships a TorchScript JIT model
    that works correctly and is the recommended inference path.

    ``frame`` must be float32 in [-1, 1], exactly ``vad_frame_ms`` × sr / 1000
    samples (512 samples = 32 ms at 16 kHz is the recommended window).
    """

    def __init__(self, sample_rate: int, threshold: float = 0.5) -> None:
        from silero_vad import load_silero_vad  # lazy: optional dep
        import torch as _torch
        self._torch = _torch
        self._sr = sample_rate
        self._threshold = threshold
        self._model = load_silero_vad()
        self.reset()

    def reset(self) -> None:
        self._model.reset_states()

    def is_speech(self, frame: np.ndarray) -> bool:
        x = self._torch.from_numpy(frame.astype(np.float32))
        with self._torch.no_grad():
            prob = float(self._model(x, self._sr).item())
        return prob >= self._threshold


def make_vad(sample_rate: int):
    """Build the configured VAD, falling back to energy on any failure."""
    if config.vad_backend == "silero":
        try:
            return SileroVad(sample_rate, config.vad_silero_threshold)
        except Exception as exc:  # noqa: BLE001
            log.warning("Silero VAD unavailable (%s); falling back to energy VAD", exc)
    return EnergyVad(config.vad_threshold, config.vad_snr_ratio)
