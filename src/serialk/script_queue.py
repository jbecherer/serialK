"""Script job queue with cooperative cancellation for serialk."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import threading


@dataclass(frozen=True, slots=True)
class ScriptJob:
    """One queued script execution request.

    Parameters
    ----------
    path:
        Script file to execute.
    delay:
        Inter-command delay in seconds.
    condition_timeout:
        Default conditional wait timeout in seconds.
    """

    path: Path
    delay: float
    condition_timeout: float

    @property
    def name(self) -> str:
        """Short display name (filename only)."""

        return self.path.name


class ScriptQueue:
    """Async queue of script jobs with cooperative single-job cancellation.

    One job runs at a time.  The console's ``_queue_worker`` task drives the
    queue by calling :meth:`next_job` to block-wait for work, then sets
    :attr:`current_job` while the job is running, and finally calls
    :meth:`finish_job` when done.

    Cancellation is cooperative: the running ``run_script`` call checks
    :attr:`cancel_event` between every command and every conditional wait.
    """

    def __init__(self) -> None:
        """Create an empty, idle queue."""

        self._queue: asyncio.Queue[ScriptJob] = asyncio.Queue()
        self._pending: list[ScriptJob] = []
        self._lock = asyncio.Lock()
        self.current_job: ScriptJob | None = None
        self.cancel_event: threading.Event = threading.Event()

    async def enqueue(self, job: ScriptJob) -> None:
        """Add one job to the back of the queue.

        Parameters
        ----------
        job:
            Script job to enqueue.
        """

        async with self._lock:
            self._pending.append(job)
        await self._queue.put(job)

    def enqueue_nowait(self, job: ScriptJob) -> None:
        """Add one job to the queue without awaiting (safe before event loop starts).

        Parameters
        ----------
        job:
            Script job to enqueue.
        """

        self._pending.append(job)
        self._queue.put_nowait(job)

    async def next_job(self) -> ScriptJob:
        """Wait for and return the next job, marking it as current.

        Returns
        -------
        ScriptJob
            The next job to run.
        """

        job = await self._queue.get()
        async with self._lock:
            if job in self._pending:
                self._pending.remove(job)
        self.current_job = job
        self.cancel_event.clear()
        return job

    def finish_job(self) -> None:
        """Mark the current job as finished and reset the cancel event."""

        self.current_job = None
        self.cancel_event.clear()
        self._queue.task_done()

    def cancel_current_only(self) -> None:
        """Cancel the active job; leave pending jobs in the queue."""

        self.cancel_event.set()

    async def clear_queue(self) -> None:
        """Cancel the active job and drain all pending jobs from the queue."""

        self.cancel_event.set()
        async with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                    self._queue.task_done()
                except asyncio.QueueEmpty:
                    break
            self._pending.clear()

    def status_text(self) -> str:
        """Return a short queue-state string suitable for a terminal toolbar.

        Returns
        -------
        str
            Human-readable queue status, e.g.
            ``'scripts: [running: foo.txt] [queued: bar.txt, baz.txt]'``.
        """

        parts: list[str] = []
        if self.current_job is not None:
            label = "cancelling" if self.cancel_event.is_set() else "running"
            parts.append(f"[{label}: {self.current_job.name}]")

        if self._pending:
            names = ", ".join(job.name for job in self._pending)
            parts.append(f"[queued: {names}]")

        if not parts:
            return "scripts: idle"
        return "scripts: " + " ".join(parts)
