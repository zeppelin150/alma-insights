"""VoiceController — the Qt-facing surface for on-device dictation (M6).

Owns an :class:`~src.services.voice.stt.SttBackend` and exposes push-to-talk
``start()`` / ``stop()`` plus Qt signals the bridge re-emits. Recognition runs on
a daemon thread so the GUI never blocks; ``transcript`` / ``state_changed`` are
emitted from that thread and delivered to the main thread by Qt's queued
connections (the controller lives on the main thread).
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from .stt import select_backend

logger = logging.getLogger("alma.voice")


class VoiceController(QObject):
    transcript = Signal(str)       # a recognized utterance
    state_changed = Signal(str)    # 'listening' | 'transcribing' | 'idle' | 'error' | 'unavailable'

    def __init__(self, backend=None, parent=None):
        super().__init__(parent)
        self._backend = backend if backend is not None else select_backend()
        self._thread = None
        self._stop = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        try:
            return bool(self._backend.available())
        except Exception:  # noqa: BLE001
            return False

    @property
    def backend_name(self) -> str:
        return getattr(self._backend, "name", "none")

    def start(self) -> None:
        """Begin capturing one utterance (push-to-talk). No-op if unavailable or
        already listening."""
        if not self.available:
            self.state_changed.emit("unavailable")
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # Create the stop Event on the GUI thread BEFORE the worker runs, so
            # stop() always signals THIS capture (no create-on-daemon-thread race
            # where a stop click could be dropped and the mic record to the cap).
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(self._stop,), daemon=True)
            self._thread.start()

    def _run(self, stop_event) -> None:
        try:
            self._backend.start(self._on_transcript, self._on_state, stop_event=stop_event)
        except Exception as exc:  # noqa: BLE001 — dictation must never crash the app
            logger.warning("voice backend error: %s", exc)
            self.state_changed.emit("error")

    def _on_transcript(self, text: str) -> None:
        if text:
            self.transcript.emit(text)

    def _on_state(self, state: str) -> None:
        self.state_changed.emit(state)

    def stop(self) -> None:
        with self._lock:
            ev = self._stop
        if ev is not None:        # signal the live capture directly (race-free)
            ev.set()
        try:
            self._backend.stop()  # backends that self-manage their own capture
        except Exception:  # noqa: BLE001
            pass
