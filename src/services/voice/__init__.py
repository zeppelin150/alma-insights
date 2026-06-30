"""On-device voice dictation for the Agent chat (M6).

Push-to-talk speech-to-text that runs entirely LOCALLY — audio never leaves the
machine (HIPAA-safe, no cloud, no BAA). The recognizer engine is platform
specific (Windows WinRT / macOS Speech.framework) and its OS package is optional
at import time; when absent the backend reports ``available() == False`` and the
UI degrades to a disabled mic. ``VoiceController`` is the Qt-facing surface the
bridge talks to.
"""

from .stt import SttBackend, select_backend, backend_status  # noqa: F401
from .controller import VoiceController  # noqa: F401
