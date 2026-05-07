"""
Alma Insights — Lightdash Client
Saved-chart ingestion: URL parse → preflight → chunked pull → SQLite.
Only saved charts are supported (never dashboards).
"""

import ipaddress
import json
import re
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Callable
from urllib.parse import urlparse

from src.data.run_logger import RunLogger
from src.data.pat_store import redact_pat


# ═══════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════

# Default trusted base — overridable in settings
DEFAULT_LIGHTDASH_BASE = "https://alma.lightdash.cloud"

# Max rows per chunk before we split the date range
SAFE_MAX_ROWS = 90_000

# Hard cap: total rows per run (safety valve — override with explicit flag)
HARD_MAX_TOTAL_ROWS = 500_000

# Date range thresholds (days)
DEFAULT_DATE_RANGE_DAYS = 7
LARGE_PULL_WARNING_DAYS = 30

# Pagination defaults
DEFAULT_PAGE_SIZE = 5000
MAX_RETRIES = 5
INITIAL_BACKOFF_MS = 1000
MAX_BACKOFF_MS = 60_000

# UUID regex
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

# Singleton job lock — only one ingestion at a time
import threading
_INGESTION_LOCK = threading.Lock()


# ═══════════════════════════════════
#  URL PARSING (NO NETWORK CALLS)
# ═══════════════════════════════════

class ParsedChartURL:
    """Result of parsing a Lightdash saved-chart URL."""
    __slots__ = ("host", "project_uuid", "chart_uuid", "valid", "error")

    def __init__(self, host="", project_uuid="", chart_uuid="", valid=False, error=""):
        self.host = host
        self.project_uuid = project_uuid
        self.chart_uuid = chart_uuid
        self.valid = valid
        self.error = error


def parse_chart_url(url: str) -> ParsedChartURL:
    """
    Parse a Lightdash saved-chart URL. String-only — NO network calls.

    Expected format:
        https://<host>/projects/<project_uuid>/saved/<chart_uuid>/view
    """
    url = url.strip()
    if not url:
        return ParsedChartURL(error="URL is empty")

    parsed = urlparse(url)

    # Scheme must be https
    if parsed.scheme != "https":
        return ParsedChartURL(error=f"Scheme must be https, got '{parsed.scheme}'")

    # No credentials in URL
    if parsed.username or parsed.password:
        return ParsedChartURL(error="URL must not contain username/password")

    host = parsed.hostname or ""
    if not host:
        return ParsedChartURL(error="No host found in URL")

    # Reject localhost / private IPs
    if _is_private_host(host):
        return ParsedChartURL(error=f"Host '{host}' resolves to private/localhost address")

    # Extract path segments: /projects/<uuid>/saved/<uuid>/view
    path = parsed.path.rstrip("/")
    segments = [s for s in path.split("/") if s]

    project_uuid = ""
    chart_uuid = ""

    for i, seg in enumerate(segments):
        if seg == "projects" and i + 1 < len(segments):
            project_uuid = segments[i + 1]
        if seg == "saved" and i + 1 < len(segments):
            chart_uuid = segments[i + 1]

    if not project_uuid:
        return ParsedChartURL(error="Could not find project UUID in URL path")
    if not chart_uuid:
        return ParsedChartURL(error="Could not find saved chart UUID in URL path")

    if not _UUID_RE.match(project_uuid):
        return ParsedChartURL(error=f"Project UUID is not valid: '{project_uuid}'")
    if not _UUID_RE.match(chart_uuid):
        return ParsedChartURL(error=f"Chart UUID is not valid: '{chart_uuid}'")

    return ParsedChartURL(
        host=host,
        project_uuid=project_uuid,
        chart_uuid=chart_uuid,
        valid=True,
    )


def _is_private_host(host: str) -> bool:
    """Check if host is localhost or a private IP."""
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_reserved
    except ValueError:
        pass  # It's a hostname, not an IP — that's fine
    return False


