"""Parakeet STT backend candidate (Phase 1, opt-in via ``STT_BACKEND=parakeet``).

NVIDIA Parakeet TDT 0.6B is an evaluation alternative to faster-whisper (see the
open question in AGENTS.md). It stays optional so faster-whisper remains the
zero-friction default; the heavy deps are imported lazily with a clear hint.

Two runtimes are tried, in order:
* ``parakeet-mlx`` -- Apple-Silicon path (fast on the M1 dev host);
* ``nemo_toolkit`` -- the reference NeMo path (CUDA on the x86 fleet).

Install (Mac dev):   pip install parakeet-mlx
Install (x86/CUDA):  pip install -U nemo_toolkit[asr]

Note: use the v3 model (default) for German -- it is multilingual (25 European
languages). The older v2 is English-only. Keep it behind a benchmark before
adopting it as the pipeline default.
"""
from __future__ import annotations

from ..config import config


class ParakeetSTT:
    def __init__(self) -> None:
        self._impl = self._load()

    def _load(self):
        # Apple-Silicon MLX path first (the current dev host).
        try:
            from parakeet_mlx import from_pretrained  # type: ignore

            model = from_pretrained(config.parakeet_model)
            return ("mlx", model)
        except ImportError:
            pass
        try:
            import nemo.collections.asr as nemo_asr  # type: ignore

            model = nemo_asr.models.ASRModel.from_pretrained(config.parakeet_model)
            return ("nemo", model)
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ImportError(
                "Parakeet backend needs 'parakeet-mlx' (Apple Silicon) or "
                "'nemo_toolkit[asr]' (CUDA/x86). Install one, or use "
                "STT_BACKEND=faster-whisper."
            ) from exc

    def transcribe(self, wav_path: str) -> str:
        kind, model = self._impl
        if kind == "mlx":
            result = model.transcribe(wav_path)
            text = getattr(result, "text", None)
            return (text if text is not None else str(result)).strip()
        # nemo
        out = model.transcribe([wav_path])
        first = out[0] if out else ""
        text = getattr(first, "text", first)
        return str(text).strip()
