"""
Alma Insights — Memory Trace: FULL APP Import Pipeline

Boots the real PySide6 app + MainWindow and traces memory
at every stage during CSV import. This catches Qt/UI-layer
bloat that the standalone pipeline test misses.

Usage:  python tests/trace_memory_app.py <csv_file>
"""

import sys
import os
import gc
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_rss_mb():
    """Get current process working set in MB (Windows)."""
    pid = os.getpid()
    out = subprocess.check_output(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        text=True, stderr=subprocess.DEVNULL,
    )
    for line in out.strip().splitlines():
        if str(pid) in line:
            parts = line.strip('"').split('","')
            if len(parts) >= 5:
                mem_str = parts[4].strip('"').replace(",", "").replace(" ", "")
                digits = "".join(c for c in mem_str if c.isdigit())
                if digits:
                    return round(int(digits) / 1024, 1)
    return 0.0


def cp(label, base=None):
    gc.collect()
    rss = get_rss_mb()
    delta = f"  (+{rss - base:.1f} MB)" if base is not None else ""
    print(f"  [{rss:>8.1f} MB]{delta}  {label}", flush=True)
    return rss


def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/trace_memory_app.py <csv_file>")
        sys.exit(1)

    csv_path = os.path.abspath(sys.argv[1])
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        sys.exit(1)

    print("=" * 70)
    print("  MEMORY TRACE: FULL APP IMPORT")
    print(f"  File: {csv_path}")
    print(f"  Size: {os.path.getsize(csv_path) / 1024:.1f} KB")
    print("=" * 70)

    base = cp("Baseline (bare Python)")

    # ── Boot Qt ──
    print("\n── Phase 1: Qt Application ──")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QTimer

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv[:1])  # strip our args
    cp("After QApplication()", base)

    from src.ui.theme import get_stylesheet
    app.setStyleSheet(get_stylesheet())
    cp("After stylesheet", base)

    # ── Boot MainWindow ──
    print("\n── Phase 2: MainWindow ──")
    from src.ui.main_window import MainWindow
    cp("After importing MainWindow", base)

    window = MainWindow()
    cp("After MainWindow()", base)

    window.show()
    app.processEvents()
    cp("After show() + processEvents()", base)

    # ── Wipe DB ──
    print("\n── Phase 3: Clear DB ──")
    window.db.conn.execute("DELETE FROM conversations")
    window.db.conn.execute("DELETE FROM comments")
    window.db.conn.execute("DELETE FROM tickets")
    window.db.conn.commit()
    app.processEvents()
    cp("After DB wipe", base)

    # ── Import CSV via the real code path ──
    print("\n── Phase 4: CSV Import (real code path) ──")

    # We'll bypass the file dialog and call _start_csv_import directly
    page = window.conversations_page
    page._pending_csv_path = csv_path
    page._pending_csv_fname = os.path.basename(csv_path)

    # Track completion
    import_done = {"done": False, "stats": None}
    original_done = page._on_csv_import_done

    def patched_done(stats):
        cp("  >> _on_csv_import_done ENTRY", base)
        original_done(stats)
        import_done["done"] = True
        import_done["stats"] = stats
        cp("  >> _on_csv_import_done EXIT", base)

    page._on_csv_import_done = patched_done

    # Also patch _on_data_loaded to trace it
    original_data_loaded = window._on_data_loaded

    def patched_data_loaded(stats):
        cp("  >> _on_data_loaded ENTRY", base)

        # Inline the steps so we can trace each one
        count = stats.get("tickets_created", stats.get("conversations", 0))
        window.ticket_count_label.setText(f"{count} conversations in database")
        cp("    After ticket_count_label update", base)

        # Refresh TRC filters
        window.dashboard_page.populate_trc_filter()
        window.trending_page.populate_trc_filter()
        window.incidents_page.populate_trc_filter()
        window.reports_page.populate_trc_filter()
        cp("    After populate_trc_filter (4 pages)", base)

        # Sync dates
        window.dashboard_page.sync_date_to_data()
        window.trending_page.sync_date_to_data()
        window.incidents_page.sync_date_to_data()
        window.reports_page.sync_date_to_data()
        window.conversations_page._sync_date_filters_to_data()
        cp("    After sync_date_to_data (5 pages)", base)

        # Daily/hourly counts
        window.db.populate_daily_counts()
        cp("    After populate_daily_counts()", base)

        window.db.populate_hourly_counts()
        cp("    After populate_hourly_counts()", base)

        # Auto-analysis (should be disabled, but let's trace it)
        aa = window._get_auto_analysis_config()
        print(f"    Auto-analysis config: {aa}", flush=True)
        window._queue_auto_analysis()
        cp("    After _queue_auto_analysis()", base)

        cp("  >> _on_data_loaded EXIT", base)

    window._on_data_loaded = patched_data_loaded

    # Start the import (no column_override = standard COLUMN_MAP)
    cp("Before _start_csv_import()", base)
    page._start_csv_import(None)
    cp("After _start_csv_import() [job queued]", base)

    # ── Pump event loop until import completes ──
    print("\n── Phase 5: Event Loop (waiting for import) ──")

    import time
    timeout = time.time() + 120  # 2 min max
    last_mem = 0
    while not import_done["done"] and time.time() < timeout:
        app.processEvents()
        time.sleep(0.1)

        # Print memory every time it changes by >50 MB
        current = get_rss_mb()
        if abs(current - last_mem) > 50:
            cp(f"Event loop (polling...)", base)
            last_mem = current

    app.processEvents()
    cp("Import complete — all events processed", base)

    # ── Post-import: let queue drain ──
    print("\n── Phase 6: Queue Drain ──")
    drain_timeout = time.time() + 60
    while window._job_queue.is_running() and time.time() < drain_timeout:
        app.processEvents()
        time.sleep(0.2)

    app.processEvents()
    cp("Queue drained", base)

    # ── Check what's in memory ──
    print("\n── Phase 7: Memory Audit ──")

    gc.collect()
    gc.collect()
    gc.collect()
    cp("After 3x gc.collect()", base)

    # Count conversation dicts in GC
    conv_dicts = 0
    conv_with_thread = 0
    thread_bytes = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, dict) and "ticket_id" in obj and "created_at" in obj:
                conv_dicts += 1
                ft = obj.get("full_thread")
                if ft and isinstance(ft, str) and len(ft) > 10:
                    conv_with_thread += 1
                    thread_bytes += len(ft)
        except (TypeError, ReferenceError):
            pass

    print(f"  Conversation dicts in GC:       {conv_dicts}")
    print(f"  ...with full_thread:            {conv_with_thread}")
    print(f"  full_thread total:              {thread_bytes / (1024*1024):.1f} MB")

    # Count large lists
    large_lists = []
    for obj in gc.get_objects():
        try:
            if isinstance(obj, list) and sys.getsizeof(obj) > 1_000_000:
                item_type = type(obj[0]).__name__ if obj else "empty"
                large_lists.append((sys.getsizeof(obj), len(obj), item_type))
        except (TypeError, ReferenceError, IndexError):
            pass

    large_lists.sort(reverse=True)
    print(f"  Large lists (>1 MB):            {len(large_lists)}")
    for sz, length, itype in large_lists[:5]:
        print(f"    {sz / (1024*1024):.1f} MB  len={length}  item_type={itype}")

    # Count large strings
    large_strings = 0
    large_string_bytes = 0
    for obj in gc.get_objects():
        try:
            if isinstance(obj, str) and len(obj) > 100_000:
                large_strings += 1
                large_string_bytes += len(obj)
        except (TypeError, ReferenceError):
            pass

    print(f"  Large strings (>100 KB):        {large_strings}")
    print(f"  Large strings total:            {large_string_bytes / (1024*1024):.1f} MB")

    # Check QThread workers
    try:
        from PySide6.QtCore import QThread
        alive_threads = []
        for obj in gc.get_objects():
            try:
                if isinstance(obj, QThread) and type(obj).__name__ != "QThread":
                    alive_threads.append(f"{type(obj).__name__} running={obj.isRunning()}")
            except (TypeError, ReferenceError, RuntimeError):
                pass
        print(f"  QThread subclasses alive:       {len(alive_threads)}")
        for t in alive_threads:
            print(f"    {t}")
    except Exception:
        pass

    # ── Final ──
    print("\n" + "=" * 70)
    final = get_rss_mb()
    print(f"  FINAL: {final:.1f} MB  (baseline was {base:.1f} MB, delta +{final - base:.1f} MB)")
    print("=" * 70)

    # Don't call app.exec() — just exit
    window.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
