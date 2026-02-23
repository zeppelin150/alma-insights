"""
Alma Insights — Generation Animation Widget
Animated overlay shown in the report output area while Gemini generates.

Features:
  • Typewriter-style status messages that type left-to-right then fade
  • Spinning pizza emoji with trailing ellipsis dots
  • Funny rotating quips (SFW) while waiting
  • Elapsed time clock in the bottom-right corner
"""

import random
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGraphicsOpacityEffect,
)
from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, Signal,
)

from src.ui.theme import (
    ALMA_GREEN_DARK, ALMA_GREEN_LIGHT, ALMA_GREEN_SUBTLE,
    ALMA_TEXT_DARK, ALMA_TEXT_MID, ALMA_TEXT_LIGHT,
    ALMA_CREAM, ALMA_WHITE, ALMA_BORDER_LIGHT, ALMA_SUCCESS,
)


# ─── BOOT SEQUENCE STEPS ──────────────────────────────────────────────────────
# Each tuple: (display_text, fake_delay_ms_before_next)
BOOT_STEPS = [
    ("Warming up Node.js runtime...", 1200),
    ("Verifying you're a real human (beep boop)...", 1400),
    ("Attempting to deadlift your enormous data file...", 1800),
    ("Crunching numbers into bite-sized insights...", 1200),
    ("AI overlord is reading your plea...", 1600),
    ("Synthesizing findings — hang tight...", 0),
]

# ─── ROTATING QUIPS (shown while waiting for Gemini) ──────────────────────────
WAITING_QUIPS = [
    "Reticulating splines...",
    "Consulting the oracle...",
    "Polishing the crystal ball...",
    "Herding data cats...",
    "Bribing the algorithm...",
    "Teaching AI to read between the lines...",
    "Converting coffee into insights...",
    "Asking nicely (it helps, trust us)...",
    "Running sentiment analysis on our own patience...",
    "Untangling the spaghetti metrics...",
    "Performing statistical gymnastics...",
    "Negotiating with the cloud...",
    "Counting tickets. So. Many. Tickets...",
    "Aligning the data chakras...",
    "Searching for meaning in the TRC codes...",
    "Generating report. May contain traces of genius...",
    "Almost there (said every loading bar ever)...",
    "Your insights are marinating...",
    "Building a narrative, one stat at a time...",
    "Making the numbers tell their story...",
]


class TypewriterLabel(QLabel):
    """A QLabel that types out text character-by-character, then fades out."""

    typing_done = Signal()

    def __init__(self, parent=None):
        super().__init__("", parent)
        self._full_text = ""
        self._char_index = 0
        self._type_timer = QTimer(self)
        self._type_timer.timeout.connect(self._type_next_char)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._fade_anim.setDuration(800)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)

    def type_text(self, text: str, char_delay_ms: int = 22):
        """Start typing the given text character by character."""
        self._fade_anim.stop()
        self._opacity_effect.setOpacity(1.0)
        self._full_text = text
        self._char_index = 0
        self.setText("")
        self.setVisible(True)
        self._type_timer.start(char_delay_ms)

    def _type_next_char(self):
        if self._char_index < len(self._full_text):
            self._char_index += 1
            self.setText(self._full_text[:self._char_index])
        else:
            self._type_timer.stop()
            self.typing_done.emit()

    def fade_out(self, on_done=None):
        """Fade the label to transparent."""
        if on_done:
            self._fade_anim.finished.connect(on_done)
        self._fade_anim.start()

    def stop(self):
        self._type_timer.stop()
        self._fade_anim.stop()