def validate_trusted_host(parsed: ParsedChartURL, trusted_base: str) -> Tuple[bool, str]:
    """
    Verify the parsed URL host matches the trusted Lightdash base.
    Returns (ok, error_message).
    """
    trusted_parsed = urlparse(trusted_base)
    trusted_host = (trusted_parsed.hostname or "").lower()
    url_host = parsed.host.lower()

    if not trusted_host:
        return False, "Trusted base URL has no host configured"
    if url_host != trusted_host:
        return False, f"Host mismatch: URL has '{url_host}', trusted is '{trusted_host}'"
    return True, ""


# ═══════════════════════════════════
#  PREFLIGHT (TEST CONNECTION)
# ═══════════════════════════════════

class PreflightResult:
    """Result of a preflight check step."""
    __slots__ = ("step", "passed", "detail", "duration_ms")

    def __init__(self, step: str, passed: bool, detail: str = "", duration_ms: int = 0):
        self.step = step
        self.passed = passed
        self.detail = detail
        self.duration_ms = duration_ms


def run_preflight(
    pat: str,
    chart_url: str,
    trusted_base: str = DEFAULT_LIGHTDASH_BASE,
    logger: Optional[RunLogger] = None,
    http_client=None,
) -> List[PreflightResult]:
    """
    Run the preflight checklist. Returns list of step results.

    Args:
        pat: Lightdash PAT
        chart_url: Saved chart URL
        trusted_base: Trusted Lightdash base URL
        logger: RunLogger for structured logging
        http_client: HTTP client (injectable for testing/mocking)
    """
    results = []
    log = logger or RunLogger(run_type="preflight")
    log.start(detail="Starting preflight checks")

    # ── Step 1: Token present ──
    if pat and len(pat) > 8:
        results.append(PreflightResult(
            "Token present", True, f"PAT: {redact_pat(pat)}"
        ))
        log.preflight("token_check", detail=f"PAT present: {redact_pat(pat)}")
    else:
        results.append(PreflightResult(
            "Token present", False, "No PAT provided or PAT too short"
        ))
        log.preflight("token_check", status="FAIL", detail="No valid PAT")
        log.end(status="FAIL", detail="Preflight failed: no token")
        return results

    # ── Step 2: URL parse + UUID extraction ──
    parsed = parse_chart_url(chart_url)
    if parsed.valid:
        results.append(PreflightResult(
            "URL valid", True,
            f"project={parsed.project_uuid[:8]}… chart={parsed.chart_uuid[:8]}…"
        ))
        log.preflight("url_parse", detail=f"project={parsed.project_uuid}, chart={parsed.chart_uuid}")
    else:
        results.append(PreflightResult("URL valid", False, parsed.error))
        log.preflight("url_parse", status="FAIL", detail=parsed.error)
        log.end(status="FAIL", detail=f"URL parse failed: {parsed.error}")
        return results

    # ── Step 3: Trusted host match ──
    host_ok, host_err = validate_trusted_host(parsed, trusted_base)
    if host_ok:
        results.append(PreflightResult(
            "Trusted host", True, f"Host matches: {parsed.host}"
        ))
        log.preflight("host_check", detail=f"Matched: {parsed.host}")
    else:
        results.append(PreflightResult("Trusted host", False, host_err))
        log.preflight("host_check", status="FAIL", detail=host_err)
        log.end(status="FAIL", detail=f"Host check failed: {host_err}")
        return results

    # ── Step 4: Server reachability ──
    if http_client is None:
        try:
            import urllib.request
            http_client = _DefaultHTTPClient(trusted_base, pat)
        except Exception as e:
            results.append(PreflightResult("Server reachable", False, str(e)))
            log.preflight("reachability", status="FAIL", detail=str(e))
            log.end(status="FAIL")
            return results

    t0 = time.monotonic()
    try:
        resp_code, resp_body = http_client.get("/api/v1/org", timeout=15)
        dur = int((time.monotonic() - t0) * 1000)

        if resp_code == 200:
            results.append(PreflightResult(
                "Server reachable", True, f"200 OK in {dur}ms", dur
            ))
            log.preflight("reachability", detail=f"200 OK in {dur}ms",
                          http_method="GET", url_path="/api/v1/org", response_code=200)
        else:
            results.append(PreflightResult(
                "Server reachable", False, f"HTTP {resp_code}", dur
            ))
            log.preflight("reachability", status="FAIL", detail=f"HTTP {resp_code}",
                          response_code=resp_code)
            log.end(status="FAIL")
            return results
    except Exception as e:
        dur = int((time.monotonic() - t0) * 1000)
        results.append(PreflightResult("Server reachable", False, str(e), dur))
        log.preflight("reachability", status="FAIL", detail=str(e))
        log.end(status="FAIL")
        return results

    # ── Step 5: Auth + chart access (execute with LIMIT 1) ──
    t0 = time.monotonic()
    try:
        api_path = f"/api/v1/saved/{parsed.chart_uuid}/results"
        resp_code, resp_body = http_client.post(
            api_path,
            body={"limit": 1},
            timeout=30,
        )
        dur = int((time.monotonic() - t0) * 1000)

        if resp_code == 200:
            # Try to extract row count from response
            row_info = ""
            try:
                data = json.loads(resp_body) if isinstance(resp_body, str) else resp_body
                rows = data.get("results", {}).get("rows", [])
                row_info = f", got {len(rows)} sample row(s)"
            except Exception:
                pass
            results.append(PreflightResult(
                "Chart access", True, f"200 OK in {dur}ms{row_info}", dur
            ))
            log.preflight("chart_access", detail=f"Chart accessible, {dur}ms{row_info}",
                          http_method="POST", url_path=api_path, response_code=200)
        elif resp_code == 401:
            results.append(PreflightResult(
                "Chart access", False, "401 Unauthorized — token invalid or expired", dur
            ))
            log.preflight("chart_access", status="FAIL",
                          detail="401 Unauthorized", response_code=401)
        elif resp_code == 403:
            results.append(PreflightResult(
                "Chart access", False, "403 Forbidden — no access to this chart", dur
            ))
            log.preflight("chart_access", status="FAIL",
                          detail="403 Forbidden", response_code=403)
        elif resp_code == 404:
            results.append(PreflightResult(
                "Chart access", False, "404 Not Found — chart UUID not found", dur
            ))
            log.preflight("chart_access", status="FAIL",
                          detail="404 Not Found", response_code=404)
        else:
            results.append(PreflightResult(
                "Chart access", False, f"HTTP {resp_code}", dur
            ))
            log.preflight("chart_access", status="FAIL",
                          detail=f"HTTP {resp_code}", response_code=resp_code)
    except Exception as e:
        dur = int((time.monotonic() - t0) * 1000)
        results.append(PreflightResult("Chart access", False, str(e), dur))
        log.preflight("chart_access", status="FAIL", detail=str(e))

    all_passed = all(r.passed for r in results)
    log.end(status="OK" if all_passed else "FAIL",
            detail=f"Preflight {'passed' if all_passed else 'failed'}: {sum(r.passed for r in results)}/{len(results)} checks")

    return results


