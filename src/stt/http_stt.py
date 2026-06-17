"""HTTP STT backend: POST audio to a remote STT service, get a transcript back
(Phase 1 transport seam; Phase 2 runs STT on the always-on NUC).

Selected via ``STT_BACKEND=http`` + ``STT_HTTP_URL``. The wire format is a simple
multipart POST of the wav file to ``/transcribe`` returning ``{"text": "..."}``,
which the matching server side in ``services/stt_server`` (Phase 2) implements.
This is deliberately not Wyoming yet -- plain HTTP keeps the seam debuggable; we
revisit Wyoming when Home Assistant (Phase 3) pulls it forward.
"""
from __future__ import annotations

import json
import urllib.request
import uuid
from pathlib import Path

from ..config import config


class HttpSTT:
    def __init__(self) -> None:
        base = config.stt_http_url.rstrip("/")
        self.url = f"{base}/transcribe"

    def transcribe(self, wav_path: str) -> str:
        body, content_type = _multipart_wav(wav_path, language=config.stt_language)
        req = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        return (obj.get("text") or "").strip()


def _multipart_wav(wav_path: str, language: str | None) -> tuple[bytes, str]:
    boundary = f"----robot{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    if language:
        parts += [
            f"--{boundary}".encode(),
            b'Content-Disposition: form-data; name="language"',
            b"",
            language.encode(),
        ]
    data = Path(wav_path).read_bytes()
    parts += [
        f"--{boundary}".encode(),
        (
            'Content-Disposition: form-data; name="audio"; '
            f'filename="{Path(wav_path).name}"'
        ).encode(),
        b"Content-Type: audio/wav",
        b"",
    ]
    body = crlf.join(parts) + crlf + data + crlf + f"--{boundary}--".encode() + crlf
    return body, f"multipart/form-data; boundary={boundary}"
