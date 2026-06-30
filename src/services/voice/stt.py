"""On-device speech-to-text backends (M6).

Every backend runs recognition LOCALLY — audio never leaves the machine. The OS
packages (``winsdk`` on Windows, ``pyobjc`` Speech on macOS) are optional: when a
backend's package isn't importable, ``available()`` returns False and the caller
degrades to a disabled mic (the installer adds the package for the target
platform — M7). Backends are deliberately tiny and side-effect-free at import.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

logger = logging.getLogger("alma.voice")

OnTranscript = Callable[[str], None]
OnState = Callable[[str], None]   # 'listening' | 'idle' | 'error' | 'unavailable'


class SttBackend:
    """Interface. ``start(on_transcript, on_state)`` captures one utterance and
    calls ``on_transcript(text)`` with the result; ``on_state`` reports lifecycle.
    The default backend is the unavailable one (no OS support)."""

    name = "none"

    def available(self) -> bool:
        return False

    def start(self, on_transcript: OnTranscript, on_state: OnState,
              stop_event=None) -> None:
        # ``stop_event`` (a threading.Event) is created by the caller on the GUI
        # thread BEFORE start() runs, so stop() always targets THIS capture.
        on_state("unavailable")

    def stop(self) -> None:
        pass


# ── whisper.cpp (pywhispercpp) — the primary on-device engine ───────
# Fully local (audio never leaves the machine), PyTorch-free, M1-friendly.
# The GGML model is bundled + SHA-256 verified; inference is fed RAW PCM (never a
# file path), so it stays out of the example WAV-loader CVE path and needs no
# ffmpeg. Vetted as the supply-chain-safest on-device option (see workflow).
_WHISPER_MODEL_NAME = "ggml-base.en-q5_1.bin"
_WHISPER_MODEL_SHA256 = "4baf70dd0d7c4247ba2b81fafd9c01005ac77c2f9ef064e00dcf195d0e2fdd2f"
_WHISPER_SAMPLE_RATE = 16000
_WHISPER_MAX_SECONDS = 60   # safety cap if the user never stops the capture


def _whisper_model_path():
    """Resolve the bundled GGML model path (env override → <app>/models/…), or
    None. Local path ONLY — we never pass a bare model name to pywhispercpp
    (which would silently download from HuggingFace, unverified)."""
    import os
    env = os.environ.get("ALMA_WHISPER_MODEL")
    if env and os.path.exists(env):
        return env
    from pathlib import Path
    cand = Path(__file__).resolve().parents[3] / "models" / _WHISPER_MODEL_NAME
    return str(cand) if cand.exists() else None


class _WhisperCppBackend(SttBackend):
    """On-device dictation via whisper.cpp. Push-to-talk: ``start`` captures mic
    audio (sounddevice) until ``stop``, then transcribes the raw PCM locally."""

    name = "whispercpp"

    def __init__(self):
        self._model = None
        self._model_path = None
        self._stop = None

    def available(self) -> bool:
        """Cheap: deps importable + the model file is present. The SHA-256
        integrity gate runs at LOAD time (off the GUI thread) so availability
        never blocks the UI on a 57MB hash — fail-closed is preserved because the
        model is verified before it is ever loaded (see _ensure_model)."""
        if not self._deps_ok():
            return False
        path = _whisper_model_path()
        if not path:
            return False
        self._model_path = path
        return True

    def _deps_ok(self) -> bool:
        try:
            import pywhispercpp.model  # noqa: F401
            import sounddevice  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def _verify_or_raise(self):
        """Fail-closed integrity gate — the bundled model MUST match the pinned
        SHA-256 or we refuse to load it. Raises on mismatch / missing / read
        error (a transient read error is NOT cached, so the next attempt retries
        rather than disabling the mic for the session)."""
        import hashlib
        path = self._model_path or _whisper_model_path()
        if not path:
            raise RuntimeError("voice model not found")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != _WHISPER_MODEL_SHA256:
            raise RuntimeError(f"voice model SHA-256 mismatch — refusing to load: {path}")
        self._model_path = path

    def _ensure_model(self):
        if self._model is None:
            import os
            self._verify_or_raise()   # fail-closed BEFORE any load
            from pywhispercpp.model import Model
            # Local verified path ONLY — never a bare name (no runtime download).
            # whisper.cpp's C-level load logs are redirected via os.dup2, which
            # needs a REAL fd → send them to devnull (kept open for the model's
            # lifetime). print_realtime/progress=False silence the runtime spam.
            self._log_sink = open(os.devnull, "w")
            self._model = Model(model=self._model_path, n_threads=4,
                                print_realtime=False, print_progress=False,
                                redirect_whispercpp_logs_to=self._log_sink)
        return self._model

    def transcribe_pcm(self, audio) -> str:
        """Transcribe a 16kHz mono float32 numpy buffer locally → text (verifies
        the model integrity before the first load)."""
        segments = self._ensure_model().transcribe(audio)
        return " ".join((s.text or "").strip() for s in segments).strip()

    def start(self, on_transcript, on_state, stop_event=None):
        if not self.available():
            on_state("unavailable")
            return
        import threading
        import numpy as np
        import sounddevice as sd

        # The caller (VoiceController, GUI thread) creates the Event BEFORE this
        # runs, so stop() always targets THIS capture (no create-on-daemon race).
        stop = stop_event if stop_event is not None else threading.Event()
        self._stop = stop
        frames = []

        def _cb(indata, _n, _t, _status):
            frames.append(indata.copy())

        try:
            on_state("listening")
            with sd.InputStream(samplerate=_WHISPER_SAMPLE_RATE, channels=1,
                                dtype="float32", callback=_cb):
                stop.wait(timeout=_WHISPER_MAX_SECONDS)
            on_state("transcribing")
            if not frames:
                on_state("idle")
                return
            audio = np.concatenate(frames, axis=0).reshape(-1).astype(np.float32)
            text = self.transcribe_pcm(audio)
            if text:
                on_transcript(text)
            on_state("idle")
        except Exception as exc:  # noqa: BLE001 — never crash the chat over dictation
            logger.warning("whisper.cpp dictation failed: %s", exc)
            on_state("error")

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()


def _winrt_speech():
    """The WinRT speech-recognition module from whichever projection is installed
    — PyWinRT ``winrt.*`` (Python 3.13 wheels) or the older ``winsdk.*`` — or None."""
    import importlib
    for mod in ("winrt.windows.media.speechrecognition",
                "winsdk.windows.media.speechrecognition"):
        try:
            return importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            continue
    return None


def _winrt_init_apartment():
    """Best-effort COM apartment init for the calling thread (PyWinRT needs it
    before any WinRT call; older winsdk inits implicitly)."""
    try:
        from winrt.runtime import init_apartment
        init_apartment()
    except Exception:  # noqa: BLE001
        pass


class _WinRtBackend(SttBackend):
    """Windows free-dictation via WinRT ``SpeechRecognizer`` (single utterance).
    **NOT on-device:** the free-dictation grammar uses Microsoft's ONLINE speech
    service when Windows "Online speech recognition" is enabled, so audio leaves
    the machine. **Never auto-selected** (see ``select_backend``) — kept only for
    a possible future EXPLICIT, consented, non-PHI opt-in."""

    name = "winrt"

    def available(self) -> bool:
        return _winrt_speech() is not None

    def start(self, on_transcript, on_state, stop_event=None):
        sr = _winrt_speech()
        if sr is None:
            on_state("unavailable")
            return
        import asyncio

        async def _recognize():
            recognizer = sr.SpeechRecognizer()
            await recognizer.compile_constraints_async()
            on_state("listening")            # mic is now live (push-to-talk)
            return await recognizer.recognize_async()

        try:
            _winrt_init_apartment()
            result = asyncio.run(_recognize())
            status = getattr(result, "status", None)
            text = getattr(result, "text", "") or ""
            success = sr.SpeechRecognitionResultStatus.SUCCESS
            if status == success and text:
                on_transcript(text)
            elif status is not None and status != success:
                logger.info("WinRT recognition ended with status=%s", status)
            on_state("idle")
        except Exception as exc:  # noqa: BLE001 — never crash the chat over dictation
            logger.warning("WinRT dictation failed: %s", exc)
            on_state("error")

    def stop(self) -> None:
        # ``recognize_async`` is single-shot and self-terminates on silence, so
        # there is nothing to cancel for the push-to-talk model.
        pass


class _DarwinBackend(SttBackend):
    """macOS on-device dictation via Speech.framework (``SFSpeechRecognizer`` with
    ``requiresOnDeviceRecognition = True``). The audio-engine + authorization wiring
    must be finished and verified on real hardware; until then this reports
    unavailable so the mic stays disabled rather than failing mid-utterance."""

    name = "darwin"

    def available(self) -> bool:
        # Gated off pending on-device verification (no Mac in this environment).
        # Flip to a real Speech import check when the engine path is implemented.
        return False

    def start(self, on_transcript, on_state, stop_event=None):
        on_state("unavailable")


def select_backend() -> SttBackend:
    """The STT backend to use. ONLY the vetted, fully-on-device whisper.cpp engine
    is ever auto-selected — for a HIPAA app we must NEVER silently fall back to a
    cloud recognizer (WinRT free-dictation is Microsoft cloud). When whisper.cpp
    or its verified model isn't present, the mic stays disabled. The WinRT/Darwin
    backends remain in the module only for a future EXPLICIT, consented opt-in."""
    whisper = _WhisperCppBackend()
    if whisper.available():
        return whisper
    return SttBackend()


def backend_status() -> dict:
    b = select_backend()
    try:
        avail = bool(b.available())
    except Exception:  # noqa: BLE001
        avail = False
    return {"platform": sys.platform, "backend": b.name, "available": avail}
