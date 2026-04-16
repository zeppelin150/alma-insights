# src/updater/

> Auto-update system providing version checking against GitHub releases, ZIP download with SHA-256 verification, stage-and-apply update mechanism, and SQL schema migration for the SQLite database.

## Module Index

### schema_migrator.py
> Applies pending numbered SQL migration files to the SQLite database, tracking applied migrations in a `schema_migrations` table.

**Public API:**
- `SchemaMigrator.__init__(self, migrations_dir: Path | None = None)` — Initialize with optional custom migrations directory (defaults to `migrations/` at project root).
- `SchemaMigrator.migrate(self, conn: sqlite3.Connection) -> list[str]` — Run all pending migrations; returns list of applied filenames.
- `SchemaMigrator.current_version(self, conn: sqlite3.Connection) -> int` — Return the highest applied migration number, or 0.
- `SchemaMigrator.pending(self, conn: sqlite3.Connection) -> list[Path]` — Return list of migration files not yet applied, sorted by name.

**Depends on:** (none -- only stdlib `sqlite3`, `pathlib`)
**Depended by:** `src.data.db_manager`, `tests.test_updater`

---

### update_checker.py
> Checks the GitHub releases API for a newer version of Alma Insights in a background thread, emitting Qt Signals for update-available, up-to-date, or check-failed.

**Public API:**
- `UpdateChecker.__init__(self, parent=None, *, releases_url: str | None = None, github_pat: str | None = None)` — Initialize with optional custom releases URL and GitHub PAT for private repos.
- `UpdateChecker.check(self)` — Launch a background thread to check for updates.
- `UpdateChecker.update_available` — Signal(str, str, str) emitting (current_version, new_version, release_url).
- `UpdateChecker.up_to_date` — Signal() emitted when already on latest version.
- `UpdateChecker.check_failed` — Signal(str) emitted with error message on failure.

**Depends on:** `src` (VERSION)
**Depended by:** `src.ui.pages.settings_page`, `tests.test_updater`

---

### updater.py
> Downloads a release ZIP from GitHub, verifies its SHA-256 checksum, and stages it for application on next launch using a backup-swap-cleanup pattern safe for Windows file locking.

**Public API:**
- `has_staged_update() -> bool` — Return True if a staged update is waiting to be applied.
- `apply_staged_update() -> bool` — Apply a previously staged update (call early in main.py before importing src modules); returns True if applied.
- `Updater.__init__(self, parent=None)` — Initialize the QObject-based updater.
- `Updater.stage(self, download_url: str, expected_sha256: str = "", new_version: str = "")` — Download and stage an update in a background thread.
- `Updater.cancel(self)` — Request cancellation of an in-progress download.
- `Updater.progress` — Signal(int, str) emitting (percent 0-100, step_description).
- `Updater.complete` — Signal() emitted when staging succeeds and restart is needed.
- `Updater.failed` — Signal(str) emitted with error message on failure.

**Depends on:** `src` (VERSION)
**Depended by:** `main`, `src.ui.pages.settings_page`, `tests.test_updater`

---
