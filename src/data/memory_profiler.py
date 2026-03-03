"""
Alma Insights — Memory Profiler Diagnostic Utility

Lightweight profiler for diagnosing memory bloat.
Uses tracemalloc (stdlib) for allocation tracking and gc for reference analysis.

Usage:
    from src.data.memory_profiler import MemoryProfiler

    # At app startup:
    MemoryProfiler.start()

    # Take a snapshot at any point:
    report = MemoryProfiler.snapshot()
    print(report)
"""

import gc
import sys
import tracemalloc
import os
from collections import defaultdict
from datetime import datetime


class MemoryProfiler:
    """Singleton memory profiler for Alma Insights."""

    _started = False
    _snapshots = []          # list of (timestamp, tracemalloc.Snapshot)
    _baseline_snapshot = None
    _MAX_GC_OBJECTS = 100_000  # Cap GC iteration to prevent UI freeze on main thread

    # ── Lifecycle ────────────────────────────────

    @classmethod
    def start(cls):
        """Start tracemalloc tracking.  Call once at app startup."""
        if cls._started:
            return
        tracemalloc.start(3)   # 3 frames — good attribution without startup penalty (10 destroys startup)
        cls._started = True
        cls._baseline_snapshot = tracemalloc.take_snapshot()

    @classmethod
    def is_started(cls):
        return cls._started

    # ── Snapshot ─────────────────────────────────

    @classmethod
    def snapshot(cls):
        """Take a memory snapshot and return a structured report dict.

        Returns
        -------
        dict with keys:
            timestamp       : ISO timestamp
            process_rss_mb  : Total process RSS from OS (if psutil available)
            tracemalloc_mb  : Current tracemalloc-tracked memory in MB
            tracemalloc_peak_mb : Peak tracemalloc-tracked memory in MB
            gc_stats        : gc.get_stats() list
            gc_objects      : Total tracked GC objects
            top_allocations : Top 20 allocation sites by size
            top_types       : Top 30 object types by count
            alma_objects    : Alma-specific object analysis
            large_dicts     : Dicts larger than 1MB (sampled)
            large_lists     : Lists larger than 1MB (sampled)
            delta_from_baseline_mb : Memory growth since start (if baseline exists)
        """
        if not cls._started:
            return {"error": "MemoryProfiler not started. Call MemoryProfiler.start() first."}

        now = datetime.now().isoformat(timespec="seconds")

        # ── 1. Process-level memory (OS) ──
        rss_mb = cls._get_process_rss_mb()

        # ── 2. GC collect first — free dead objects so snapshot reflects live memory only ──
        gc.collect()

        # ── 3. tracemalloc stats (after GC so we only report truly live allocations) ──
        current, peak = tracemalloc.get_traced_memory()
        snap = tracemalloc.take_snapshot()
        snap_filtered = snap.filter_traces([
            tracemalloc.Filter(False, "<frozen importlib._bootstrap>"),
            tracemalloc.Filter(False, "<frozen importlib._bootstrap_external>"),
            tracemalloc.Filter(False, tracemalloc.__file__) if hasattr(tracemalloc, '__file__') else tracemalloc.Filter(True, "*"),
        ])

        # Top allocations by file:line
        top_stats = snap_filtered.statistics("lineno")[:30]
        top_allocations = []
        for stat in top_stats:
            top_allocations.append({
                "location": str(stat),
                "size_mb": round(stat.size / (1024 * 1024), 2),
                "count": stat.count,
            })

        # Delta from baseline
        delta_mb = None
        if cls._baseline_snapshot is not None:
            try:
                diffs = snap_filtered.compare_to(cls._baseline_snapshot, "lineno")
                top_growth = []
                for d in sorted(diffs, key=lambda x: x.size_diff, reverse=True)[:20]:
                    if d.size_diff > 100_000:  # Only show > 100 KB growth
                        top_growth.append({
                            "location": str(d),
                            "size_diff_mb": round(d.size_diff / (1024 * 1024), 2),
                            "count_diff": d.count_diff,
                        })
                delta_mb = round((current - (cls._baseline_snapshot.traces._total if hasattr(cls._baseline_snapshot, 'traces') else 0)) / (1024 * 1024), 2)
            except Exception:
                top_growth = []
                delta_mb = None
        else:
            top_growth = []

        # ── 4. GC analysis (gc.collect() already ran above before snapshot) ──
        gc_stats = gc.get_stats()
        gc_objects_total = len(gc.get_objects())

        # ── 5. Object type census (capped to prevent UI freeze) ──
        type_counts = defaultdict(lambda: {"count": 0, "total_size": 0})
        for i, obj in enumerate(gc.get_objects()):
            if i >= cls._MAX_GC_OBJECTS:
                break
            try:
                t = type(obj).__name__
                sz = sys.getsizeof(obj)
                type_counts[t]["count"] += 1
                type_counts[t]["total_size"] += sz
            except (TypeError, ReferenceError):
                pass

        top_types = sorted(
            type_counts.items(),
            key=lambda kv: kv[1]["total_size"],
            reverse=True,
        )[:30]
        top_types_formatted = [
            {
                "type": name,
                "count": data["count"],
                "total_mb": round(data["total_size"] / (1024 * 1024), 2),
            }
            for name, data in top_types
        ]

        # ── 6. Alma-specific object hunt ──
        alma_objects = cls._analyze_alma_objects()

        # ── 7. Large containers ──
        large_dicts, large_lists = cls._find_large_containers()

        # Store snapshot for later comparison
        cls._snapshots.append((now, snap))
        if len(cls._snapshots) > 10:
            cls._snapshots.pop(0)

        return {
            "timestamp": now,
            "process_rss_mb": rss_mb,
            "tracemalloc_mb": round(current / (1024 * 1024), 2),
            "tracemalloc_peak_mb": round(peak / (1024 * 1024), 2),
            "gc_stats": gc_stats,
            "gc_objects_total": gc_objects_total,
            "top_allocations": top_allocations[:20],
            "top_growth_since_start": top_growth,
            "top_types": top_types_formatted,
            "alma_objects": alma_objects,
            "large_dicts_count": large_dicts["count"],
            "large_dicts_top5": large_dicts["top5"],
            "large_lists_count": large_lists["count"],
            "large_lists_top5": large_lists["top5"],
        }

    # ── Formatted text report ─────────────────────

    @classmethod
    def format_report(cls, report=None):
        """Return a human-readable text report.

        Parameters
        ----------
        report : dict | None
            If None, takes a fresh snapshot.
        """
        if report is None:
            report = cls.snapshot()
        if "error" in report:
            return report["error"]

        lines = []
        lines.append("=" * 70)
        lines.append(f"  ALMA INSIGHTS — MEMORY DIAGNOSTIC REPORT")
        lines.append(f"  {report['timestamp']}")
        lines.append("=" * 70)

        # Process memory
        lines.append("")
        lines.append(f"  Process RSS:          {report['process_rss_mb'] or '(psutil not available)'} MB")
        lines.append(f"  Tracemalloc Current:  {report['tracemalloc_mb']} MB")
        lines.append(f"  Tracemalloc Peak:     {report['tracemalloc_peak_mb']} MB")
        lines.append(f"  GC Tracked Objects:   {report['gc_objects_total']:,}")

        # GC generations
        lines.append("")
        lines.append("  GC Generation Stats:")
        for i, gen in enumerate(report.get("gc_stats", [])):
            lines.append(f"    Gen {i}: collections={gen.get('collections', '?')}, "
                         f"collected={gen.get('collected', '?')}, "
                         f"uncollectable={gen.get('uncollectable', '?')}")

        # Top types by total size
        lines.append("")
        lines.append("  Top Object Types by Total Size:")
        lines.append(f"  {'Type':<30} {'Count':>10} {'Total MB':>10}")
        lines.append("  " + "-" * 52)
        for t in report.get("top_types", []):
            lines.append(f"  {t['type']:<30} {t['count']:>10,} {t['total_mb']:>10.2f}")

        # Top allocations
        lines.append("")
        lines.append("  Top Allocation Sites (tracemalloc):")
        for a in report.get("top_allocations", [])[:15]:
            lines.append(f"    {a['size_mb']:>8.2f} MB  ({a['count']:>6,} blocks)  {a['location']}")

        # Growth since start
        growth = report.get("top_growth_since_start", [])
        if growth:
            lines.append("")
            lines.append("  Top Memory Growth Since App Start:")
            for g in growth[:10]:
                sign = "+" if g["size_diff_mb"] >= 0 else ""
                lines.append(f"    {sign}{g['size_diff_mb']:>8.2f} MB  ({g['count_diff']:>+6,} blocks)  {g['location']}")

        # Alma-specific objects
        alma = report.get("alma_objects", {})
        if alma:
            lines.append("")
            lines.append("  Alma-Specific Object Analysis:")
            for key, val in alma.items():
                if isinstance(val, dict):
                    lines.append(f"    {key}:")
                    for k2, v2 in val.items():
                        lines.append(f"      {k2}: {v2}")
                else:
                    lines.append(f"    {key}: {val}")

        # Large containers
        lines.append("")
        lines.append(f"  Large Dicts (>1 MB): {report.get('large_dicts_count', 0)}")
        for d in report.get("large_dicts_top5", []):
            lines.append(f"    {d['size_mb']:.2f} MB  keys={d['num_keys']}  sample_keys={d['sample_keys']}")

        lines.append(f"  Large Lists (>1 MB): {report.get('large_lists_count', 0)}")
        for l in report.get("large_lists_top5", []):
            lines.append(f"    {l['size_mb']:.2f} MB  len={l['length']}  item_type={l['item_type']}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    # ── Internal helpers ──────────────────────────

    @classmethod
    def _get_process_rss_mb(cls):
        """Get process RSS memory via psutil or OS fallback."""
        try:
            import psutil
            proc = psutil.Process(os.getpid())
            return round(proc.memory_info().rss / (1024 * 1024), 1)
        except ImportError:
            pass

        # Windows fallback without psutil
        try:
            import ctypes
            from ctypes import wintypes

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            handle = kernel32.GetCurrentProcess()
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                return round(pmc.WorkingSetSize / (1024 * 1024), 1)
        except Exception:
            pass

        return None

    @classmethod
    def _analyze_alma_objects(cls):
        """Hunt for Alma-specific objects that may be holding memory."""
        results = {}

        # Look for conversation-like dicts (have 'ticket_id' and 'full_thread')
        conversation_dicts = 0
        conversation_dicts_with_thread = 0
        total_thread_bytes = 0
        thread_lengths = []

        # Look for result-like dicts (have 'sentiment' and 'terms')
        result_dicts = 0

        # QThread workers alive
        qthread_workers = []

        # _last_analysis_result holders
        analysis_result_holders = 0

        try:
            from PySide6.QtCore import QThread
        except ImportError:
            QThread = None

        for i, obj in enumerate(gc.get_objects()):
            if i >= cls._MAX_GC_OBJECTS:
                break
            try:
                if isinstance(obj, dict):
                    keys = set(obj.keys()) if len(obj) < 50 else set()

                    # Conversation dict detection
                    if "ticket_id" in keys and "created_at" in keys:
                        conversation_dicts += 1
                        thread = obj.get("full_thread")
                        if thread and isinstance(thread, str) and len(thread) > 10:
                            conversation_dicts_with_thread += 1
                            total_thread_bytes += len(thread.encode("utf-8", errors="ignore"))
                            thread_lengths.append(len(thread))

                    # Analysis result dict detection
                    if "sentiment" in keys and "terms" in keys:
                        result_dicts += 1

                # QThread subclass detection
                if QThread is not None and isinstance(obj, QThread) and type(obj).__name__ != "QThread":
                    status = "running" if obj.isRunning() else "finished"
                    qthread_workers.append({
                        "class": type(obj).__name__,
                        "status": status,
                        "referrers": len(gc.get_referrers(obj)),
                    })
            except (TypeError, ReferenceError, RuntimeError):
                pass

        results["conversation_dicts_total"] = conversation_dicts
        results["conversation_dicts_with_full_thread"] = conversation_dicts_with_thread
        results["full_thread_total_mb"] = round(total_thread_bytes / (1024 * 1024), 2)
        if thread_lengths:
            results["full_thread_avg_chars"] = round(sum(thread_lengths) / len(thread_lengths))
            results["full_thread_max_chars"] = max(thread_lengths)
        results["analysis_result_dicts"] = result_dicts
        results["qthread_workers"] = qthread_workers

        return results

    @classmethod
    def _find_large_containers(cls, threshold_bytes=1_000_000):
        """Find dicts and lists larger than threshold."""
        large_dicts = {"count": 0, "top5": []}
        large_lists = {"count": 0, "top5": []}

        dict_candidates = []
        list_candidates = []

        for i, obj in enumerate(gc.get_objects()):
            if i >= cls._MAX_GC_OBJECTS:
                break
            try:
                if isinstance(obj, dict):
                    sz = sys.getsizeof(obj)
                    if sz > threshold_bytes:
                        large_dicts["count"] += 1
                        sample_keys = list(obj.keys())[:5]
                        dict_candidates.append({
                            "size_mb": round(sz / (1024 * 1024), 2),
                            "num_keys": len(obj),
                            "sample_keys": [str(k)[:40] for k in sample_keys],
                        })
                elif isinstance(obj, list):
                    sz = sys.getsizeof(obj)
                    if sz > threshold_bytes:
                        large_lists["count"] += 1
                        item_type = type(obj[0]).__name__ if obj else "empty"
                        list_candidates.append({
                            "size_mb": round(sz / (1024 * 1024), 2),
                            "length": len(obj),
                            "item_type": item_type,
                        })
            except (TypeError, ReferenceError, IndexError):
                pass

        dict_candidates.sort(key=lambda x: x["size_mb"], reverse=True)
        list_candidates.sort(key=lambda x: x["size_mb"], reverse=True)
        large_dicts["top5"] = dict_candidates[:5]
        large_lists["top5"] = list_candidates[:5]

        return large_dicts, large_lists
