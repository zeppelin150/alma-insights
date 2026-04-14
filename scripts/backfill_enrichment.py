"""
scripts/backfill_enrichment.py — Backfill Phase 1 enrichment from source CSVs.

Phase 1 deliverable (canonicalization-enrichment plan, drift fix #8).

Walks completed import_runs and re-extracts enrichment columns (insurance,
client_id, provider_id, agent_id, service_state, channel, session_date,
dispute_amount_usd, tags) into ticket_index + ticket_tags + entity registries.

Idempotent and resumeable via the backfill_state table (one-row checkpoint
keyed by run_id; the script resumes from the last completed import_run on
re-invocation). Use --restart to clear state and start from scratch.

The backfill does NOT re-ingest tickets (that would be destructive).
It only fills in the Phase 1 enrichment columns on rows that already exist
in ticket_index via the ensure_ticket_index_row writer. For import_runs whose
source file is no longer present, the run is skipped with a warning.

Usage:
    python scripts/backfill_enrichment.py
    python scripts/backfill_enrichment.py --restart
    python scripts/backfill_enrichment.py --db data/local_warehouse.db
    python scripts/backfill_enrichment.py --source-dir imports/
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from src.data.connection_factory import atomic, get_connection   # noqa: E402
from src.data.csv_ingestion import COLUMN_MAP, _normalize_header   # noqa: E402
from src.services.ticket_index_writer import (   # noqa: E402
    ENRICHMENT_META_KEYS,
    ensure_ticket_index_row,
)

logger = logging.getLogger("alma.backfill_enrichment")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "local_warehouse.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_checkpoint(conn) -> dict:
    """Read the single-row backfill_state record; create it on first call."""
    conn.execute(
        "INSERT OR IGNORE INTO backfill_state "
        "(id, last_import_run_id, rows_processed, runs_processed, "
        " started_at, updated_at, status) "
        "VALUES (1, NULL, 0, 0, NULL, ?, 'idle')",
        (_now_iso(),),
    )
    row = conn.execute(
        "SELECT last_import_run_id, rows_processed, runs_processed, "
        "started_at, status FROM backfill_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"last_import_run_id": None, "rows_processed": 0,
                "runs_processed": 0, "started_at": None, "status": "idle"}
    return dict(row) if hasattr(row, "keys") else {
        "last_import_run_id": row[0], "rows_processed": row[1],
        "runs_processed": row[2], "started_at": row[3], "status": row[4],
    }


def _save_checkpoint(conn, *, run_id: str, rows_delta: int,
                     status: str = "running") -> None:
    conn.execute(
        """
        UPDATE backfill_state
           SET last_import_run_id = ?,
               rows_processed     = rows_processed + ?,
               runs_processed     = runs_processed + 1,
               updated_at         = ?,
               status             = ?
         WHERE id = 1
        """,
        (run_id, rows_delta, _now_iso(), status),
    )


def _mark_started(conn) -> None:
    conn.execute(
        "UPDATE backfill_state SET started_at = ?, status = 'running' "
        "WHERE id = 1 AND (started_at IS NULL OR status != 'running')",
        (_now_iso(),),
    )


def _mark_done(conn, status: str) -> None:
    conn.execute(
        "UPDATE backfill_state SET status = ?, updated_at = ? WHERE id = 1",
        (status, _now_iso()),
    )


def _clear_checkpoint(conn) -> None:
    conn.execute(
        "UPDATE backfill_state SET last_import_run_id = NULL, "
        "rows_processed = 0, runs_processed = 0, started_at = NULL, "
        "status = 'idle', updated_at = ? WHERE id = 1",
        (_now_iso(),),
    )


# ─── CSV re-extraction ─────────────────────────────────────────────────────

def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """Read CSV with encoding fallback. Mirrors csv_ingestion._read_csv_with_encoding."""
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Could not decode CSV: {path}")
    reader = csv.reader(io.StringIO(text))
    headers = next(reader)
    return headers, list(reader)


def _map_enrichment_cols(headers: list[str]) -> dict[int, str]:
    """Return {col_index: field_name} for enrichment-relevant columns only."""
    wanted = set(ENRICHMENT_META_KEYS) | {"ticket_id", "tags"}
    mapping: dict[int, str] = {}
    for i, h in enumerate(headers):
        target = COLUMN_MAP.get(_normalize_header(h))
        if target and target in wanted:
            mapping[i] = target
    return mapping


def _iter_enrichment_rows(path: Path):
    """Yield (ticket_id, meta_dict) per row, enrichment columns only."""
    headers, rows = _read_csv(path)
    mapping = _map_enrichment_cols(headers)
    if "ticket_id" not in mapping.values():
        raise ValueError(f"ticket_id column missing in {path}")

    # Collapse multi-row tickets — first non-empty value per field wins
    per_ticket: dict[str, dict] = {}
    for row in rows:
        record = {
            mapping[i]: (row[i].strip() if row[i] else "")
            for i in mapping if i < len(row)
        }
        tid = record.get("ticket_id", "")
        if not tid:
            continue
        slot = per_ticket.setdefault(tid, {})
        for k, v in record.items():
            if k == "ticket_id" or not v:
                continue
            if not slot.get(k):
                slot[k] = v

    for tid, meta in per_ticket.items():
        # Coerce numeric
        if "dispute_amount_usd" in meta:
            try:
                meta["dispute_amount_usd"] = float(meta["dispute_amount_usd"])
            except (ValueError, TypeError):
                meta.pop("dispute_amount_usd", None)
        yield tid, meta


def _backfill_import_run(conn, run_id: str, file_name: str,
                         source_dir: Path) -> int:
    """Re-read a single import_run's source CSV and re-apply enrichment.

    Returns rows processed.
    """
    csv_path = source_dir / file_name
    if not csv_path.exists():
        logger.warning("Run %s: source file %s not found — skipping", run_id, file_name)
        return 0

    count = 0
    try:
        for tid, meta in _iter_enrichment_rows(csv_path):
            try:
                ensure_ticket_index_row(tid, meta, conn)
                count += 1
            except Exception as exc:   # noqa: BLE001 — keep the loop alive
                logger.warning("Row %s in %s failed: %s", tid, file_name, exc)
    except Exception as exc:   # noqa: BLE001 — file-level failure still commits partial
        logger.error("Failed to read %s: %s", csv_path, exc)
    return count


# ─── driver ────────────────────────────────────────────────────────────────

def backfill(db_path: Path, source_dir: Path, restart: bool = False) -> dict:
    """Execute the backfill. Returns summary dict."""
    conn = get_connection(db_path, for_writer=True)
    try:
        if restart:
            with atomic(conn):
                _clear_checkpoint(conn)
                logger.info("Checkpoint cleared")

        checkpoint = _load_checkpoint(conn)
        last_run_id = checkpoint["last_import_run_id"]

        # Fetch import_runs after the checkpoint, completed, CSV-sourced
        if last_run_id:
            rows = conn.execute(
                "SELECT run_id, file_name, started_at FROM import_runs "
                "WHERE status = 'completed' AND source = 'csv' "
                "AND started_at > COALESCE("
                "  (SELECT started_at FROM import_runs WHERE run_id = ?), ''"
                ") "
                "ORDER BY started_at",
                (last_run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT run_id, file_name, started_at FROM import_runs "
                "WHERE status = 'completed' AND source = 'csv' "
                "ORDER BY started_at"
            ).fetchall()

        if not rows:
            logger.info("No pending import_runs to backfill")
            return {"runs": 0, "rows": 0, "status": "nothing_to_do"}

        with atomic(conn):
            _mark_started(conn)

        total_rows = 0
        runs_done = 0
        for row in rows:
            run_id = row[0] if not hasattr(row, "keys") else row["run_id"]
            file_name = row[1] if not hasattr(row, "keys") else row["file_name"]
            if not file_name:
                continue
            logger.info("Backfilling run %s (file=%s)", run_id, file_name)
            with atomic(conn):
                processed = _backfill_import_run(conn, run_id, file_name, source_dir)
                _save_checkpoint(conn, run_id=run_id, rows_delta=processed)
            total_rows += processed
            runs_done += 1

        with atomic(conn):
            _mark_done(conn, "completed")

        return {"runs": runs_done, "rows": total_rows, "status": "completed"}
    except Exception:
        try:
            with atomic(conn):
                _mark_done(conn, "failed")
        except Exception:   # noqa: BLE001 — best effort
            pass
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill Phase 1 enrichment columns.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--source-dir", type=Path, default=Path.cwd(),
                   help="Directory containing the original CSV files")
    p.add_argument("--restart", action="store_true",
                   help="Clear checkpoint before running")
    args = p.parse_args(argv)

    summary = backfill(args.db, args.source_dir, restart=args.restart)
    logger.info("Backfill summary: %s", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