# ═══════════════════════════════════
#  INGESTION RUNNER
# ═══════════════════════════════════

class ChunkSpec:
    """A date-range chunk to ingest."""
    __slots__ = ("chunk_id", "date_start", "date_end", "status",
                 "rows_expected", "rows_ingested", "last_page")

    def __init__(self, date_start: str, date_end: str):
        self.chunk_id = uuid.uuid4().hex[:8]
        self.date_start = date_start
        self.date_end = date_end
        self.status = "pending"      # pending | running | done | failed
        self.rows_expected = 0
        self.rows_ingested = 0
        self.last_page = 0


def run_ingestion(
    pat: str,
    chart_url: str,
    date_start: str,
    date_end: str,
    db,
    trusted_base: str = DEFAULT_LIGHTDASH_BASE,
    logger: Optional[RunLogger] = None,
    http_client=None,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
    dry_run: bool = False,
    override_row_cap: bool = False,
) -> dict:
    """
    Full ingestion pipeline: preflight → chunk → paginate → store → rebuild.

    Args:
        pat: Lightdash PAT
        chart_url: Saved chart URL
        date_start: YYYY-MM-DD
        date_end: YYYY-MM-DD
        db: DatabaseManager
        trusted_base: Trusted Lightdash base
        logger: RunLogger instance
        http_client: Injectable HTTP client (for mocking)
        progress_callback: callable(message, percent)
        cancel_check: callable() -> bool, returns True if user cancelled
        dry_run: If True, only estimate row count — don't ingest
        override_row_cap: If True, bypass HARD_MAX_TOTAL_ROWS safety

    Returns:
        dict with ingestion stats (or estimate if dry_run)
    """
    # ── Guardrail: one job at a time ──
    if not _INGESTION_LOCK.acquire(blocking=False):
        raise RuntimeError(
            "Another ingestion job is already running. "
            "Wait for it to complete or cancel it first."
        )

    try:
        return _run_ingestion_inner(
            pat, chart_url, date_start, date_end, db,
            trusted_base, logger, http_client,
            progress_callback, cancel_check,
            dry_run, override_row_cap,
        )
    finally:
        _INGESTION_LOCK.release()


