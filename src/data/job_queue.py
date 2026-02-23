"""
Alma Insights — Unified Job Queue

Sequential job executor that runs one QThread worker at a time,
preventing concurrent DB access and UI freezes. All analysis and
import operations route through this queue.

AI Reports workers are intentionally excluded.
"""

import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional, Any

from PySide6.QtCore import QObject, QThread, Signal, Slot


# ═══════════════════════════════════════════
#  JOB DESCRIPTOR
# ═══════════════════════════════════════════

@dataclass
class JobDescriptor:
    """Describes a unit of work to be run through the queue.

    The ``create_worker`` callable must return a *fresh* QThread instance
    whose worker-specific signals (finished, error, progress) are already
    connected to the appropriate page-level callbacks.  The queue itself
    only relies on ``QThread.finished`` to know when the thread exits.
    """

    job_id: str                           # unique key, e.g. "trc_analytics_refresh"
    name: str                             # human-readable, shown in overlay
    description: str                      # shown below the sprout animation
    create_worker: Callable[[], QThread]  # factory → fresh QThread
    state: str = "queued"                 # queued | running | completed | failed
    error_msg: str = ""


# ═══════════════════════════════════════════
#  CALLABLE WORKER (generic wrapper)
# ═══════════════════════════════════════════

class CallableWorker(QThread):
    """Generic QThread that runs a plain callable on a background thread.

    Useful for wrapping synchronous functions (e.g. ``ingest_csv``)
    so they can participate in the sequential job queue.
    """

    finished_result = Signal(object)  # emits the callable's return value
    error = Signal(str)               # emits traceback string on failure
    progress = Signal(str, int)       # (message, percent) — percent -1 for indeterminate

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished_result.emit(result)
        except Exception:
            self.error.emit(traceback.format_exc())


# ═══════════════════════════════════════════
#  JOB QUEUE
# ═══════════════════════════════════════════

class JobQueue(QObject):
    """Central sequential job queue.

    Accepts ``JobDescriptor`` objects via :meth:`submit` or
    :meth:`submit_batch`.  Jobs execute ONE AT A TIME in submission
    order.  Signals keep the overlay and status bar informed.
    """

    # ── Signals ──────────────────────────────────────────────────────
    job_started = Signal(str, str)        # (job_id, description)
    job_progress = Signal(str, str, int)  # (job_id, message, percent)
    job_finished = Signal(str, str)       # (job_id, name)
    job_failed = Signal(str, str, str)    # (job_id, name, error_msg)
    queue_empty = Signal()                # all jobs done
    queue_changed = Signal(list)          # [{job_id, name, state}, ...]

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._jobs: list[JobDescriptor] = []
        self._history: list[JobDescriptor] = []  # completed/failed jobs (recent)
        self._current_worker: Optional[QThread] = None
        self._is_running: bool = False
        self._max_history: int = 20

    # ── Public API ───────────────────────────────────────────────────

    def submit(self, job: JobDescriptor) -> None:
        """Add a single job to the queue. Starts immediately if idle."""
        self._jobs.append(job)
        self._emit_changed()
        if not self._is_running:
            self._start_next()

    def submit_batch(self, jobs: list[JobDescriptor]) -> None:
        """Add multiple jobs. They run sequentially in list order."""
        self._jobs.extend(jobs)
        self._emit_changed()
        if not self._is_running:
            self._start_next()

    def cancel_pending(self, job_id: str) -> None:
        """Remove a queued (not yet started) job by its ID."""
        self._jobs = [j for j in self._jobs if not (j.job_id == job_id and j.state == "queued")]
        self._emit_changed()

    def cancel_all(self) -> None:
        """Cancel everything: stop current worker and clear queue."""
        # Clear pending jobs
        self._jobs.clear()

        # Stop current worker
        if self._current_worker and self._current_worker.isRunning():
            self._current_worker.quit()
            self._current_worker.wait(3000)
            self._current_worker = None

        self._is_running = False
        self._history.clear()
        self._emit_changed()

    def is_running(self) -> bool:
        return self._is_running

    def pending_count(self) -> int:
        return sum(1 for j in self._jobs if j.state == "queued")

    def get_job_list(self) -> list[dict]:
        """Return a combined list of history + current + pending for overlay."""
        result = []

        # Recent completed/failed jobs
        for j in self._history:
            result.append({
                "job_id": j.job_id,
                "name": j.name,
                "state": j.state,
            })

        # Current + pending
        for j in self._jobs:
            result.append({
                "job_id": j.job_id,
                "name": j.name,
                "state": j.state,
            })

        return result

    # ── Internal ─────────────────────────────────────────────────────

    def _start_next(self) -> None:
        """Pop the next queued job and run its worker."""
        # Find next queued job
        next_job = None
        for j in self._jobs:
            if j.state == "queued":
                next_job = j
                break

        if next_job is None:
            self._is_running = False
            self._emit_changed()
            self.queue_empty.emit()
            return

        self._is_running = True
        next_job.state = "running"

        try:
            worker = next_job.create_worker()
        except Exception as e:
            next_job.state = "failed"
            next_job.error_msg = str(e)
            self._move_to_history(next_job)
            self.job_failed.emit(next_job.job_id, next_job.name, str(e))
            self._emit_changed()
            self._start_next()
            return

        self._current_worker = worker

        # When the QThread exits (run() returns), advance the queue.
        # Worker-specific signals (finished/error) are already connected
        # to page callbacks by the create_worker factory.
        worker.finished.connect(lambda: self._on_thread_done(next_job))

        self.job_started.emit(next_job.job_id, next_job.description)
        self._emit_changed()
        worker.start()

    @Slot()
    def _on_thread_done(self, job: JobDescriptor) -> None:
        """Called when the worker QThread exits."""
        self._current_worker = None

        # If the job wasn't already marked as failed by an error callback,
        # mark it completed.
        if job.state == "running":
            job.state = "completed"

        self._move_to_history(job)

        if job.state == "failed":
            self.job_failed.emit(job.job_id, job.name, job.error_msg)
        else:
            self.job_finished.emit(job.job_id, job.name)

        self._emit_changed()
        self._start_next()

    def _move_to_history(self, job: JobDescriptor) -> None:
        """Move a finished/failed job from _jobs to _history."""
        if job in self._jobs:
            self._jobs.remove(job)
        self._history.append(job)
        # Trim history
        while len(self._history) > self._max_history:
            self._history.pop(0)

    def _emit_changed(self) -> None:
        """Emit queue_changed with current state snapshot."""
        self.queue_changed.emit(self.get_job_list())

    def mark_job_failed(self, job_id: str, error_msg: str) -> None:
        """Called by error callbacks to mark the current job as failed.

        This is invoked from the worker's error signal handler (on the main
        thread) before QThread.finished fires, so the queue knows to emit
        job_failed instead of job_finished.
        """
        for j in self._jobs:
            if j.job_id == job_id and j.state == "running":
                j.state = "failed"
                j.error_msg = error_msg
                break
