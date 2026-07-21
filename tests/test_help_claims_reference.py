"""Accuracy audit of the in-app Help Center — ``reference`` section.

Every test here settles ONE falsifiable claim made by an article under
``assets/help/reference/``:

  * ``glossary.md``            — reference-glossary
  * ``settings-keys.md``       — reference-settings-keys
  * ``version-and-data.md``    — reference-version-and-data
  * ``what-renn-can-write.md`` — reference-what-renn-can-write

Each test's docstring names the article and quotes (or closely paraphrases)
the claim under test. Tests marked ``xfail(strict=True)`` assert the
ARTICLE's claim while the code does something else — the reason string says
what the code actually does. A strict xfail that starts PASSING means the
code changed to match the article and the marker should be dropped.

Headless: no network, no credentials, no QtWebEngine. Settings are always
redirected to a tmp file so the developer's own ``data/settings.yaml``
can never change a result.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HELP_DIR = PROJECT_ROOT / "assets" / "help" / "reference"


# ══════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def qapp():
    """One QApplication for the whole module (Qt widgets need exactly one)."""
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    """Redirect settings_manager at a tmp settings.yaml.

    Returns a ``write(dict)`` callable. Without this, every ``get_section``
    call would read the developer's live ``data/settings.yaml`` and the
    defaults under test would be masked by real values.
    """
    from src.data import settings_manager as sm

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(sm, "_DATA_DIR", data_dir)
    monkeypatch.setattr(sm, "_CONFIG_DIR", tmp_path / "config-absent")
    path = data_dir / "settings.yaml"

    def write(cfg: dict) -> Path:
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return path

    write({})
    return write


@pytest.fixture
def conn(empty_db):
    """Raw sqlite3 connection to a fully-initialised empty warehouse."""
    return empty_db.conn


# ══════════════════════════════════════════════════════════════════════
#  glossary.md — reference-glossary
# ══════════════════════════════════════════════════════════════════════

class TestGlossary:
    """Claims from ``assets/help/reference/glossary.md``."""

    def test_draft_is_held_in_the_app_and_reaches_nobody_until_published(self, conn):
        """GLOSSARY: "Draft — an unpublished working copy of a card, held
        inside the app... Nothing in a draft reaches Guru until you publish."

        Creating a draft must write a local row and leave it in a
        not-yet-published state; publishing is a separate tool.
        """
        from src.data import enablement_store as store

        draft_id = store.save_card_draft(conn, title="Prior auth refresh",
                                         content="# Prior auth\nbody")
        row = conn.execute(
            "SELECT title, status FROM guru_content_drafts WHERE id = ?",
            (draft_id,)).fetchone()
        assert row is not None, "creating a draft wrote no local row"
        assert row["title"] == "Prior auth refresh"
        assert row["status"] != "published", (
            "a freshly created draft must not already be published")

        # Publishing is a distinct, separately-invoked tool.
        from src.data.chat_tools.registry import get_tool_registry
        assert "push_guru_draft" in get_tool_registry()

    def test_workspace_keeps_its_own_unsaved_edits_across_a_switch(self, qapp):
        """GLOSSARY: "Each workspace keeps its own unsaved edits, view and
        cursor position, so switching away and back does not lose your place."
        """
        from src.ui.pages.enablement.workbench import WorkbenchPage

        page = WorkbenchPage()
        try:
            page.switch_workspace(1, card={"title": "A", "content": "# A"})
            page._current_md = "# A edited but unsaved"
            page.switch_workspace(2, card={"title": "B", "content": "# B"})
            assert page._current_md != "# A edited but unsaved", (
                "switching workspaces did not change the canvas")
            page.switch_workspace(1, card={"title": "A", "content": "# A"})
            assert page._current_md == "# A edited but unsaved", (
                "switching back lost the workspace's unsaved edits")
        finally:
            page.deleteLater()

    def test_artifact_kinds_are_the_five_named_plus_reserved_podcast(self):
        """GLOSSARY: "Artifact — ... a Mermaid diagram, a knowledge-check
        quiz, a branded .pptx deck, a one-pager, or a battle card... A sixth
        kind, podcast, is reserved in the data model."
        """
        from src.data.artifact_store import KINDS

        assert KINDS == {"diagram", "quiz", "deck", "one_pager",
                         "battle_card", "podcast"}

    def test_nothing_generates_a_podcast_artifact(self):
        """GLOSSARY: the podcast kind has "no implementation behind it —
        nothing generates one."

        No chat tool may create an artifact of kind 'podcast'.
        """
        tools_dir = PROJECT_ROOT / "src" / "data" / "chat_tools"
        offenders = [
            p.name for p in tools_dir.glob("*.py")
            if "podcast" in p.read_text(encoding="utf-8").lower()
        ]
        assert offenders == [], (
            f"podcast is referenced by generator modules: {offenders}")

    def test_artifacts_have_no_dedicated_tab(self, qapp):
        """GLOSSARY: "Artifacts have no dedicated tab; you make them by
        asking Renn."
        """
        source = (PROJECT_ROOT / "src" / "ui" / "pages" / "enablement"
                  / "page.py").read_text(encoding="utf-8")
        tab_labels = re.findall(r'addTab\([^,]+,\s*"([^"]+)"\)', source)
        assert tab_labels, "no tabs found — the parse is wrong, not the claim"
        lowered = {t.lower() for t in tab_labels}
        assert not (lowered & {"artifacts", "artifact", "studio",
                               "content studio"}), (
            f"an artifact tab is registered: {tab_labels}")

    def test_index_card_carries_summary_key_facts_and_topics(self):
        """GLOSSARY: "Indexing a Drive folder produces one index card per
        document, each holding a two-sentence summary, a handful of key
        facts, and topic tags."

        The card format must round-trip exactly those three fields.
        """
        from src.data.kb import card_format

        meta = {
            "card_id": "kb-0000abcd", "schema_version": 1,
            "title": "Eligibility rules",
            "type": "source_summary",
            "topics": ["eligibility", "payers"],
            "summary": "First sentence. Second sentence.",
            "key_facts": ["Effective 2026-01-01", "Applies to Aetna"],
        }
        text = card_format.serialize_card(meta, "body text")
        parsed, body, issues = card_format.parse_card(text)
        assert issues == [], f"a card we just serialised failed to parse: {issues}"
        assert parsed["summary"] == "First sentence. Second sentence."
        assert parsed["key_facts"] == ["Effective 2026-01-01",
                                       "Applies to Aetna"]
        assert parsed["topics"] == ["eligibility", "payers"]
        assert "body text" in body

    def test_full_extracted_document_text_is_kept_locally(self, conn):
        """GLOSSARY: "The full extracted text of the document is also kept
        locally, so a detail the summary omitted is still findable."
        """
        from src.data import enablement_store as store
        from src.data.kb.search import kb_search

        needle = "reconsideration"
        store.save_document(
            conn, source="drive", name="Payer packet", source_ref="file-1",
            mime_type="application/pdf", web_url="", modified_time="",
            full_text=f"page 1 ... the {needle} window is 90 days ... page 2")

        stored = conn.execute(
            "SELECT full_text FROM enablement_documents "
            "WHERE source_ref = 'file-1'").fetchone()
        assert needle in (stored["full_text"] or ""), (
            "the full extracted text was not stored locally")

        hits = kb_search(conn, needle)
        assert any(h.get("match") == "fulltext" for h in hits), (
            "the local full text is stored but not reachable by search")

    def test_index_cards_cannot_be_written_outside_the_ec_allowlist(self, conn):
        """GLOSSARY: "[the EC folder] is the single allowlisted destination
        for the app's own Drive writes: the app will not write index cards
        anywhere else."
        """
        from src.data.kb import drive_kb

        conn.execute(
            "INSERT INTO kb_folders (folder_id, topic, role, status) "
            "VALUES ('ec-root', '', 'ec_root', 'ok')")
        conn.commit()

        class _ExploderExporter:
            def find_child_by_app_property(self, *a, **k):
                raise AssertionError("probed Drive for a denied folder")

            def upload_file(self, *a, **k):
                raise AssertionError("uploaded into a non-allowlisted folder")

        with pytest.raises(drive_kb.KBWriteDenied):
            drive_kb.write_card_file(
                conn, "some-other-drive-folder", "c.md", "body",
                card_id="kb-0000abcd", exporter=_ExploderExporter())

    def test_missing_ec_folder_is_reported_plainly_and_by_name(
            self, conn, settings_file):
        """GLOSSARY: "[if the EC folder is missing] Renn will say so plainly
        rather than fail silently — the message names the EC folder
        specifically."
        """
        from src.data.chat_tools.kb_tools import _kb_precheck

        settings_file({"enablement": {"demo_mode": False,
                                      "kb": {"enabled": True}}})
        refusal = _kb_precheck(conn)
        assert refusal is not None, "a missing EC folder was not refused"
        assert refusal["error"] == "ec_not_bootstrapped"
        assert "EC folder" in refusal["message"], (
            f"the refusal does not name the EC folder: {refusal['message']!r}")

    def test_attention_queue_rows_offer_open_and_dismiss(self, qapp):
        """GLOSSARY: "Attention queue — ... Each row offers a way to open a
        targeted update, or to dismiss the row."
        """
        from src.ui.pages.enablement.attention_queue_tab import AttentionQueueTab

        tab = AttentionQueueTab()
        try:
            opened, dismissed = [], []
            tab.open_update_requested.connect(opened.append)
            tab.dismiss_requested.connect(dismissed.append)

            open_btn = tab._open_button("card-1")
            open_btn.click()
            assert opened == ["card-1"], "the row's open control emitted nothing"

            from PySide6.QtWidgets import QFrame
            dismiss_btn = tab._dismiss_button("card-1", QFrame())
            dismiss_btn.click()
            assert dismissed == ["card-1"], (
                "the row's dismiss control emitted nothing")
        finally:
            tab.deleteLater()

    def test_attention_scoring_uses_staleness_and_engagement(self):
        """GLOSSARY: the attention queue is "based on Guru analytics such as
        staleness and low engagement."

        An overdue card must score worse than a current one, and a card with
        no views must score worse than a well-viewed one.
        """
        from src.data.enablement_health.models import CardSignals
        from src.data.enablement_health.score import score_card

        def sig(**kw):
            base = dict(card_id="c", days_overdue=0.0, view_count=40,
                        open_comment_count=0, source_changed=False,
                        duplicate_of=None, gap=False)
            base.update(kw)
            return CardSignals(**base)

        healthy = score_card(sig()).score
        stale = score_card(sig(days_overdue=30.0)).score
        unengaged = score_card(sig(view_count=0)).score

        assert stale < healthy, "staleness does not lower the health score"
        assert unengaged < healthy, (
            "low engagement does not lower the health score")
        assert score_card(sig(days_overdue=5.0)).bucket == "verification_overdue"

    def test_one_style_guide_and_one_card_template_are_active_at_a_time(self):
        """GLOSSARY: "One style guide is active at a time" and the card
        template is "a separate designated document".
        """
        from src.data import enablement_store as store

        assert store._STYLE_GUIDE_KEY == "style_guide_doc_id"
        assert store._CARD_TEMPLATE_KEY == "card_template_doc_id"
        assert store._STYLE_GUIDE_KEY != store._CARD_TEMPLATE_KEY, (
            "the two guides share one settings pointer")

    def test_the_active_guide_pointers_are_read_from_settings(
            self, conn, settings_file):
        """SETTINGS-KEYS: ``style_guide_doc_id`` — "The document acting as
        the active style guide"; ``card_template_doc_id`` — "The document
        acting as the active card template".

        Each key must select which stored document the app treats as active,
        and the two must be independent.
        """
        from src.data import enablement_store as store

        store.save_document(conn, source="local", name="[STYLE-GUIDE] Voice",
                            doc_id="sg-doc", full_text="Write plainly.")
        store.save_document(conn, source="local", name="[CARD-TEMPLATE] Shape",
                            doc_id="tpl-doc", full_text="## Overview")

        settings_file({"enablement": {}})
        assert store.get_style_guide(conn) == ""
        assert store.get_card_template(conn) == ""

        settings_file({"enablement": {"style_guide_doc_id": "sg-doc"}})
        assert store.get_style_guide(conn) == "Write plainly."
        assert store.get_card_template(conn) == "", (
            "the style-guide key also set the card template")

        settings_file({"enablement": {"card_template_doc_id": "tpl-doc"}})
        assert store.get_card_template(conn) == "## Overview"
        assert store.get_style_guide(conn) == ""

    def test_trc_vocabulary_does_not_appear_in_the_enablement_workspace(self):
        """GLOSSARY: "TRC — ... does not appear anywhere in the enablement
        workspace, so you should not expect to see it in the Workbench,
        Calendar, Tasks or Help."
        """
        pkg = PROJECT_ROOT / "src" / "ui" / "pages" / "enablement"
        pattern = re.compile(r"\bTRC\b")
        offenders = {
            p.name: pattern.findall(p.read_text(encoding="utf-8"))
            for p in pkg.glob("*.py")
        }
        offenders = {k: v for k, v in offenders.items() if v}
        assert offenders == {}, f"TRC vocabulary in enablement UI: {offenders}"

    def test_multi_word_search_matches_individual_words(self, conn):
        """GLOSSARY (If it doesn't): "Document and knowledge-base search now match
        your individual words ... a multi-word query finds content whose words are
        scattered through it."
        """
        from src.data import enablement_store as store

        store.save_document(
            conn, source="drive", name="Payer packet", source_ref="file-2",
            mime_type="text/plain", web_url="", modified_time="",
            full_text="alpha appears here and much later beta appears too")

        assert len(store.search_documents(conn, "alpha")) == 1
        assert len(store.search_documents(conn, "beta")) == 1
        assert [d["name"] for d in store.search_documents(conn, "alpha beta")] == \
            ["Payer packet"], "the tokenized search now matches non-adjacent words"


# ══════════════════════════════════════════════════════════════════════
#  settings-keys.md — reference-settings-keys
# ══════════════════════════════════════════════════════════════════════

# Every ``enablement.*`` key the article documents, as a dotted path.
DOCUMENTED_ENABLEMENT_KEYS = [
    "operator_email", "detected_email", "identity_auto_detect",
    "operator_name", "operator_asana_gid",
    "demo_mode", "provider",
    "drive.read_enabled", "drive.credentials_path", "drive.active_folders",
    "google.oauth_client_path", "kb.ec_folder_id",
    "asana.active_board", "asana.poll_interval_seconds",
    "poll_interval_seconds", "asana.extras_per_poll_cap",
    "guru.publish_collection_id", "guru.publish_folder_id",
    "analytics_poll_enabled",
    "style_guide_doc_id", "card_template_doc_id",
    "help.bug_form_url", "web_tabs",
]


class TestSettingsKeys:
    """Claims from ``assets/help/reference/settings-keys.md``."""

    @pytest.mark.parametrize("dotted", DOCUMENTED_ENABLEMENT_KEYS)
    def test_documented_key_is_actually_read_by_the_code(self, dotted):
        """SETTINGS-KEYS: every key in the article's tables is a real
        setting, and "a key you found in documentation ... [that] does
        nothing" is worth flagging.

        The leaf name must appear as a settings lookup somewhere in ``src/``
        — either inline, or bound to a module constant that is then used as
        the lookup key (the pattern ``enablement_store`` uses for the two
        guide pointers).
        """
        leaf = dotted.rsplit(".", 1)[-1]
        pattern = re.compile(
            rf"""(get|setdefault)\(\s*["']{re.escape(leaf)}["']"""
            rf"""|\[\s*["']{re.escape(leaf)}["']\s*\]"""
            rf"""|=\s*["']{re.escape(leaf)}["']\s*$""", re.MULTILINE)
        hits = [
            p for p in (PROJECT_ROOT / "src").rglob("*.py")
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore"))
        ]
        assert hits, f"no code reads the documented settings key {dotted!r}"

    def test_no_documented_key_raises_when_the_section_is_empty(
            self, settings_file, conn):
        """SETTINGS-KEYS: "a key missing from the file falls back to a
        default defined in code."

        With an entirely empty ``enablement`` section, every documented
        accessor must return a value rather than raise.
        """
        settings_file({"enablement": {}})

        from src.data import enablement_identity as ident
        from src.data.asana_extras import extras_cap
        from src.data.drive_query import is_live_drive_configured
        from src.data.drive_reader import DriveReader
        from src.data.enablement_store import get_style_guide
        from src.gemini.client_factory import _enablement_provider
        from src.ui.web.web_flags import web_home_enabled, web_tabs_mode

        assert ident.operator_email(resolve=False) is None
        assert ident.operator_name() == ""
        assert ident.operator_asana_gid() == ""
        assert ident.operator_identity()["source"] == "unset"
        assert extras_cap() == 10
        assert is_live_drive_configured() is False
        assert DriveReader.from_settings().is_configured() is False
        assert _enablement_provider() == ""
        assert web_tabs_mode() == "off"
        assert web_home_enabled() is False
        assert get_style_guide(conn) == ""

    def test_identity_override_wins_over_the_detected_value(self, settings_file):
        """SETTINGS-KEYS: "Identity resolves in order: the explicit override,
        then the detected value" — ``operator_email`` "Wins over detection."
        """
        from src.data import enablement_identity as ident

        settings_file({"enablement": {"operator_email": "override@example.com",
                                      "detected_email": "auto@example.com"}})
        assert ident.operator_email(resolve=False) == "override@example.com"
        assert ident.operator_identity()["source"] == "settings"

        settings_file({"enablement": {"detected_email": "auto@example.com"}})
        assert ident.operator_email(resolve=False) == "auto@example.com"
        assert ident.operator_identity()["source"] == "google"

    def test_identity_auto_detect_gates_the_detection_fallback(
            self, settings_file, monkeypatch):
        """SETTINGS-KEYS: ``identity_auto_detect`` — "Whether detection is
        allowed to run at all."
        """
        from src.data import enablement_identity as ident

        called = []

        def _fake_detect():
            called.append(True)
            return "detected@example.com"

        monkeypatch.setattr(ident, "_detect_and_cache", _fake_detect)

        settings_file({"enablement": {"identity_auto_detect": False}})
        assert ident.operator_email(resolve=True) is None
        assert called == [], "detection ran while auto-detect was off"

        settings_file({"enablement": {"identity_auto_detect": True}})
        assert ident.operator_email(resolve=True) == "detected@example.com"
        assert called == [True]

    def test_demo_mode_stops_background_monitoring(self, settings_file, conn):
        """SETTINGS-KEYS: "While [demo_mode] is true, background monitoring of
        Asana and Guru does not start at all."
        """
        from src.data.chat_tools.kb_tools import _kb_precheck

        settings_file({"enablement": {"demo_mode": True,
                                      "kb": {"enabled": True,
                                             "ec_folder_id": "ec-1"}}})
        refusal = _kb_precheck(conn)
        assert refusal is not None and refusal["error"] == "demo_mode", (
            "live KB work runs while demo mode is on")

        # The Asana/Guru poll loop is the EnablementMonitor, started only
        # when demo_mode is off.
        source = (PROJECT_ROOT / "src" / "ui"
                  / "main_window.py").read_text(encoding="utf-8")
        guard = re.search(
            r'en\.get\("demo_mode", True\):\s*\n\s*return', source)
        assert guard is not None, (
            "the enablement monitor no longer short-circuits on demo_mode")

        settings_file({"enablement": {"demo_mode": False,
                                      "kb": {"enabled": True,
                                             "ec_folder_id": "ec-1"}}})
        assert _kb_precheck(conn) is None, (
            "the refusal was not caused by demo_mode after all")

    def test_provider_key_selects_the_enablement_lane_provider(
            self, settings_file):
        """SETTINGS-KEYS: ``provider`` — "Which model provider the enablement
        lanes use."
        """
        from src.gemini.client_factory import resolve_provider_for_task

        settings_file({"enablement": {"provider": "claude"}})
        assert resolve_provider_for_task("enablement_card_generation") == "claude"

        settings_file({"enablement": {"provider": "gemini"}})
        assert resolve_provider_for_task("enablement_card_generation") == "gemini"

    def test_drive_reads_need_both_the_credentials_and_the_enable_flag(
            self, settings_file, conn):
        """SETTINGS-KEYS: "Drive reading needs both a credentials file and
        ``read_enabled``; enabling one without the other does nothing useful."
        """
        from src.data.drive_query import is_live_drive_configured
        from src.data.drive_reader import DriveReader

        creds = tmp_path_factory = PROJECT_ROOT / "requirements.txt"
        assert creds.is_file(), "fixture file missing — the test is wrong"

        # A credentials file, but the org flag is off.
        settings_file({"enablement": {"drive": {
            "credentials_path": str(creds)}}})
        assert is_live_drive_configured() is False, (
            "a credentials path alone enabled Drive reading")
        assert DriveReader.from_settings().is_configured() is False

        # The flag on, but no credentials file.
        settings_file({"enablement": {"drive": {"read_enabled": True}}})
        assert DriveReader.from_settings().is_configured() is False, (
            "read_enabled alone made the reader configured")

        # Both together.
        settings_file({"enablement": {"drive": {
            "read_enabled": True, "credentials_path": str(creds)}}})
        assert DriveReader.from_settings().is_configured() is True

    def test_asana_poll_interval_defaults_to_60_seconds(self, settings_file):
        """SETTINGS-KEYS: ``asana.poll_interval_seconds`` — "Default 60."."""
        from src.data.settings_manager import get_section

        settings_file({"enablement": {}})
        en = get_section("enablement", {}) or {}
        asana_cfg = en.get("asana") or {}
        assert int(asana_cfg.get("poll_interval_seconds", 60)) == 60

        source = (PROJECT_ROOT / "src" / "ui"
                  / "main_window.py").read_text(encoding="utf-8")
        assert 'asana_cfg.get("poll_interval_seconds", 60)' in source, (
            "the Asana poll cadence no longer defaults to 60")

    def test_enablement_poll_interval_defaults_to_300_seconds(self):
        """SETTINGS-KEYS: top-level ``poll_interval_seconds`` — "Default
        300", and it is "a different setting at a different nesting level"
        from the Asana one.
        """
        source = (PROJECT_ROOT / "src" / "ui"
                  / "main_window.py").read_text(encoding="utf-8")
        assert 'en.get("poll_interval_seconds", 300)' in source, (
            "the enablement monitor cadence no longer defaults to 300")
        assert 'asana_cfg.get("poll_interval_seconds", 60)' in source, (
            "the two poll intervals are no longer read at separate levels")

    def test_extras_per_poll_cap_defaults_to_10(self, settings_file):
        """SETTINGS-KEYS: ``asana.extras_per_poll_cap`` — "Default 10."."""
        from src.data.asana_extras import extras_cap

        settings_file({"enablement": {}})
        assert extras_cap() == 10

        settings_file({"enablement": {"asana": {"extras_per_poll_cap": 3}}})
        assert extras_cap() == 3

    def test_guru_analytics_polling_is_off_by_default(
            self, settings_file, conn, monkeypatch):
        """SETTINGS-KEYS: ``analytics_poll_enabled`` — "Off by default."."""
        from src.data import guru_client
        from src.data.guru_analytics_monitor import poll_once

        # Never reach a live Guru call from a test, whatever the gate does.
        monkeypatch.setattr(guru_client.GuruClient, "load_credentials",
                            staticmethod(lambda: ("", "")))

        settings_file({"enablement": {}})
        assert poll_once(conn) == {"skipped": "disabled"}, (
            "Guru analytics polling is on with the key absent")

        settings_file({"enablement": {"analytics_poll_enabled": True}})
        assert poll_once(conn) == {"skipped": "no_credentials"}, (
            "the key exists but does not turn polling on")

    def test_web_tabs_accepts_three_values_and_defaults_to_off(
            self, settings_file):
        """SETTINGS-KEYS: "``web_tabs`` accepts ``off``, ``calendar`` or
        ``all``, and defaults to ``off``. ... Anything unrecognised also
        degrades to ``off``."
        """
        from src.ui.web.web_flags import VALID_MODES, web_tabs_mode

        assert VALID_MODES == ("off", "calendar", "all")

        settings_file({"enablement": {}})
        assert web_tabs_mode() == "off", "an absent web_tabs did not default off"

        for value in ("off", "calendar", "all"):
            settings_file({"enablement": {"web_tabs": value}})
            assert web_tabs_mode() == value

        for junk in ("ALL_TABS", "yes", "", 7, None, ["all"]):
            settings_file({"enablement": {"web_tabs": junk}})
            assert web_tabs_mode() == "off", (
                f"the unrecognised value {junk!r} did not degrade to off")

    def test_ui_web_home_is_a_separate_app_level_key(self, settings_file):
        """SETTINGS-KEYS: "There is a related app-level key, ``ui.web_home``,
        which is deliberately kept separate from ``web_tabs``... Turning the
        two on is two independent decisions."
        """
        from src.ui.web.web_flags import web_home_enabled, web_tabs_mode

        settings_file({"enablement": {"web_tabs": "all"}})
        assert web_tabs_mode() == "all"
        assert web_home_enabled() is False, (
            "web_tabs=all also turned on the web Home page")

        settings_file({"ui": {"web_home": True}})
        assert web_home_enabled() is True
        assert web_tabs_mode() == "off", (
            "ui.web_home also turned on the web enablement tabs")

    def test_bug_form_url_unset_explains_instead_of_opening_a_dead_link(
            self, qapp, settings_file, monkeypatch):
        """SETTINGS-KEYS: "``help.bug_form_url`` has no default at all. Until
        an administrator points it at your team's Asana form, the flag-a-bug
        action explains that no form is configured instead of opening a dead
        link. The form deliberately opens in your own browser."
        """
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMessageBox, QWidget

        from src.ui.pages.enablement.page import EnablementPage

        shown, opened = [], []
        monkeypatch.setattr(
            QMessageBox, "information",
            staticmethod(lambda *a, **k: shown.append(a[2] if len(a) > 2 else "")))
        monkeypatch.setattr(
            QDesktopServices, "openUrl",
            staticmethod(lambda url: opened.append(url.toString()) or True))

        host = QWidget()
        try:
            settings_file({"enablement": {}})
            EnablementPage._open_bug_form(host)
            assert opened == [], "an unset bug form URL still opened a link"
            assert shown and "configured" in shown[0].lower(), (
                f"no explanation was shown; got {shown!r}")

            settings_file({"enablement": {
                "help": {"bug_form_url": "https://app.asana.com/form/x"}}})
            EnablementPage._open_bug_form(host)
            assert opened == ["https://app.asana.com/form/x"], (
                "a configured form URL was not opened in the system browser")
        finally:
            host.deleteLater()


# ══════════════════════════════════════════════════════════════════════
#  what-renn-can-write.md — reference-what-renn-can-write
# ══════════════════════════════════════════════════════════════════════

class TestWhatRennCanWrite:
    """Claims from ``assets/help/reference/what-renn-can-write.md``."""

    def test_exactly_five_operations_open_a_confirm_card(self):
        """WHAT-RENN-CAN-WRITE: "Five operations open a Confirm card" —
        create/rename a Guru folder, create/update an Asana task, upload an
        artifact to Drive.
        """
        from src.data.chat_action_requests import _CONFIRM_WRITE_OPS

        assert _CONFIRM_WRITE_OPS == {
            "create_guru_folder", "rename_guru_folder",
            "create_asana_task", "asana_task_update",
            "upload_artifact_to_drive",
        }

    def test_any_other_write_operation_is_rejected_before_dispatch(self, conn):
        """WHAT-RENN-CAN-WRITE: "That list is enforced, not advisory. Any
        other write operation is rejected before it can be dispatched, so a
        malformed or unexpected request cannot smuggle itself through as one
        of these five."
        """
        from src.data.chat_action_requests import create_confirm_write

        for forged in ("delete_guru_folder", "create_drive_folder",
                       "asana_task_delete", "", "upload_artifact"):
            with pytest.raises(ValueError):
                create_confirm_write(conn, "sess-1", forged, "summary", {})

        assert conn.execute(
            "SELECT COUNT(*) FROM chat_action_requests").fetchone()[0] == 0, (
            "a rejected op still wrote a dispatchable row")

    def test_asana_task_update_covers_the_five_documented_actions(self):
        """WHAT-RENN-CAN-WRITE: "Update an Asana task | Complete, reopen, due
        date, comment, subtask."
        """
        from src.data.chat_tools.enablement_tools import _TASK_UPDATE_ACTIONS

        assert _TASK_UPDATE_ACTIONS == {"complete", "reopen", "set_due",
                                        "comment", "add_subtask"}

    def test_four_operations_open_a_picker_or_connect_card(self):
        """WHAT-RENN-CAN-WRITE: "Four operations open a picker or a connect
        card" — Connect Google, Drive folder, Asana board, Guru publish
        target.
        """
        from src.data.chat_action_requests import ACTION_TYPES

        picker_types = {"google_connect", "drive_folder_picker",
                        "asana_board_picker", "guru_publish_picker"}
        assert picker_types <= ACTION_TYPES

        from src.data.chat_tools.registry import get_tool_registry
        registry = get_tool_registry()
        for tool in ("request_google_connect", "request_drive_picker",
                     "request_asana_board_picker", "request_guru_publish_picker"):
            assert tool in registry, f"{tool} is not a registered tool"

    def test_in_app_operations_need_no_confirmation(self):
        """WHAT-RENN-CAN-WRITE: "Everything else Renn does stays inside the
        app and needs no confirmation: searching your content, reading a
        document, creating and revising a card draft, generating an artifact,
        listing tasks, updating an enablement task, writing a task
        scratchpad, and tracking a multi-step job."
        """
        from src.data.chat_tools.enablement_tools import RESOLVER_ACTION_TOOLS
        from src.data.chat_tools.registry import get_tool_registry

        registry = get_tool_registry()
        in_app = [
            "search_local_documents", "get_drive_doc", "create_card_draft",
            "revise_draft", "generate_diagram", "generate_quiz",
            "generate_deck", "generate_doc", "list_tasks", "update_task",
            "update_scratchpad", "create_job", "update_job", "list_jobs",
        ]
        for tool in in_app:
            assert tool in registry, f"{tool} is not registered"
            assert tool not in RESOLVER_ACTION_TOOLS, (
                f"{tool} now opens a gated UI action")

    def test_publishing_to_guru_and_kb_index_writes_are_un_gated(self):
        """WHAT-RENN-CAN-WRITE: "Two writes are deliberately un-gated.
        Publishing a draft to Guru ... And the knowledge base writes its own
        index cards into the EC folder without a card."
        """
        from src.data.chat_tools.enablement_tools import RESOLVER_ACTION_TOOLS
        from src.data.chat_tools.registry import get_tool_registry

        registry = get_tool_registry()
        for tool in ("push_guru_draft", "index_drive_folder"):
            assert tool in registry, f"{tool} is not registered"
            assert tool not in RESOLVER_ACTION_TOOLS, (
                f"{tool} is gated — the article says it is not")

    def test_there_is_no_way_to_delete_a_guru_folder(self):
        """WHAT-RENN-CAN-WRITE: "There is no way to delete a Guru folder from
        this app. Renaming exists; deleting does not."
        """
        from src.data.chat_action_requests import _CONFIRM_WRITE_OPS
        from src.data.chat_tools.registry import get_tool_registry
        from src.data.guru_client import GuruClient

        assert not hasattr(GuruClient, "delete_folder")
        assert any("rename" in op for op in _CONFIRM_WRITE_OPS)
        assert not any("delete" in op for op in _CONFIRM_WRITE_OPS)

        tool_names = list(get_tool_registry())
        assert not [t for t in tool_names
                    if "delete" in t and "folder" in t], (
            f"a delete-folder tool is registered: {tool_names}")

    def test_no_confirmed_operation_writes_a_drive_folder_name(self, conn):
        """WHAT-RENN-CAN-WRITE: "Renn will not write a Drive folder name.
        Folder names can carry identifying information, so no confirmed
        operation writes one."
        """
        import json

        from src.data.chat_action_requests import (_CONFIRM_WRITE_OPS,
                                                   create_confirm_write)

        assert not any("drive_folder" in op for op in _CONFIRM_WRITE_OPS)

        # The one Drive-touching confirmed op carries an id, never a name.
        create_confirm_write(conn, "sess-1", "upload_artifact_to_drive",
                             "Upload deck.pptx to Drive folder abc123",
                             {"artifact_id": "a-1", "folder_id": "abc123"})
        row = conn.execute(
            "SELECT payload_json FROM chat_action_requests").fetchone()
        params = json.loads(row["payload_json"])["params"]
        assert set(params) == {"artifact_id", "folder_id"}, (
            f"the upload envelope grew a new field: {sorted(params)}")
        assert "name" not in json.dumps(params).lower()

    def test_the_three_un_gated_asana_write_tools_are_retired(self):
        """WHAT-RENN-CAN-WRITE: "Three older Asana write tools that ran
        without a card were retired; every change Renn initiates now goes
        through the approval path."
        """
        from src.data.chat_tools.registry import get_tool_registry
        from src.mcp.chat_mcp_server import (_MCP_ALLOWED_TOOLS,
                                             _RETIRED_TOOL_HINTS, _execute_tool)

        retired = {"create_asana_subtask", "post_asana_comment",
                   "update_asana_due_date"}
        assert set(_RETIRED_TOOL_HINTS) == retired

        registry = get_tool_registry()
        for name in retired:
            assert name not in registry, f"{name} is still a registered tool"
            assert name not in _MCP_ALLOWED_TOOLS, (
                f"{name} is still advertised over MCP")
            result = _execute_tool(name, {})
            assert "request_asana_task_update" in result.get("error", ""), (
                f"calling {name} did not steer to the gated tool")

    def test_the_request_id_is_minted_in_app_and_never_returned_to_the_model(
            self, conn, monkeypatch):
        """WHAT-RENN-CAN-WRITE: "The request identifier for each pending
        confirmation is generated inside the app rather than supplied by the
        model, so an approval cannot be fabricated or replayed."

        Also: "Renn should stop and wait after opening a Confirm card" — the
        propose tool returns only a wait instruction.
        """
        from src.data.chat_tools import enablement_tools as et

        monkeypatch.setattr(et, "_guru_client_or_none", lambda: None)
        reply = et._request_create_guru_folder_impl(
            conn, "sess-1", "col-1", "Onboarding",
            collection_name="Enablement")

        row = conn.execute(
            "SELECT request_id, type FROM chat_action_requests").fetchone()
        assert row is not None, "no confirm row was minted"
        assert row["type"] == "confirm_write"
        assert len(row["request_id"]) == 32, "the id is not an app-minted uuid4"
        assert row["request_id"] not in reply, (
            "the propose tool leaked the request id to the model")
        assert "STOP and wait" in reply
        assert "cannot run writes directly" in reply

    def test_the_confirm_card_names_the_specific_thing_being_changed(
            self, conn, monkeypatch):
        """WHAT-RENN-CAN-WRITE: "The card should name the specific thing
        being changed. You should be able to read it and recognise your own
        request in it."
        """
        import json

        from src.data.chat_tools import enablement_tools as et

        monkeypatch.setattr(et, "_guru_client_or_none", lambda: None)
        et._request_create_guru_folder_impl(
            conn, "sess-1", "col-1", "Prior Auth 2026",
            collection_name="Enablement")

        payload = json.loads(conn.execute(
            "SELECT payload_json FROM chat_action_requests").fetchone()[0])
        assert "Prior Auth 2026" in payload["summary"]
        assert "Enablement" in payload["summary"]
        assert payload["params"]["title"] == "Prior Auth 2026"

    def test_a_confirmed_asana_change_is_rejected_when_the_task_moved(
            self, empty_db, monkeypatch):
        """WHAT-RENN-CAN-WRITE (If it doesn't): "If an Asana task moved
        underneath you, a message about the task having changed and needing a
        retry is expected."
        """
        from src.data import asana_client, enablement_tasks as et
        from src.services.agent_chat import _write_asana_task_update

        conn = empty_db.conn
        task_id = et.create_task(conn, source="asana", kind="request",
                                 title="Refresh the payer packet",
                                 source_ref="gid-123")
        et.update_task(conn, task_id, remote_modified_at="2026-07-01T00:00:00Z")

        class _FakeAsana:
            api_key = "not-a-real-key"

            def get_task(self, gid, opt_fields=""):
                # Asana reports a NEWER modified_at than the propose-time snapshot.
                return {"modified_at": "2026-07-19T12:00:00Z", "due_on": ""}

        monkeypatch.setattr(asana_client.AsanaClient, "from_store",
                            classmethod(lambda cls: _FakeAsana()))

        result = _write_asana_task_update(
            {"task_id": task_id, "action": "complete", "value": "",
             "expected_modified_at": "2026-07-01T00:00:00Z"},
            {"db_path": str(empty_db.db_path)})

        assert result["ok"] is False
        assert result.get("conflict") is True
        assert "changed in Asana" in result["error"]

    def test_a_confirmed_artifact_upload_targets_the_named_folder_over_the_ec_default(
            self, conn, settings_file, monkeypatch, tmp_path):
        """WHAT-RENN-CAN-WRITE: "Upload an artifact to Drive | A file written
        into a Drive folder — the EC folder by default, or another folder if
        one was named", and "read the folder ID on the card ... before you
        approve".

        The corrected article no longer promises the EC folder is the only
        destination: an explicit ``target_folder_id`` overrides the EC default
        (artifact_tools.py:488-493), so the Confirm card must carry whichever
        folder ID will actually be used. The gate is unchanged — a proposal
        only mints a confirm_write row, never a live write.
        """
        import json

        from src.data import artifact_store
        from src.data.chat_tools import artifact_tools
        from src.data.chat_tools import enablement_tools as et

        settings_file({"enablement": {"kb": {"ec_folder_id": "EC-FOLDER"}}})
        monkeypatch.setattr(et, "_active_session_id", lambda: "sess-1")

        rendered = tmp_path / "deck.pptx"
        rendered.write_bytes(b"x")
        artifact_id = artifact_store.create_artifact(
            conn, kind="deck", title="Deck")
        artifact_store.update_artifact(conn, artifact_id,
                                       file_path=str(rendered))

        # A named target folder wins over the EC default...
        artifact_tools.handle_request_upload_artifact(
            conn, {"artifact_id": artifact_id,
                   "target_folder_id": "SOMEWHERE-ELSE"}, {})
        row = conn.execute(
            "SELECT type, payload_json FROM chat_action_requests").fetchone()
        payload = json.loads(row["payload_json"])
        assert row["type"] == "confirm_write", (
            "the upload proposal bypassed the Confirm gate")
        assert payload["params"]["folder_id"] == "SOMEWHERE-ELSE", (
            "the named target folder did not override the EC default")
        assert "SOMEWHERE-ELSE" in payload["summary"], (
            "the Confirm card does not name the folder the file will land in, "
            "so the operator cannot read the destination before approving")

        # ...and with no named folder, the EC folder is the default.
        conn.execute("DELETE FROM chat_action_requests")
        conn.commit()
        artifact_tools.handle_request_upload_artifact(
            conn, {"artifact_id": artifact_id}, {})
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM chat_action_requests").fetchone()[0])
        assert payload["params"]["folder_id"] == "EC-FOLDER", (
            "an upload with no named folder did not default to the EC folder")


# ══════════════════════════════════════════════════════════════════════
#  version-and-data.md — reference-version-and-data
# ══════════════════════════════════════════════════════════════════════

class TestVersionAndData:
    """Claims from ``assets/help/reference/version-and-data.md``."""

    def test_this_build_is_version_1_0_0(self):
        """VERSION-AND-DATA: "This build is version 1.0.0.\""""
        from src import VERSION

        assert VERSION == "1.0.0"

    def test_sidebar_and_settings_show_the_same_v_prefixed_version(self):
        """VERSION-AND-DATA: "The version appears in two places: at the
        bottom of the sidebar, written as ``v`` followed by the number, and
        in Settings on the Updates tab... The version in the sidebar and the
        version in Settings should always agree."

        Both must render from the single ``src.VERSION`` constant — neither
        may hardcode a literal, or they could drift apart.
        """
        sidebar = (PROJECT_ROOT / "src" / "ui"
                   / "main_window.py").read_text(encoding="utf-8")
        settings = (PROJECT_ROOT / "src" / "ui" / "pages"
                    / "settings_page.py").read_text(encoding="utf-8")

        for name, source in (("sidebar", sidebar), ("settings", settings)):
            assert "from src import VERSION" in source, (
                f"the {name} version is not read from src.VERSION")
            assert 'f"v{VERSION}' in source, (
                f"the {name} no longer renders v-prefixed src.VERSION")

    def test_update_checking_can_be_switched_off_entirely(self, qapp):
        """VERSION-AND-DATA: "[update checking] can be switched off entirely
        in settings."

        With auth_mode=disabled the checker must emit up_to_date without
        making any network call.
        """
        from src.updater.update_checker import UpdateChecker

        checker = UpdateChecker(releases_url="http://127.0.0.1:0/never",
                                github_pat="", auth_mode="disabled")
        seen = []
        checker.up_to_date.connect(lambda: seen.append("up_to_date"))
        checker.check_failed.connect(lambda m: seen.append(f"failed:{m}"))
        checker.check()
        assert seen == ["up_to_date"], (
            f"a disabled checker did not short-circuit: {seen}")

    def test_a_missing_update_token_warns_at_startup(self, monkeypatch):
        """VERSION-AND-DATA: "Update checking needs an access token to be
        configured... If no token is configured you will see a warning at
        startup rather than a silent failure."
        """
        from src.startup.checks import updates as updates_check
        from src.updater import update_checker

        monkeypatch.setattr(
            update_checker, "_resolve_config_and_token",
            lambda: ("pat", "http://127.0.0.1:0/never", ""))
        result = updates_check.check_for_update()
        assert result.status == "warn"
        assert "token" in result.message.lower()
        assert result.critical is False, "a missing token blocks startup"

        monkeypatch.setattr(
            update_checker, "_resolve_config_and_token",
            lambda: ("disabled", "http://127.0.0.1:0/never", ""))
        off = updates_check.check_for_update()
        assert off.status == "pass"

    def test_rollback_restores_the_version_number(self, tmp_path, monkeypatch):
        """VERSION-AND-DATA: "the app can roll back to the previous version,
        which includes restoring the version number itself so what the
        sidebar shows always matches what is actually installed."
        """
        from src.updater import rollback

        fake_root = tmp_path / "app"
        (fake_root / "src").mkdir(parents=True)
        init_py = fake_root / "src" / "__init__.py"
        init_py.write_text('VERSION = "9.9.9"\n', encoding="utf-8")
        monkeypatch.setattr(rollback, "_PROJECT_ROOT", fake_root)

        rollback._update_version_in_init("v1.0.0")
        assert 'VERSION = "1.0.0"' in init_py.read_text(encoding="utf-8"), (
            "rollback did not restore the version number")

    def test_data_lives_in_the_documented_paths(self):
        """VERSION-AND-DATA: "All app data | ``data/local_warehouse.db``",
        "Your settings | ``data/settings.yaml``", "Logs | ``data/logs``".
        """
        from src.data.connection_factory import DEFAULT_DB_PATH
        from src.data.run_logger import LOG_DIR
        from src.data.settings_manager import _DATA_DIR, _SETTINGS_FILE

        assert DEFAULT_DB_PATH.parent.name == "data"
        assert DEFAULT_DB_PATH.name == "local_warehouse.db"
        assert _DATA_DIR.name == "data" and _SETTINGS_FILE == "settings.yaml"
        assert LOG_DIR.parent.name == "data" and LOG_DIR.name == "logs"

    def test_the_database_has_wal_and_shm_sidecars(self, tmp_path):
        """VERSION-AND-DATA: "Database sidecars | ``local_warehouse.db-wal``
        and ``-shm``... The two sidecar files are part of the database, not
        temporary junk — copying the database without them can lose recent
        writes."
        """
        from src.data.connection_factory import get_connection

        db_path = tmp_path / "local_warehouse.db"
        c = get_connection(db_path)
        try:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal", f"journal_mode is {mode!r}, not WAL"
            c.execute("CREATE TABLE t (x INTEGER)")
            c.execute("INSERT INTO t VALUES (1)")
            c.commit()
            assert db_path.with_name(db_path.name + "-wal").exists()
            assert db_path.with_name(db_path.name + "-shm").exists()
        finally:
            c.close()

    def test_the_named_content_types_are_rows_in_the_one_database(self, conn):
        """VERSION-AND-DATA: "The database is a single SQLite file. Card
        drafts, enablement tasks, generated artifacts, indexed document text,
        knowledge base cards, chat history and this help content are all rows
        inside it."
        """
        expected = {
            "card drafts": "guru_content_drafts",
            "enablement tasks": "enablement_tasks",
            "generated artifacts": "enablement_artifacts",
            "indexed document text": "enablement_documents",
            "knowledge base cards": "kb_cards",
            "chat history": "chat_messages",
            "help content": "help_articles",
        }
        present = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        missing = {label: table for label, table in expected.items()
                   if table not in present}
        assert missing == {}, f"claimed content has no table: {missing}"

    def test_help_content_is_loaded_from_bundled_files_into_rows(
            self, conn, tmp_path):
        """VERSION-AND-DATA: "...and this help content are all rows inside
        it." (The markdown under ``assets/help`` is the source; the app
        reads rows.)
        """
        from src.data.help import loader, store

        assert store.count_articles(conn) == 0
        corpus = tmp_path / "help" / "reference"
        corpus.mkdir(parents=True)
        (corpus / "a.md").write_text(
            "---\nid: t-1\ntitle: T\nsection: reference\n"
            "section_title: Reference\nsection_order: 10\norder: 1\n"
            "status: available\nsummary: s\n---\n\nBody text.\n",
            encoding="utf-8")
        summary = loader.load_bundled_help(conn, directory=corpus.parent)
        assert summary["errors"] == []
        assert store.count_articles(conn) == 1
        article = store.get_article(conn, "t-1")
        assert article["title"] == "T" and "Body text." in article["body"]

    def test_base_redaction_covers_every_documented_identifier_class(self):
        """VERSION-AND-DATA: "The base pass — emails, phone numbers, national
        identifiers, card numbers, member IDs — always runs."
        """
        from src.gemini.gemini_client import GeminiClient

        sample = ("contact jane.doe@example.com or 555-867-5309, "
                  "SSN 123-45-6789, card 4111 1111 1111 1111, "
                  "member ID M123456789")
        out = GeminiClient._redact_base(None, sample)

        for leaked in ("jane.doe@example.com", "555-867-5309", "123-45-6789",
                       "4111 1111 1111 1111"):
            assert leaked not in out, f"base redaction leaked {leaked!r}"
        assert out != sample

    def test_enablement_disables_only_the_capitalised_terms_pass(
            self, settings_file, monkeypatch):
        """VERSION-AND-DATA: "On enablement work the additional pass that
        rewrites capitalised terms is deliberately turned off... base
        redaction ... always runs regardless."
        """
        from src.gemini import client_factory
        from src.gemini.gemini_client import GeminiClient

        class _StubClient:
            pii_redaction = True

        monkeypatch.setattr(client_factory, "_build_gemini_client_internal",
                            lambda use_bridge: _StubClient())
        settings_file({"enablement": {"provider": "gemini"}})

        enablement = client_factory.build_client_for_task(
            "enablement_card_generation")
        assert enablement.pii_redaction is False, (
            "the aggressive pass is still on for the enablement lane")

        ticket = client_factory.build_client_for_task("nlp_classification")
        assert ticket.pii_redaction is True, (
            "the aggressive pass was disabled outside the enablement lane")

        # The aggressive pass is what rewrites capitalised product terms;
        # the base pass is untouched by the toggle.
        assert "[NAME]" in GeminiClient._redact_aggressive(
            None, "Ask Jane Doe about it")
        assert "a@b.com" not in GeminiClient._redact_base(None, "mail a@b.com")

    def test_base_redaction_fails_closed_on_both_the_ops_path_and_the_enablement_lane(
            self, monkeypatch):
        """VERSION-AND-DATA: "if that pass ever cannot load its rules the call
        fails rather than sending your text through un-redacted" and "That path
        now aborts the call and raises if it cannot load its rules, so no
        un-redacted text goes out on either path."

        Both model paths now fail CLOSED when the redaction config cannot load.
        The direct Anthropic-API ops path raises ``RedactionError``
        (claude_client.py:64-97) so ``ClaudeClient.generate`` aborts before any
        request; the enablement lane's base pass (``GeminiClient._redact_base``,
        gemini_client.py:300) has no swallow either and lets the load fault
        surface instead of leaking un-redacted text.
        """
        from src.gemini import gemini_client
        from src.gemini.gemini_client import GeminiClient
        from src.llm.claude_client import RedactionError, _redact_text

        def _boom():
            raise RuntimeError("patterns file unreadable")

        monkeypatch.setattr(gemini_client, "_load_redaction_config", _boom)

        # Ops path (direct Anthropic API): fails CLOSED — the fault surfaces as
        # RedactionError rather than the raw text coming back.
        with pytest.raises(RedactionError):
            _redact_text("mail jane.doe@example.com now")

        # Enablement lane (ClaudeCliClient → GeminiClient._redact_base): no
        # swallow either — the fault surfaces rather than leaking un-redacted text.
        with pytest.raises(RuntimeError):
            GeminiClient._redact_base(None, "mail jane.doe@example.com now")
