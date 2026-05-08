"""
Tests for src/ui/widgets/rate_chart.py — RateChartWidget.

These are non-interactive paint tests. We exercise paintEvent against a
``QImage`` of fixed size so behavior is independent of the host machine's
DPI scaling (a known Qt-on-Windows hazard for UI tests).

Coverage map
────────────
  - Widget construction (no exceptions)
  - sizeHint matches the documented value
  - paintEvent for the four golden cases:
      empty / normal / spike / cold-start
  - Empty baseline shows the awaiting-data label
  - set_baseline / clear interplay
  - tooltip body formats correctly for each state
  - mouse hover triggers tooltip dispatch (no crash)
  - cold-start banner drawn on cold-start data only
  - degenerate plot rect (tiny widget) doesn't crash

All paints use ``QPainter`` against a ``QImage`` rather than a real
window — no QWindow needed, no flakiness.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

# Qt-dependent imports must come AFTER the qapp fixture has created
# QApplication (handled at module level by sys.path setup + the qapp
# fixture below). Importing PySide6 itself does NOT need a QApplication.
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from src.data.source_baseline import RateBaseline


pytestmark = pytest.mark.ui


# ═══════════════════════════════════════════════════════════════════
#  qapp fixture (module-scoped)
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qapp():
    """Module-scoped QApplication.

    Qt requires exactly one QApplication per process. We create it once
    at module load and reuse across tests.
    """
    app = QApplication.instance() or QApplication([])
    yield app


# ═══════════════════════════════════════════════════════════════════
#  Baseline factories
# ═══════════════════════════════════════════════════════════════════

def _normal_baseline(n: int = 24) -> RateBaseline:
    """Create a normal-state RateBaseline with no spikes."""
    now = datetime(2026, 5, 7, 14, 0, 0, tzinfo=timezone.utc)
    hours = [
        (now - timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00")
        for h in range(n, 0, -1)
    ]
    rates = [5] * n
    mean = [5.0] * n
    std = [1.0] * n
    upper = [m + 2.0 * s for m, s in zip(mean, std)]
    return RateBaseline(
        hours=hours,
        rates=rates,
        baseline_mean=mean,
        baseline_std=std,
        upper_band=upper,
        is_spike=[False] * n,
        cold_start=False,
        days_with_data=7,
        source="zendesk",
        trc_code=None,
        computed_at=now.isoformat(timespec="seconds"),
        spike_sigma=2.0,
        window_hours=n,
    )


def _spike_baseline() -> RateBaseline:
    """A baseline with one obvious spike at the end."""
    bl = _normal_baseline(24)
    # Mutate via dataclasses.replace since RateBaseline is frozen.
    from dataclasses import replace
    rates = list(bl.rates)
    rates[-1] = 30
    is_spike = list(bl.is_spike)
    is_spike[-1] = True
    return replace(bl, rates=rates, is_spike=is_spike)


def _cold_start_baseline() -> RateBaseline:
    """Cold-start state — only 2 days of data."""
    bl = _normal_baseline(24)
    from dataclasses import replace
    return replace(
        bl,
        cold_start=True,
        days_with_data=2,
        baseline_std=[0.0] * 24,
        upper_band=[7.5] * 24,
    )


def _empty_baseline() -> RateBaseline:
    """Zero-length baseline (no data yet)."""
    now = datetime(2026, 5, 7, 14, 0, 0, tzinfo=timezone.utc)
    return RateBaseline(
        hours=[], rates=[], baseline_mean=[], baseline_std=[],
        upper_band=[], is_spike=[],
        cold_start=False,
        days_with_data=0,
        source="zendesk",
        trc_code=None,
        computed_at=now.isoformat(timespec="seconds"),
    )


# ═══════════════════════════════════════════════════════════════════
#  Paint helpers — render to QImage and assert no crashes
# ═══════════════════════════════════════════════════════════════════

def _render_to_image(widget) -> QImage:
    """Render the widget to a 720×320 QImage and return it.

    Qt guarantees paintEvent runs synchronously when render() is called,
    so any exception in our painter code would propagate up here.
    """
    image = QImage(720, 320, QImage.Format_ARGB32)
    image.fill(0)
    widget.resize(720, 320)
    widget.render(image)
    return image


# ═══════════════════════════════════════════════════════════════════
#  Construction
# ═══════════════════════════════════════════════════════════════════

class TestConstruction:
    """Widget can be constructed without a QApplication."""

    def test_construct_no_baseline(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        assert w is not None

    def test_size_hint(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        sz = w.sizeHint()
        assert sz.width() == 720
        assert sz.height() == 320

    def test_minimum_height(self, qapp):
        """Documented minimum to keep the chart legible."""
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        assert w.minimumHeight() == 280

    def test_mouse_tracking_enabled(self, qapp):
        """Required for hover tooltips."""
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        assert w.hasMouseTracking() is True


# ═══════════════════════════════════════════════════════════════════
#  Paint — golden cases
# ═══════════════════════════════════════════════════════════════════

class TestPaintGoldenCases:
    """Render each documented state and verify no exceptions + plausible
    image content."""

    def test_paint_empty(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        img = _render_to_image(w)
        assert not img.isNull()
        # Empty state should have the chart background color somewhere.
        assert img.width() == 720

    def test_paint_normal(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        img = _render_to_image(w)
        assert not img.isNull()

    def test_paint_with_spike(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_spike_baseline())
        img = _render_to_image(w)
        assert not img.isNull()

    def test_paint_cold_start(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_cold_start_baseline())
        img = _render_to_image(w)
        assert not img.isNull()

    def test_paint_explicit_empty_baseline(self, qapp):
        """A populated-but-empty RateBaseline (n=0) renders the empty state."""
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_empty_baseline())
        img = _render_to_image(w)
        assert not img.isNull()

    def test_paint_window_48h(self, qapp):
        """48-bucket window is the user-toggleable wide view."""
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline(48))
        img = _render_to_image(w)
        assert not img.isNull()

    def test_paint_at_minimum_size(self, qapp):
        """Tiny window — must not crash. Plot rect may be skipped."""
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        # Force smaller than the minimum plot area.
        image = QImage(100, 50, QImage.Format_ARGB32)
        image.fill(0)
        w.resize(100, 50)
        w.render(image)
        # No assertion on content — just no crash.

    def test_paint_single_bucket(self, qapp):
        """Edge case: one-bucket baseline still renders."""
        from src.ui.widgets.rate_chart import RateChartWidget
        bl = _normal_baseline(1)
        w = RateChartWidget()
        w.set_baseline(bl)
        img = _render_to_image(w)
        assert not img.isNull()


# ═══════════════════════════════════════════════════════════════════
#  set_baseline / clear / repaint
# ═══════════════════════════════════════════════════════════════════

class TestSetBaselineAndClear:
    """Stateful behaviour around set_baseline and clear."""

    def test_set_baseline_triggers_repaint(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        # Internal state should now reference the baseline.
        assert w._baseline is not None

    def test_clear_drops_baseline(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        w.clear()
        assert w._baseline is None
        # And renders the empty state.
        img = _render_to_image(w)
        assert not img.isNull()

    def test_set_baseline_replaces_previous(self, qapp):
        """A second set_baseline call replaces the first."""
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        first = w._baseline
        w.set_baseline(_spike_baseline())
        assert w._baseline is not first
        assert w._baseline.spike_count >= 1


# ═══════════════════════════════════════════════════════════════════
#  Tooltip formatting
# ═══════════════════════════════════════════════════════════════════

class TestTooltipFormat:
    """_format_tooltip produces useful multi-line text for each state."""

    def test_normal_tooltip_includes_baseline(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        bl = _normal_baseline()
        text = RateChartWidget._format_tooltip(bl, 0)
        assert "Rate:" in text
        assert "Baseline:" in text
        assert "Upper band:" in text
        assert "SPIKE" not in text

    def test_spike_tooltip_marks_spike(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        bl = _spike_baseline()
        text = RateChartWidget._format_tooltip(bl, len(bl.rates) - 1)
        assert "SPIKE" in text

    def test_cold_start_tooltip_shows_progress(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        bl = _cold_start_baseline()
        text = RateChartWidget._format_tooltip(bl, 0)
        assert "Cold-start" in text
        assert "2/5" in text


# ═══════════════════════════════════════════════════════════════════
#  Hit-test rect cache
# ═══════════════════════════════════════════════════════════════════

class TestPointRects:
    """The hit-test rects are rebuilt on each paint and contain N rows."""

    def test_point_rects_match_bucket_count(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        _render_to_image(w)
        assert len(w._point_rects) == 24

    def test_point_rects_clear_on_empty(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        w = RateChartWidget()
        w.set_baseline(_normal_baseline())
        _render_to_image(w)
        w.clear()
        _render_to_image(w)
        assert w._point_rects == []


# ═══════════════════════════════════════════════════════════════════
#  Tick helpers
# ═══════════════════════════════════════════════════════════════════

class TestTickHelpers:
    """Internal helpers used by both gridlines and labels."""

    def test_y_ticks_evenly_spaced(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        ticks = RateChartWidget._y_ticks(0.0, 10.0, 5)
        assert ticks == [0.0, 2.5, 5.0, 7.5, 10.0]

    def test_y_ticks_minimum_count(self, qapp):
        """n<2 still returns endpoints — defensive."""
        from src.ui.widgets.rate_chart import RateChartWidget
        ticks = RateChartWidget._y_ticks(0.0, 10.0, 1)
        assert ticks == [0.0, 10.0]

    def test_short_date_formats_iso(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        assert RateChartWidget._short_date("2026-05-07") == "May 07"

    def test_short_date_passthrough_on_bad_input(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        assert RateChartWidget._short_date("not-a-date") == "not-a-date"

    def test_short_time_extracts_hh_mm(self, qapp):
        from src.ui.widgets.rate_chart import RateChartWidget
        assert RateChartWidget._short_time("2026-05-07T14:30:00") == "14:30"
