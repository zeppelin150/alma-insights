"""
Tests for Phase 3.5 — Source Abstraction Interfaces

Covers:
  - SourceConfig dataclass round-trip
  - SourceClient ABC contract satisfaction by ZendeskClient
  - SourceMonitor base class contract satisfaction by ZendeskMonitor
  - source_name property returns correct values
  - Signal declarations on SourceMonitor
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════
#  SourceConfig Tests
# ═══════════════════════════════════════

class TestSourceConfig:
    """Verify SourceConfig dataclass."""

    def test_basic_construction(self):
        from src.data.source_types import SourceConfig
        cfg = SourceConfig(
            name="zendesk", display_name="Zendesk Support",
            icon="📡", is_configured=True, is_connected=False
        )
        assert cfg.name == "zendesk"
        assert cfg.display_name == "Zendesk Support"
        assert cfg.icon == "📡"
        assert cfg.is_configured is True
        assert cfg.is_connected is False

    def test_default_values(self):
        from src.data.source_types import SourceConfig
        cfg = SourceConfig(name="jira", display_name="Jira")
        assert cfg.icon == ""
        assert cfg.is_configured is False
        assert cfg.is_connected is False
        assert cfg.extra == {}

    def test_extra_dict(self):
        from src.data.source_types import SourceConfig
        cfg = SourceConfig(
            name="intercom", display_name="Intercom",
            extra={"workspace_id": "abc123"}
        )
        assert cfg.extra["workspace_id"] == "abc123"

    def test_round_trip_equality(self):
        from src.data.source_types import SourceConfig
        a = SourceConfig(name="x", display_name="X", is_configured=True)
        b = SourceConfig(name="x", display_name="X", is_configured=True)
        assert a == b


# ═══════════════════════════════════════
#  SourceClient ABC Tests
# ═══════════════════════════════════════

class TestSourceClientABC:
    """Verify ZendeskClient satisfies SourceClient ABC."""

    def test_isinstance(self):
        from src.data.source_types import SourceClient
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        assert isinstance(client, SourceClient)

    def test_source_name_property(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        assert client.source_name == "zendesk"

    def test_is_configured_property(self):
        from src.data.zendesk_client import ZendeskClient
        assert ZendeskClient("a", "b", "c").is_configured is True
        assert ZendeskClient("", "", "").is_configured is False

    def test_has_test_connection(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        assert callable(getattr(client, "test_connection", None))

    def test_has_fetch_incremental(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        assert callable(getattr(client, "fetch_incremental", None))

    def test_has_extract_trc(self):
        from src.data.zendesk_client import ZendeskClient
        client = ZendeskClient("acme", "user@co.com", "tok")
        assert callable(getattr(client, "extract_trc", None))

    def test_abc_enforcement(self):
        """Verify SourceClient cannot be instantiated directly."""
        from src.data.source_types import SourceClient
        with pytest.raises(TypeError):
            SourceClient()


# ═══════════════════════════════════════
#  SourceMonitor Base Tests
# ═══════════════════════════════════════

class TestSourceMonitorBase:
    """Verify SourceMonitor base class and ZendeskMonitor inheritance."""

    @pytest.fixture(autouse=True)
    def _patch_qt(self):
        """Stub out QTimer so we don't need a running QApplication."""
        with patch("src.data.zendesk_monitor.QTimer") as mock_timer:
            mock_timer.return_value = MagicMock()
            yield

    def test_isinstance(self):
        from src.data.source_types import SourceMonitor
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)
        assert isinstance(monitor, SourceMonitor)

    def test_source_name_zendesk(self):
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        monitor = ZendeskMonitor(db)
        assert monitor.source_name == "zendesk"

    def test_has_signals(self):
        from src.data.source_types import SourceMonitor
        # Verify signal declarations exist on the class
        assert hasattr(SourceMonitor, "records_received")
        assert hasattr(SourceMonitor, "spike_detected")
        assert hasattr(SourceMonitor, "alert_fired")
        assert hasattr(SourceMonitor, "status_changed")

    def test_base_start_raises(self):
        """SourceMonitor.start() should raise NotImplementedError."""
        from src.data.source_types import SourceMonitor
        monitor = SourceMonitor.__new__(SourceMonitor)
        with pytest.raises(NotImplementedError):
            monitor.start()

    def test_base_pause_raises(self):
        from src.data.source_types import SourceMonitor
        monitor = SourceMonitor.__new__(SourceMonitor)
        with pytest.raises(NotImplementedError):
            monitor.pause()

    def test_base_resume_raises(self):
        from src.data.source_types import SourceMonitor
        monitor = SourceMonitor.__new__(SourceMonitor)
        with pytest.raises(NotImplementedError):
            monitor.resume()

    def test_base_stop_raises(self):
        from src.data.source_types import SourceMonitor
        monitor = SourceMonitor.__new__(SourceMonitor)
        with pytest.raises(NotImplementedError):
            monitor.stop()

    def test_base_source_name_raises(self):
        from src.data.source_types import SourceMonitor
        monitor = SourceMonitor.__new__(SourceMonitor)
        with pytest.raises(NotImplementedError):
            _ = monitor.source_name

    def test_base_status_raises(self):
        from src.data.source_types import SourceMonitor
        monitor = SourceMonitor.__new__(SourceMonitor)
        with pytest.raises(NotImplementedError):
            _ = monitor.status

    def test_zendesk_monitor_accepts_warehouse_watchlist(self):
        """ZendeskMonitor.__init__ accepts warehouse and watchlist params."""
        from src.data.zendesk_monitor import ZendeskMonitor
        db = MagicMock()
        wh = MagicMock()
        wl = MagicMock()
        monitor = ZendeskMonitor(db, warehouse=wh, watchlist=wl)
        assert monitor._warehouse is wh
        assert monitor._watchlist is wl