class SpinnerWidget(QWidget):
    """A spinning pizza emoji with trailing dots and rotating quips."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Pizza spinner
        self._pizza = QLabel("🍕")
        self._pizza.setStyleSheet("font-size: 28px; background: transparent; border: none;")
        layout.addWidget(self._pizza)

        # Dots that trail out from the spinner
        self._dots_label = QLabel("")
        self._dots_label.setStyleSheet(
            f"font-size: 22px; color: {ALMA_GREEN_SUBTLE}; font-weight: 700;"
            " background: transparent; border: none; letter-spacing: 3px;"
        )
        layout.addWidget(self._dots_label)

        # Quip label
        self._quip = QLabel("")
        self._quip.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; font-style: italic;"
            " background: transparent; border: none;"
        )
        layout.addWidget(self._quip)
        layout.addStretch()

        # Animation state
        self._rotation_angles = ["🍕", "🍕", "🍕", "🍕"]  # pizza stays pizza, we rotate the dots
        self._dot_index = 0
        self._dot_patterns = ["", "·", "· ·", "· · ·", "· · · ·", "· · ·", "· ·", "·"]
        self._quip_list = list(WAITING_QUIPS)
        random.shuffle(self._quip_list)
        self._quip_index = 0

        # Dot animation timer
        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._animate_dots)

        # Quip rotation timer
        self._quip_timer = QTimer(self)
        self._quip_timer.timeout.connect(self._next_quip)

        # Pizza spin (rotate between food emojis for visual movement)
        self._spin_frames = ["🍕", "🫠", "🍕", "🧠", "🍕", "⚡", "🍕", "🔮"]
        self._spin_index = 0
        self._spin_timer = QTimer(self)
        self._spin_timer.timeout.connect(self._spin_pizza)

    def start(self):
        self._dot_timer.start(250)
        self._quip_timer.start(4000)
        self._spin_timer.start(600)
        self._next_quip()  # show first quip immediately

    def stop(self):
        self._dot_timer.stop()
        self._quip_timer.stop()
        self._spin_timer.stop()

    def _animate_dots(self):
        self._dot_index = (self._dot_index + 1) % len(self._dot_patterns)
        self._dots_label.setText(self._dot_patterns[self._dot_index])

    def _next_quip(self):
        if self._quip_list:
            self._quip.setText(self._quip_list[self._quip_index % len(self._quip_list)])
            self._quip_index += 1

    def _spin_pizza(self):
        self._spin_index = (self._spin_index + 1) % len(self._spin_frames)
        self._pizza.setText(self._spin_frames[self._spin_index])


class ElapsedClockWidget(QLabel):
    """Bottom-right elapsed time display: ⏱ 0:00"""

    def __init__(self, parent=None):
        super().__init__("⏱  0:00", parent)
        self._seconds = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setStyleSheet(
            f"font-size: 13px; color: {ALMA_TEXT_LIGHT}; font-weight: 600;"
            f" font-family: 'Consolas', 'SF Mono', monospace;"
            " background: transparent; border: none;"
        )

    def start(self):
        self._seconds = 0
        self._update_display()
        self._timer.start(1000)

    def stop(self):
        self._timer.stop()

    def _tick(self):
        self._seconds += 1
        self._update_display()

    def _update_display(self):
        mins = self._seconds // 60
        secs = self._seconds % 60
        self.setText(f"⏱  {mins}:{secs:02d}")


class GenerationAnimationWidget(QWidget):
    """
    Full animation widget shown during report generation.

    Layout:
      ┌──────────────────────────────────────────┐
      │  [boot step 1 — typewriter, then fade]   │
      │  [boot step 2 — typewriter, then fade]   │
      │  ...                                      │
      │  🍕 · · · ·   "Reticulating splines..."  │
      │                                           │
      │                                  ⏱ 1:23  │
      └──────────────────────────────────────────┘
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"background: {ALMA_CREAM}; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 20, 16, 12)
        layout.setSpacing(6)

        # ── Status step area (typewriter lines) ──
        self._step_area = QVBoxLayout()
        self._step_area.setSpacing(4)
        layout.addLayout(self._step_area)

        # ── Spinner + quips (appears after boot steps) ──
        self._spinner = SpinnerWidget()
        self._spinner.setVisible(False)
        layout.addWidget(self._spinner)

        layout.addStretch()

        # ── Elapsed clock (bottom right) ──
        self._clock = ElapsedClockWidget()
        layout.addWidget(self._clock, alignment=Qt.AlignRight | Qt.AlignBottom)

        # Internal state
        self._boot_index = 0
        self._current_label = None
        self._step_timer = QTimer(self)
        self._step_timer.setSingleShot(True)
        self._step_timer.timeout.connect(self._advance_boot)
        self._active = False
        self._all_labels = []

    def start_animation(self):
        """Begin the boot sequence animation."""
        self._active = True
        self._boot_index = 0

        # Clear previous labels
        for lbl in self._all_labels:
            lbl.stop()
            lbl.deleteLater()
        self._all_labels.clear()

        self._spinner.setVisible(False)
        self._clock.start()
        self._advance_boot()

    def stop_animation(self):
        """Stop all animations and clean up."""
        self._active = False
        self._step_timer.stop()
        self._spinner.stop()
        self._clock.stop()
        for lbl in self._all_labels:
            lbl.stop()

    def update_status(self, msg: str):
        """
        Called by the worker's progress signal.
        Injects a new typewriter line if we're past the boot sequence.
        """
        if not self._active:
            return
        if self._boot_index >= len(BOOT_STEPS):
            # We're in the "waiting" phase — update the quip area
            # or inject a dynamic status line
            self._inject_status_line(f"📡  {msg}")

    def _advance_boot(self):
        """Show the next boot step with typewriter effect."""
        if not self._active:
            return

        if self._boot_index >= len(BOOT_STEPS):
            # Boot sequence done — show spinner
            self._spinner.setVisible(True)
            self._spinner.start()
            return

        text, delay = BOOT_STEPS[self._boot_index]
        self._boot_index += 1

        # Create typewriter label
        lbl = TypewriterLabel(self)
        lbl.setStyleSheet(
            f"font-size: 13px; color: {ALMA_GREEN_DARK}; font-weight: 500;"
            " background: transparent; border: none;"
        )
        self._step_area.addWidget(lbl)
        self._all_labels.append(lbl)

        # Wire up: when typing finishes → fade → advance
        prefix = self._step_prefix(self._boot_index)
        lbl.type_text(f"{prefix} {text}")

        if delay > 0:
            lbl.typing_done.connect(lambda d=delay: self._fade_and_advance(lbl, d))
        else:
            # Last step — don't fade, just advance to spinner
            lbl.typing_done.connect(lambda: self._step_timer_fire(800))

    def _fade_and_advance(self, lbl: TypewriterLabel, delay_ms: int):
        """Fade the label, then schedule the next boot step."""
        if not self._active:
            return
        # Start next step while fading
        QTimer.singleShot(delay_ms, self._advance_boot)

    def _step_timer_fire(self, delay_ms: int):
        self._step_timer.start(delay_ms)

    def _step_prefix(self, step_num: int) -> str:
        """Visual prefix for each step: checkmark for completed, arrow for current."""
        return f"✓"  # All steps show checkmark once typed

    def _inject_status_line(self, text: str):
        """Add a dynamic status line during the waiting phase."""
        lbl = TypewriterLabel(self)
        lbl.setStyleSheet(
            f"font-size: 12px; color: {ALMA_TEXT_MID}; font-weight: 400;"
            " background: transparent; border: none;"
        )
        # Insert before the spinner
        idx = self._step_area.count()
        self._step_area.addWidget(lbl)
        self._all_labels.append(lbl)
        lbl.type_text(text, char_delay_ms=15)