def _run_ingestion_inner(
    pat, chart_url, date_start, date_end, db,
    trusted_base, logger, http_client,
    progress_callback, cancel_check,
    dry_run, override_row_cap,
) -> dict:
    """Inner ingestion logic (called under lock)."""
    log = logger or RunLogger(dataset_name="ingestion", run_type="ingestion")
    log.start(detail=f"{'Estimate' if dry_run else 'Ingestion'}: {date_start} to {date_end}")

    def _progress(msg, pct=None):
        if progress_callback:
            progress_callback(msg, pct)

    def _cancelled():
        return cancel_check() if cancel_check else False

    # ── Preflight ──
    _progress("Running preflight checks...", 2)
    parsed = parse_chart_url(chart_url)
    if not parsed.valid:
        log.error("preflight", f"URL parse failed: {parsed.error}")
        log.end(status="FAIL")
        raise ValueError(f"Invalid chart URL: {parsed.error}")

    if http_client is None:
        http_client = _DefaultHTTPClient(trusted_base, pat)

    preflight_results = run_preflight(pat, chart_url, trusted_base, log, http_client)
    if not all(r.passed for r in preflight_results):
        failed = [r for r in preflight_results if not r.passed]
        msg = "; ".join(f"{r.step}: {r.detail}" for r in failed)
        log.error("preflight", f"Preflight failed: {msg}")
        log.end(status="FAIL")
        raise ConnectionError(f"Preflight failed: {msg}")

    _progress("Preflight passed", 5)

    # ── Ensure raw_rows table ──
    if not dry_run:
        _ensure_ingestion_tables(db)

    # ── Chunking: split-until-safe ──
    _progress("Calculating data volume...", 8)
    api_path = f"/api/v1/saved/{parsed.chart_uuid}/results"

    chunks = _split_until_safe(
        http_client, api_path, date_start, date_end, log, _cancelled
    )

    if not chunks:
        log.warn("chunking", "No chunks to ingest (empty date range or cancelled)")
        log.end(status="OK", detail="No data to ingest")
        return {"chunks": 0, "rows_written": 0, "conversations": 0,
                "estimated_rows": 0, "dry_run": dry_run}

    # ── Calculate total estimated rows ──
    estimated_total = sum(c.rows_expected for c in chunks)
    log.log("REQUEST", "chunk_plan",
            detail=f"Split into {len(chunks)} chunk(s), ~{estimated_total:,} rows estimated",
            total_results=estimated_total)

    # ── Dry run: return estimate only ──
    if dry_run:
        _progress(f"Estimate: ~{estimated_total:,} rows in {len(chunks)} chunk(s)", 100)
        log.end(status="OK", detail=f"Dry run estimate: {estimated_total:,} rows")
        return {
            "dry_run": True,
            "chunks": len(chunks),
            "estimated_rows": estimated_total,
            "rows_written": 0,
            "conversations": 0,
        }

    # ── Guardrail: hard cap on total rows ──
    if estimated_total > HARD_MAX_TOTAL_ROWS and not override_row_cap:
        msg = (
            f"Estimated {estimated_total:,} rows exceeds the safety limit of "
            f"{HARD_MAX_TOTAL_ROWS:,}. Narrow your date range or enable "
            f"'Override row cap' in Settings."
        )
        log.error("row_cap", msg)
        log.end(status="FAIL")
        raise ValueError(msg)

    _progress(f"Ingesting {len(chunks)} chunk(s), ~{estimated_total:,} rows...", 10)

    # ── Paginate each chunk ──
    total_written = 0
    for ci, chunk in enumerate(chunks):
        if _cancelled():
            log.warn("cancel", "User cancelled")
            break

        pct_base = 10 + int((ci / len(chunks)) * 70)
        _progress(f"Chunk {ci+1}/{len(chunks)}: {chunk.date_start} → {chunk.date_end}", pct_base)

        chunk.status = "running"
        log.log("PAGE", "chunk_start", chunk_id=chunk.chunk_id,
                date_start=chunk.date_start, date_end=chunk.date_end,
                total_results=chunk.rows_expected,
                detail=f"Chunk {ci+1}: {chunk.rows_expected:,} rows expected")

        try:
            written = _ingest_chunk_paginated(
                http_client, api_path, chunk, db, log, _cancelled,
                lambda msg, p: _progress(msg, pct_base + int((p or 0) * 0.7 / len(chunks)))
            )
            chunk.status = "done"
            chunk.rows_ingested = written
            total_written += written
            log.chunks_completed += 1
            log.db("chunk_complete", rows_written=written,
                    detail=f"Chunk {ci+1} done: {written:,} rows")

            # ── Guardrail: check running total against hard cap ──
            if total_written > HARD_MAX_TOTAL_ROWS and not override_row_cap:
                log.warn("row_cap_hit",
                         f"Hit row cap ({total_written:,}/{HARD_MAX_TOTAL_ROWS:,}), stopping")
                break

        except Exception as e:
            chunk.status = "failed"
            log.error("chunk_ingest", f"Chunk {ci+1} failed: {e}")
            # Continue with remaining chunks

    _progress("Committing to database...", 82)
    db.commit()

    # ── Conversation rebuild ──
    _progress("Rebuilding conversations...", 85)
    from src.data.conversation_rebuild import rebuild_conversations
    rebuild_stats = rebuild_conversations(db, log, progress_callback=lambda msg, pct: _progress(msg, 85 + int((pct or 0) * 0.12)))

    _progress("Building search index...", 98)
    db.rebuild_fts_index()

    stats = {
        "chunks": len(chunks),
        "chunks_completed": log.chunks_completed,
        "rows_written": total_written,
        "conversations": rebuild_stats.get("conversations_rebuilt", 0),
        "warnings": len(log._warnings),
        "errors": len(log._errors),
        "log_dir": str(log.log_dir) if log.log_dir else "",
    }

    _progress(f"Done — {stats['conversations']:,} conversations loaded", 100)
    log.rows_written = total_written
    log.conversations_rebuilt = stats["conversations"]
    log.end(status="OK" if not log._errors else "WARN",
            detail=f"{stats['conversations']:,} conversations from {total_written:,} rows")

    # Clear the Clear-&-Close session-visibility flag so Conversation
    # Search shows the newly imported conversations.
    if stats["conversations"] > 0:
        try:
            from src.services.clear_session import set_conversation_search_hidden
            set_conversation_search_hidden(str(db.db_path), False)
        except Exception:
            pass  # non-fatal

    return stats


