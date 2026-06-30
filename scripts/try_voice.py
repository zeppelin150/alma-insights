"""Manual test harness for the Agent's on-device voice dictation (M6).

Drives the REAL VoiceController (same object the chat bridge uses) against your
mic — speak a sentence and it prints the transcript, then listens again. This is
the on-hardware check the automated suite can't do (no mic in CI).

    python scripts/try_voice.py

Notes:
  • Windows needs mic access for desktop apps: Settings → Privacy & security →
    Microphone → "Let desktop apps access your microphone" = On. The first run
    may pop a consent prompt.
  • Recognition is single-utterance: it listens until a natural pause, returns
    the text, then re-arms. Ctrl+C to quit.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, QTimer

from src.services.voice import VoiceController


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QCoreApplication(sys.argv)
    vc = VoiceController()
    print(f"backend: {vc.backend_name} | available: {vc.available}")
    if not vc.available:
        print("Voice backend unavailable — install the WinRT speech projection:\n"
              '  pip install "winrt-Windows.Media.SpeechRecognition" '
              '"winrt-Windows.Foundation" "winrt-Windows.Globalization"')
        return 1

    def on_state(state: str) -> None:
        print(f"  [{state}]")
        if state in ("idle", "error"):
            QTimer.singleShot(400, vc.start)   # re-arm for the next utterance

    def on_transcript(text: str) -> None:
        print(f"\n  YOU SAID: {text}\n")

    vc.state_changed.connect(on_state)
    vc.transcript.connect(on_transcript)

    print("\nListening — speak a sentence (Ctrl+C to quit)…")
    QTimer.singleShot(200, vc.start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
