"""Fetch the on-device STT model the Agent's dictation bundles (M6).

Downloads the pinned whisper.cpp GGML model into ``models/`` and verifies its
SHA-256 (fail-closed). Idempotent. Run before packaging — ``build_release.py``
bundles ``models/*.bin`` — or for local dictation. The model is gitignored (57MB,
too big to commit); this script is the reproducible, integrity-checked fetch.

    python scripts/fetch_voice_model.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

MODEL = "ggml-base.en-q5_1.bin"
URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + MODEL
# Pinned digest — keep in sync with src/services/voice/stt.py::_WHISPER_MODEL_SHA256.
SHA256 = "4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f"
DEST = Path(__file__).resolve().parents[1] / "models" / MODEL


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    DEST.parent.mkdir(parents=True, exist_ok=True)
    if DEST.exists() and _sha256(DEST) == SHA256:
        print(f"OK (cached, verified): {DEST}")
        return 0
    print(f"Downloading {URL}\n         -> {DEST}")
    urllib.request.urlretrieve(URL, DEST)
    got = _sha256(DEST)
    if got != SHA256:
        DEST.unlink(missing_ok=True)
        print(f"SHA-256 MISMATCH: got {got}, expected {SHA256}", file=sys.stderr)
        return 1
    print(f"OK: {DEST} ({DEST.stat().st_size} bytes, sha256 verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
