"""Download the German Kokoro "Martin" ONNX model + voices into voices/.

    pip install kokoro-onnx huggingface_hub
    python -m utils.fetch_kokoro

Pulls kokoro-martin.onnx and voices-martin.npz from the Hugging Face repo and
places them where config.py expects (voices/). Re-running is cheap (hf cache).
"""
from __future__ import annotations

import os

# Force the classic HTTP downloader: the default hf-xet (Rust) transfer stalls at
# 0% on some networks/proxies. Must be set BEFORE importing huggingface_hub.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import shutil
from pathlib import Path

REPO_ID = "Godelaune/Kokoro-82M-ONNX-German-Martin"
FILES = ["kokoro-martin.onnx", "voices-martin.npz"]
DEST = Path(__file__).resolve().parent.parent / "voices"


def main() -> None:
    from huggingface_hub import hf_hub_download

    DEST.mkdir(parents=True, exist_ok=True)
    for filename in FILES:
        cached = hf_hub_download(repo_id=REPO_ID, filename=filename)
        target = DEST / filename
        if not target.exists():
            shutil.copy(cached, target)
        print(f"{filename} -> {target}")
    print("done. Set TTS_BACKEND=kokoro in .env")


if __name__ == "__main__":
    main()