# ═══════════════════════════════════
#  CHUNKING LOGIC
# ═══════════════════════════════════

def _split_until_safe(
    http_client, api_path: str,
    date_start: str, date_end: str,
    log: RunLogger,
    cancel_check: Callable,
) -> List[ChunkSpec]:
    """
    Recursively split date ranges until each chunk is under SAFE_MAX_ROWS.
    """
    queue = [ChunkSpec(date_start, date_end)]
    safe_chunks = []

    while queue:
        if cancel_check():
            return safe_chunks

        chunk = queue.pop(0)

        # Probe: execute chart with date filter, limit 1, read totalResults
        try:
            total = _probe_chunk_size(http_client, api_path, chunk.date_start, chunk.date_end, log)
        except Exception as e:
            log.error("probe", f"Failed to probe chunk {chunk.date_start}..{chunk.date_end}: {e}")
            chunk.rows_expected = 0
            safe_chunks.append(chunk)  # Try anyway
            continue

        chunk.rows_expected = total

        if total <= SAFE_MAX_ROWS:
            safe_chunks.append(chunk)
            log.log("REQUEST", "chunk_sized",
                    detail=f"{chunk.date_start}..{chunk.date_end}: {total:,} rows (safe)",
                    chunk_id=chunk.chunk_id, total_results=total)
        else:
            # Split into two halves
            ds = datetime.strptime(chunk.date_start, "%Y-%m-%d")
            de = datetime.strptime(chunk.date_end, "%Y-%m-%d")
            delta = (de - ds).days

            if delta <= 0:
                # Single day still too big — fail with clear message
                log.error("chunk_split",
                    f"Single day {chunk.date_start} has {total:,} rows (>{SAFE_MAX_ROWS:,}). "
                    f"This report needs additional narrowing filters beyond date range.")
                raise ValueError(
                    f"Date {chunk.date_start} alone has {total:,} rows which exceeds "
                    f"the {SAFE_MAX_ROWS:,} row limit. Add additional filters in Lightdash."
                )

            mid = ds + timedelta(days=delta // 2)
            mid_str = mid.strftime("%Y-%m-%d")
            next_day = (mid + timedelta(days=1)).strftime("%Y-%m-%d")

            log.log("REQUEST", "chunk_split",
                    detail=f"Splitting {chunk.date_start}..{chunk.date_end} ({total:,} rows) at {mid_str}",
                    total_results=total)

            queue.append(ChunkSpec(chunk.date_start, mid_str))
            queue.append(ChunkSpec(next_day, chunk.date_end))

    return safe_chunks


def _probe_chunk_size(
    http_client, api_path: str,
    date_start: str, date_end: str,
    log: RunLogger,
) -> int:
    """Execute chart with LIMIT 1 and date filter to get totalResults."""
    body = {
        "limit": 1,
        "invalidateCache": False,
        "filters": {
            "dimensions": [{
                "id": "created_date_filter",
                "target": {"fieldId": "tickets_created_at_day"},
                "operator": "inBetween",
                "values": [date_start, date_end],
            }]
        }
    }

    resp_code, resp_body = _request_with_retry(
        http_client, "POST", api_path, body=body, logger=log, action="probe_chunk"
    )

    data = json.loads(resp_body) if isinstance(resp_body, str) else resp_body
    # Lightdash returns metricQuery.totalResults or the rows array length
    total = (
        data.get("results", {}).get("metricQuery", {}).get("totalResults")
        or len(data.get("results", {}).get("rows", []))
    )
    return int(total) if total else 0


# ═══════════════════════════════════
#  PAGINATED INGESTION
# ═══════════════════════════════════

def _ingest_chunk_paginated(
    http_client, api_path: str,
    chunk: ChunkSpec, db, log: RunLogger,
    cancel_check: Callable,
    progress_callback: Optional[Callable] = None,
) -> int:
    """Paginate through a chunk and write rows to SQLite."""
    page = chunk.last_page  # Resume support
    total_written = 0
    page_size = DEFAULT_PAGE_SIZE

    while True:
        if cancel_check():
            break

        body = {
            "limit": page_size,
            "page": page + 1,  # Lightdash uses 1-based pages
            "invalidateCache": False,
            "filters": {
                "dimensions": [{
                    "id": "created_date_filter",
                    "target": {"fieldId": "tickets_created_at_day"},
                    "operator": "inBetween",
                    "values": [chunk.date_start, chunk.date_end],
                }]
            }
        }

        resp_code, resp_body = _request_with_retry(
            http_client, "POST", api_path, body=body, logger=log, action=f"page_{page+1}"
        )

        data = json.loads(resp_body) if isinstance(resp_body, str) else resp_body
        rows = data.get("results", {}).get("rows", [])

        if not rows:
            break

        # Write rows to raw_rows table
        written = _store_raw_rows(db, rows, chunk.chunk_id, log)
        total_written += written
        chunk.last_page = page + 1

        log.page(page + 1, len(rows), chunk_id=chunk.chunk_id,
                 rows_written=written)

        if progress_callback and chunk.rows_expected > 0:
            pct = min(100, int(total_written / chunk.rows_expected * 100))
            progress_callback(f"Page {page+1}: {total_written:,}/{chunk.rows_expected:,}", pct)

        # Commit periodically
        if total_written % 10000 < page_size:
            db.commit()

        page += 1

        # If we got fewer rows than page_size, we're done
        if len(rows) < page_size:
            break

    db.commit()
    return total_written


def _store_raw_rows(db, rows: list, chunk_id: str, log: RunLogger) -> int:
    """Insert raw API rows into raw_ingestion_rows. Idempotent via INSERT OR IGNORE."""
    written = 0
    for row in rows:
        # Flatten Lightdash row format: each field is {value: ..., ...}
        flat = {}
        for key, cell in row.items():
            if isinstance(cell, dict):
                flat[key] = cell.get("value", cell.get("raw", ""))
            else:
                flat[key] = cell

        # Generate a deterministic row key for idempotency
        ticket_id = str(flat.get("tickets_ticket_id", flat.get("ticket_id", "")))
        comment_body_hash = str(hash(str(flat.get("comments_body", flat.get("comment_body", "")))))[:12]
        event_ts = str(flat.get("ticket_update_details_created_est_raw",
                                flat.get("created_at", "")))
        row_key = f"{ticket_id}_{event_ts}_{comment_body_hash}"

        try:
            db.conn.execute("""
                INSERT OR IGNORE INTO raw_ingestion_rows
                    (row_key, chunk_id, ticket_id, raw_json, ingested_at)
                VALUES (?, ?, ?, ?, ?)
            """, (row_key, chunk_id, ticket_id, json.dumps(flat), datetime.now().isoformat()))
            written += 1
        except Exception as e:
            log.warn("db_insert", f"Row insert failed: {e}")

    return written


# ═══════════════════════════════════
#  HTTP RETRY LOGIC
# ═══════════════════════════════════

def _request_with_retry(
    http_client, method: str, path: str,
    body: dict = None, logger: RunLogger = None,
    action: str = "", max_retries: int = MAX_RETRIES,
) -> Tuple[int, any]:
    """Make an HTTP request with retry + exponential backoff."""
    log = logger
    backoff = INITIAL_BACKOFF_MS

    for attempt in range(1, max_retries + 1):
        try:
            if method == "GET":
                code, resp = http_client.get(path, timeout=60)
            else:
                code, resp = http_client.post(path, body=body, timeout=60)

            if log:
                log.request(action, http_method=method, url_path=path,
                            response_code=code, attempt=attempt)

            # Success
            if 200 <= code < 300:
                return code, resp

            # Auth failures: don't retry
            if code in (401, 403):
                raise PermissionError(f"HTTP {code}: authentication/permission error")

            # Rate limited
            if code == 429:
                retry_after = 0
                if isinstance(resp, dict):
                    retry_after = float(resp.get("retry-after", 0))
                wait_ms = int(retry_after * 1000) if retry_after else backoff

                if log:
                    log.backoff(action, attempt, wait_ms, retry_after,
                                detail=f"429 rate limited, waiting {wait_ms}ms")
                time.sleep(wait_ms / 1000)
                backoff = min(backoff * 2, MAX_BACKOFF_MS)
                continue

            # Server errors: retry with backoff
            if code >= 500:
                if log:
                    log.backoff(action, attempt, backoff,
                                detail=f"HTTP {code}, retrying in {backoff}ms")
                time.sleep(backoff / 1000)
                backoff = min(backoff * 2, MAX_BACKOFF_MS)
                continue

            # Other errors
            raise RuntimeError(f"HTTP {code}: unexpected response")

        except (PermissionError, ValueError):
            raise
        except Exception as e:
            if attempt >= max_retries:
                raise
            if log:
                log.backoff(action, attempt, backoff, detail=f"Error: {e}, retrying")
            time.sleep(backoff / 1000)
            backoff = min(backoff * 2, MAX_BACKOFF_MS)

    raise RuntimeError(f"Max retries ({max_retries}) exceeded for {action}")


# ═══════════════════════════════════
#  DB SCHEMA EXTENSION
# ═══════════════════════════════════

def _ensure_ingestion_tables(db):
    """Create ingestion-specific tables if they don't exist."""
    db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS raw_ingestion_rows (
            row_key         TEXT PRIMARY KEY,
            chunk_id        TEXT,
            ticket_id       TEXT,
            raw_json        TEXT,
            ingested_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS ingestion_chunks (
            chunk_id        TEXT PRIMARY KEY,
            date_start      TEXT,
            date_end        TEXT,
            status          TEXT DEFAULT 'pending',
            rows_expected   INTEGER DEFAULT 0,
            rows_ingested   INTEGER DEFAULT 0,
            last_page       INTEGER DEFAULT 0,
            started_at      TEXT,
            completed_at    TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_raw_rows_ticket ON raw_ingestion_rows(ticket_id);
    """)
    db.commit()


# ═══════════════════════════════════
#  DEFAULT HTTP CLIENT
# ═══════════════════════════════════

class _DefaultHTTPClient:
    """
    Minimal HTTP client using urllib. Only contacts the trusted base.
    PAT is sent in Authorization header, never in URLs.
    Redirects are NOT followed across hosts.
    """

    def __init__(self, base_url: str, pat: str):
        self.base_url = base_url.rstrip("/")
        self.pat = pat

    def get(self, path: str, timeout: int = 30) -> Tuple[int, any]:
        import urllib.request
        import urllib.error

        url = self.base_url + path
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"ApiKey {self.pat}")
        req.add_header("Accept", "application/json")

        try:
            # Custom opener that rejects cross-host redirects
            opener = urllib.request.build_opener(_SafeRedirectHandler(self.base_url))
            resp = opener.open(req, timeout=timeout)
            body = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception:
                pass
            return e.code, body
        except Exception as e:
            raise ConnectionError(f"Request failed: {e}")

    def post(self, path: str, body: dict = None, timeout: int = 30) -> Tuple[int, any]:
        import urllib.request
        import urllib.error

        url = self.base_url + path
        data = json.dumps(body or {}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"ApiKey {self.pat}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")

        try:
            opener = urllib.request.build_opener(_SafeRedirectHandler(self.base_url))
            resp = opener.open(req, timeout=timeout)
            body_text = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(body_text)
            except json.JSONDecodeError:
                return resp.status, body_text
        except urllib.error.HTTPError as e:
            body_text = ""
            try:
                body_text = e.read().decode("utf-8")
            except Exception:
                pass
            return e.code, body_text
        except Exception as e:
            raise ConnectionError(f"Request failed: {e}")


class _SafeRedirectHandler:
    """urllib redirect handler that rejects cross-host redirects."""

    def __init__(self, trusted_base: str):
        from urllib.parse import urlparse as _urlparse
        self._trusted_host = _urlparse(trusted_base).hostname

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        from urllib.parse import urlparse as _urlparse
        new_host = _urlparse(newurl).hostname
        if new_host != self._trusted_host:
            raise SecurityError(
                f"Redirect blocked: {self._trusted_host} → {new_host}"
            )
        # Allow same-host redirects
        import urllib.request
        return urllib.request.Request(newurl, headers=dict(req.headers))


class SecurityError(Exception):
    """Raised for SSRF / redirect safety violations."""
    pass
