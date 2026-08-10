"""Shared auto-update / GitHub repo / support-and-recovery panel.

Extracted verbatim from the product Settings page's Updates tab
(settings_page.py, 2026-07-22) so both modes can host the same surface:
the product page embeds it on its Updates tab, the enablement Settings
page on its own Updates tab — the CredentialsPanel pattern, one widget,
one settings store, no drift.

Self-contained: talks to ``src.updater`` (UpdateChecker / Updater /
rollback / restart), ``settings_manager`` (the ``updates`` section) and
``pat_store`` (``github_pat``). No database, no host coupling.

Reads are deferred like CredentialsPanel's: the constructor touches only
``settings.yaml`` (last-checked timestamp) and the rollback state file;
the GitHub repo + PAT fields populate on first show, so constructing the
panel in a test never opens the credential store.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.data.settings_manager import get_section, update_section
from src.ui.theme import (
    ALMA_BG_ELEVATED,
    ALMA_BORDER,
    ALMA_BORDER_LIGHT,
    ALMA_CREAM,
    ALMA_ERROR,
    ALMA_GREEN_DARK,
    ALMA_GREEN_MID,
    ALMA_SUCCESS,
    ALMA_TEXT_DARK,
    ALMA_TEXT_LIGHT,
    ALMA_TEXT_MID,
    ALMA_WARNING,
    ALMA_WHITE,
    apply_card_shadow,
)


class UpdatesPanel(QWidget):
    """Auto-update status + GitHub repo config + support & recovery."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._github_loaded = False
        self._build()

    # ── layout ──────────────────────────────────────────────────────

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._section_label("AUTO-UPDATE"))
        lay.addSpacing(8)
        lay.addWidget(self._build_update_card())
        lay.addSpacing(24)

        lay.addWidget(self._section_label("GITHUB REPOSITORY"))
        lay.addSpacing(8)
        lay.addWidget(self._build_repo_card())
        lay.addSpacing(24)

        lay.addWidget(self._section_label("SUPPORT & RECOVERY"))
        lay.addSpacing(8)
        lay.addWidget(self._build_recovery_card())

    def _build_update_card(self) -> QFrame:
        from src import VERSION

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        status_row = QHBoxLayout()
        info_col = QVBoxLayout()
        info_col.setSpacing(4)
        title = QLabel("Auto-Update")
        title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {ALMA_TEXT_DARK};")
        desc = QLabel(
            "Updates replace src/ and config/ only. "
            "Your database and credentials are never modified."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        info_col.addWidget(title)
        info_col.addWidget(desc)
        status_row.addLayout(info_col, 1)

        self._update_status_pill = QLabel("Up to date")
        self._update_status_pill.setStyleSheet(self._pill_style(ALMA_SUCCESS, "22,163,74"))
        status_row.addWidget(self._update_status_pill)
        card_layout.addLayout(status_row)

        version_frame = QFrame()
        version_frame.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_CREAM}; border-radius: 5px;
                padding: 10px 14px;
            }}
        """)
        version_layout = QHBoxLayout(version_frame)
        version_layout.setContentsMargins(14, 10, 14, 10)

        v_col = QVBoxLayout()
        v_label = QLabel("Current version")
        v_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_LIGHT}; border: none;")
        v_value = QLabel(f"v{VERSION}")
        v_value.setStyleSheet(
            f"font-size: 14px; font-weight: 700; font-family: monospace; "
            f"color: {ALMA_TEXT_DARK}; border: none;")
        v_col.addWidget(v_label)
        v_col.addWidget(v_value)
        version_layout.addLayout(v_col)
        version_layout.addStretch()

        card_layout.addWidget(version_frame)

        self._update_msg = QLabel("")
        self._update_msg.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._update_msg.setWordWrap(True)
        self._update_msg.setVisible(False)
        card_layout.addWidget(self._update_msg)

        btn_row = QHBoxLayout()
        self._check_updates_btn = QPushButton("Check for Updates")
        self._check_updates_btn.setStyleSheet(self._ghost_btn_style())
        self._check_updates_btn.setCursor(Qt.PointingHandCursor)
        self._check_updates_btn.clicked.connect(self._on_check_updates)
        btn_row.addWidget(self._check_updates_btn)

        self._release_notes_btn = QPushButton("View Release Notes")
        self._release_notes_btn.setStyleSheet(self._ghost_btn_style())
        self._release_notes_btn.setCursor(Qt.PointingHandCursor)
        self._release_notes_btn.setEnabled(False)
        self._release_notes_btn.clicked.connect(self._on_view_release_notes)
        btn_row.addWidget(self._release_notes_btn)

        self._install_btn = QPushButton("Install Now")
        self._install_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 8px 20px;
                font-weight: 600; font-size: 13px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
            QPushButton:disabled {{ background: {ALMA_BORDER}; color: {ALMA_TEXT_LIGHT}; }}
        """)
        self._install_btn.setCursor(Qt.PointingHandCursor)
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self._on_install_update)
        btn_row.addWidget(self._install_btn)

        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        self._update_progress = QProgressBar()
        self._update_progress.setRange(0, 100)
        self._update_progress.setValue(0)
        self._update_progress.setVisible(False)
        self._update_progress.setStyleSheet(f"""
            QProgressBar {{
                background: {ALMA_CREAM}; border: 1px solid {ALMA_BORDER};
                border-radius: 6px; height: 18px; text-align: center;
                font-size: 11px; color: {ALMA_TEXT_DARK};
            }}
            QProgressBar::chunk {{
                background: {ALMA_GREEN_DARK}; border-radius: 5px;
            }}
        """)
        card_layout.addWidget(self._update_progress)

        self._progress_label = QLabel("")
        self._progress_label.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        self._progress_label.setVisible(False)
        card_layout.addWidget(self._progress_label)

        self._restart_btn = QPushButton("Restart Now")
        self._restart_btn.setStyleSheet(f"""
            QPushButton {{
                background: {ALMA_GREEN_DARK}; color: {ALMA_CREAM};
                border: none; border-radius: 8px; padding: 10px 28px;
                font-weight: 700; font-size: 14px;
            }}
            QPushButton:hover {{ background: {ALMA_GREEN_MID}; }}
        """)
        self._restart_btn.setCursor(Qt.PointingHandCursor)
        self._restart_btn.setVisible(False)
        self._restart_btn.clicked.connect(self._on_restart_app)
        card_layout.addWidget(self._restart_btn)

        self._last_checked_label = QLabel("")
        self._last_checked_label.setStyleSheet(
            f"font-size: 11px; color: {ALMA_TEXT_LIGHT};"
        )
        card_layout.addWidget(self._last_checked_label)
        self._load_last_checked()

        return card

    def _build_repo_card(self) -> QFrame:
        repo_card = self._card()
        repo_layout = QVBoxLayout(repo_card)
        repo_layout.setContentsMargins(20, 18, 20, 18)
        repo_layout.setSpacing(10)

        repo_desc = QLabel(
            "Configure the GitHub repository for update checks. "
            "For private repositories, provide a Personal Access Token (PAT) "
            "with read-only repo access."
        )
        repo_desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        repo_desc.setWordWrap(True)
        repo_layout.addWidget(repo_desc)

        fields_row = QHBoxLayout()
        fields_row.setSpacing(16)

        lbl_style = (f"font-size: 11px; font-weight: 600; color: {ALMA_TEXT_MID}; "
                     "letter-spacing: 0.5px;")
        field_style = f"""
            QLineEdit {{
                background: {ALMA_WHITE}; border: 1px solid {ALMA_BORDER};
                border-radius: 8px; padding: 8px 12px; font-size: 13px;
                color: {ALMA_TEXT_DARK};
            }}
        """

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("REPO URL")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._github_repo_input = QLineEdit()
        self._github_repo_input.setPlaceholderText("owner/repo  (e.g. alma-health/alma-insights)")
        self._github_repo_input.setStyleSheet(field_style)
        col.addWidget(self._github_repo_input)
        fields_row.addLayout(col, 2)

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("GITHUB PAT (optional — for private repos)")
        lbl.setStyleSheet(lbl_style)
        col.addWidget(lbl)
        self._github_pat_input = QLineEdit()
        self._github_pat_input.setPlaceholderText("ghp_xxxxxxxxxxxxxxxxxxxx")
        self._github_pat_input.setEchoMode(QLineEdit.Password)
        self._github_pat_input.setStyleSheet(field_style)
        col.addWidget(self._github_pat_input)
        fields_row.addLayout(col, 2)

        repo_layout.addLayout(fields_row)

        save_row = QHBoxLayout()
        save_btn = QPushButton("Save Repository Settings")
        save_btn.setStyleSheet(self._ghost_btn_style())
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.clicked.connect(self._on_save_github_settings)
        save_row.addWidget(save_btn)

        self._github_status = QLabel("")
        self._github_status.setStyleSheet(f"font-size: 11px; color: {ALMA_TEXT_MID};")
        save_row.addWidget(self._github_status, 1)
        save_row.addStretch()
        repo_layout.addLayout(save_row)

        return repo_card

    def _build_recovery_card(self) -> QFrame:
        """Export crash reports + rollback to previous version."""
        from src.updater.rollback import current_state

        card = self._card()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(10)

        desc = QLabel(
            "Export the last 20 crash reports as a zip for a support ticket, "
            "or roll back the app to the previous installed version."
        )
        desc.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_LIGHT};")
        desc.setWordWrap(True)
        card_layout.addWidget(desc)

        btn_row = QHBoxLayout()

        self._export_crash_btn = QPushButton("Export Crash Reports")
        self._export_crash_btn.setStyleSheet(self._ghost_btn_style())
        self._export_crash_btn.setCursor(Qt.PointingHandCursor)
        self._export_crash_btn.clicked.connect(self._on_export_crash_reports)
        btn_row.addWidget(self._export_crash_btn)

        self._rollback_btn = QPushButton("Rollback to Previous Version")
        self._rollback_btn.setStyleSheet(self._ghost_btn_style())
        self._rollback_btn.setCursor(Qt.PointingHandCursor)
        self._rollback_btn.clicked.connect(self._on_manual_rollback)
        state = current_state()
        self._rollback_btn.setEnabled(state is not None)
        btn_row.addWidget(self._rollback_btn)

        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        self._support_status = QLabel("")
        self._support_status.setStyleSheet(f"font-size: 12px; color: {ALMA_TEXT_MID};")
        self._support_status.setWordWrap(True)
        card_layout.addWidget(self._support_status)

        return card

    # ── deferred credential read (CredentialsPanel pattern) ─────────

    def showEvent(self, event):
        super().showEvent(event)
        if not self._github_loaded:
            self._github_loaded = True
            self._load_github_settings()

    # ── support & recovery handlers ─────────────────────────────────

    def _on_export_crash_reports(self):
        """Zip the last 20 crash reports and let the user save them."""
        from datetime import datetime
        from src.core import crash_handler

        reports = crash_handler.list_reports(limit=20)
        if not reports:
            self._support_status.setText("No crash reports found — nothing to export.")
            return

        suggested = f"alma-crash-reports-{datetime.now():%Y%m%d-%H%M%S}.zip"
        from src.data.app_paths import start_dir
        path, _ = QFileDialog.getSaveFileName(
            self, "Export crash reports", start_dir("downloads", suggested),
            "Zip archive (*.zip)"
        )
        if not path:
            return

        try:
            from pathlib import Path
            crash_handler.export_bundle(Path(path), limit=20)
            self._support_status.setText(
                f"Exported {len(reports)} report(s) to {path}"
            )
        except OSError as exc:
            self._support_status.setText(f"Export failed: {exc}")

    def _on_manual_rollback(self):
        """Confirm + perform a user-initiated rollback to the previous version."""
        from src.updater.rollback import current_state, perform_rollback

        state = current_state()
        if state is None:
            self._support_status.setText("No previous version on disk.")
            self._rollback_btn.setEnabled(False)
            return

        previous = state.get("previous", "unknown")
        reply = QMessageBox.question(
            self,
            "Rollback to previous version",
            f"Restore the previous installed version (v{previous})?\n\n"
            "The app will need to restart after rollback.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        ok, message = perform_rollback()
        self._support_status.setText(message)
        if ok:
            self._rollback_btn.setEnabled(False)
            QMessageBox.information(
                self, "Rollback complete",
                "Restart Alma Insights for the change to take effect.",
            )

    # ── GitHub settings ─────────────────────────────────────────────

    def _load_github_settings(self):
        """Load GitHub repo + PAT from settings/credentials."""
        try:
            update_cfg = get_section("updates", {})
            repo = update_cfg.get("github_repo", "")
            self._github_repo_input.setText(repo)
        except Exception:
            pass
        try:
            from src.data.pat_store import load_setting
            pat = load_setting("github_pat", "")
            if pat:
                self._github_pat_input.setText(pat)
                self._github_status.setText("✓ PAT configured")
        except Exception:
            pass

    def _on_save_github_settings(self):
        """Save GitHub repo (owner/repo) and PAT."""
        from src.updater.update_checker import normalize_github_repo
        repo = normalize_github_repo(self._github_repo_input.text())
        pat = self._github_pat_input.text().strip()
        try:
            # MERGE, don't replace — a bare set_section here wipes last_checked,
            # and _save_last_checked (also on the updates section) would then wipe
            # github_repo on the very next check. That clobber was why the repo
            # never persisted and every check fell back to the default repo.
            update_section("updates", {"github_repo": repo})
            if pat:
                from src.data.pat_store import save_setting
                save_setting("github_pat", pat)
            # Reflect the normalized value back so the user sees owner/repo.
            self._github_repo_input.setText(repo)
            self._github_status.setText("✓ Saved")
        except Exception as e:
            self._github_status.setText(f"Error: {e}")

    # ── update check / install / restart ────────────────────────────

    def _on_check_updates(self):
        """Launch UpdateChecker in background."""
        self._check_updates_btn.setEnabled(False)
        self._check_updates_btn.setText("Checking…")
        self._update_msg.setVisible(False)

        # Build releases URL from saved repo or use default
        releases_url = None
        try:
            update_cfg = get_section("updates", {})
            repo = update_cfg.get("github_repo", "").strip()
            if repo:
                releases_url = f"https://api.github.com/repos/{repo}/releases/latest"
        except Exception:
            pass

        # Load PAT for auth header
        github_pat = None
        try:
            from src.data.pat_store import load_setting
            github_pat = load_setting("github_pat", "")
        except Exception:
            pass

        from src.updater.update_checker import UpdateChecker
        self._update_checker = UpdateChecker(
            parent=self, releases_url=releases_url, github_pat=github_pat
        )
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.up_to_date.connect(self._on_up_to_date)
        self._update_checker.check_failed.connect(self._on_check_failed)
        self._update_checker.check()

    def _on_update_available(self, current, new_ver, url):
        self._save_last_checked()
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")
        self._update_status_pill.setText(f"v{new_ver} available")
        self._update_status_pill.setStyleSheet(self._pill_style(ALMA_WARNING, "245,158,11"))
        self._update_msg.setText(
            f"Version {new_ver} is available (you have v{current})."
        )
        self._update_msg.setVisible(True)
        self._latest_release_url = url
        self._latest_new_version = new_ver
        self._release_notes_btn.setEnabled(bool(url))
        # Build download URL from release page URL
        # GitHub pattern: html_url ends with /releases/tag/vX.Y.Z
        # Download: /releases/download/vX.Y.Z/alma-insights-vX.Y.Z.zip
        self._latest_download_url = ""
        if url and "github.com" in url:
            base = url.rsplit("/releases/", 1)[0] if "/releases/" in url else ""
            if base:
                tag = f"v{new_ver}"
                self._latest_download_url = (
                    f"{base}/releases/download/{tag}/"
                    f"alma-insights-{tag}.zip"
                )
        self._install_btn.setVisible(bool(self._latest_download_url))

    def _on_up_to_date(self):
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")
        self._update_status_pill.setText("Up to date")
        self._update_status_pill.setStyleSheet(self._pill_style(ALMA_SUCCESS, "22,163,74"))
        self._update_msg.setText("You are running the latest version.")
        self._update_msg.setVisible(True)
        self._save_last_checked()

    def _on_check_failed(self, error_msg):
        self._check_updates_btn.setEnabled(True)
        self._check_updates_btn.setText("Check for Updates")
        self._update_status_pill.setText("Check failed")
        self._update_status_pill.setStyleSheet(self._pill_style(ALMA_ERROR, "220,38,38"))
        self._update_msg.setText(error_msg)
        self._update_msg.setVisible(True)
        self._save_last_checked()

    def _on_view_release_notes(self):
        url = getattr(self, "_latest_release_url", "")
        if url:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(url))

    def _on_install_update(self):
        """Download and stage the update.

        The install path resolves the platform-specific zip + its SHA-256
        from the release's ``manifest.json`` before calling
        :meth:`Updater.stage` (the ``require_checksum=True`` guard refuses
        an unchecksummed install).
        """
        new_ver = getattr(self, "_latest_new_version", "")

        # Show progress + hide install button up front — keeps the UI
        # responsive even if the manifest fetch takes a moment.
        self._install_btn.setVisible(False)
        self._update_progress.setValue(0)
        self._update_progress.setVisible(True)
        self._progress_label.setText("Resolving release manifest...")
        self._progress_label.setVisible(True)

        try:
            url, sha, _size = self._resolve_install_artifact()
        except Exception as exc:  # noqa: BLE001 — surface the message
            self._on_update_failed(str(exc))
            return

        from src.updater.updater import Updater
        self._updater = Updater(parent=self)
        self._updater.progress.connect(self._on_update_progress)
        self._updater.complete.connect(self._on_update_complete)
        self._updater.failed.connect(self._on_update_failed)
        self._progress_label.setText("Starting download...")
        # The token is REQUIRED for a private repo — the asset 404s without it.
        checker = getattr(self, "_update_checker", None)
        token = getattr(checker, "last_token", "") if checker else ""
        self._updater.stage(url, expected_sha256=sha, new_version=new_ver,
                            token=token)

    def _resolve_install_artifact(self):
        """Look up download URL + SHA-256 for the running platform.

        Returns ``(download_url, sha256, size_bytes_or_none)``. Raises
        :class:`ManifestFetchError` (or a generic Exception with a
        readable message) on any failure — caller funnels that into
        :meth:`_on_update_failed`.
        """
        from src.updater.manifest_fetcher import resolve_release_artifact

        # Pull the assets array + token straight off the checker that
        # last fired `update_available`. Avoids a redundant GitHub call.
        checker = getattr(self, "_update_checker", None)
        assets = getattr(checker, "last_assets", None) if checker else None
        token = getattr(checker, "last_token", "") if checker else ""

        return resolve_release_artifact(assets, token=token)

    def _on_update_progress(self, pct, msg):
        self._update_progress.setValue(pct)
        self._progress_label.setText(msg)

    def _on_update_complete(self):
        self._update_progress.setVisible(False)
        self._progress_label.setText("Update staged. Restart to apply.")
        self._restart_btn.setVisible(True)

    def _on_update_failed(self, msg):
        self._update_progress.setVisible(False)
        self._progress_label.setVisible(False)
        self._update_msg.setText(f"Update failed: {msg}")
        self._update_msg.setVisible(True)
        self._install_btn.setVisible(True)

    def _on_restart_app(self):
        """Restart the application to apply the staged update.

        Uses :func:`src.updater.restart.restart_app`, which spawns the
        replacement process before quitting so the user lands back in the
        running app. Confirmed via a small modal — relaunching while work
        is mid-flight would lose it.
        """
        reply = QMessageBox.question(
            self,
            "Restart to apply update",
            "Restart Alma Insights now to apply the staged update?\n\n"
            "Any in-progress work in this session will be discarded.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        from src.updater.restart import restart_app
        try:
            restart_app()
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Restart failed",
                f"Could not restart automatically: {exc}\n\n"
                "Please close Alma Insights and reopen it manually — "
                "the staged update will apply on next launch.",
            )

    def _save_last_checked(self):
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        # MERGE — must not wipe github_repo (see _on_save_github_settings).
        update_section("updates", {"last_checked": ts})
        self._last_checked_label.setText(f"Last checked: {ts}")

    def _load_last_checked(self):
        section = get_section("updates")
        ts = section.get("last_checked", "")
        if ts:
            self._last_checked_label.setText(f"Last checked: {ts}")

    # ── style helpers (match the product Settings chrome) ───────────

    def _section_label(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            font-size: 10px; font-weight: 700; color: {ALMA_TEXT_LIGHT};
            letter-spacing: 1.2px; padding: 0 4px;
        """)
        return lbl

    def _card(self):
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background: {ALMA_BG_ELEVATED}; border: none;
                border-radius: 12px;
            }}
        """)
        apply_card_shadow(card)
        return card

    def _ghost_btn_style(self):
        return f"""
            QPushButton {{
                background: {ALMA_WHITE}; color: {ALMA_TEXT_DARK};
                border: 1px solid {ALMA_BORDER}; border-radius: 6px;
                padding: 6px 14px; font-size: 12px; font-weight: 600;
            }}
            QPushButton:hover {{ background: {ALMA_CREAM}; }}
            QPushButton:disabled {{ color: {ALMA_TEXT_LIGHT}; border-color: {ALMA_BORDER_LIGHT}; }}
        """

    @staticmethod
    def _pill_style(color: str, rgb: str) -> str:
        return f"""
            font-size: 11px; font-weight: 600; color: {color};
            background: rgba({rgb},0.1); border-radius: 10px;
            padding: 3px 10px;
        """
