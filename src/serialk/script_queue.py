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

    Thread-safe operations (``prepend_job_from_thread``,
    ``cancel_by_name_from_thread``) are available for use from worker threads
    (i.e. inside ``run_script`` via directives).  Call :meth:`set_loop` once
    from the async context before any thread-safe calls are made.
    """

    def __init__(self) -> None:
        """Create an empty, idle queue."""

        self._pending: list[ScriptJob] = []
        self._condition: asyncio.Condition = asyncio.Condition()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.current_job: ScriptJob | None = None
        self.cancel_event: threading.Event = threading.Event()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running event loop for thread-safe queue operations.

        Parameters
        ----------
        loop:
            The currently running asyncio event loop.  Must be called from
            async context before any ``*_from_thread`` methods are used.
        """

        self._loop = loop

    # ------------------------------------------------------------------
    # Async enqueueing
    # ------------------------------------------------------------------

    async def enqueue(self, job: ScriptJob) -> None:
        """Add one job to the back of the queue.

        Parameters
        ----------
        job:
            Script job to enqueue.
        """

        async with self._condition:
            self._pending.append(job)
            self._condition.notify()

    def enqueue_nowait(self, job: ScriptJob) -> None:
        """Add one job to the queue without awaiting (safe before event loop starts).

        Parameters
        ----------
        job:
            Script job to enqueue.
        """

        self._pending.append(job)

    async def prepend_job(self, job: ScriptJob) -> None:
        """Insert one job at the front of the pending queue (runs next).

        Parameters
        ----------
        job:
            Script job to insert ahead of all other pending jobs.
        """

        async with self._condition:
            self._pending.insert(0, job)
            self._condition.notify()

    def prepend_job_from_thread(self, job: ScriptJob) -> None:
        """Thread-safe wrapper for :meth:`prepend_job`.

        Blocks the calling thread until the operation completes on the event
        loop.  Requires :meth:`set_loop` to have been called first.

        Parameters
        ----------
        job:
            Script job to insert at the front of the queue.
        """

        if self._loop is None:
            raise RuntimeError("set_loop() must be called before prepend_job_from_thread().")
        asyncio.run_coroutine_threadsafe(self.prepend_job(job), self._loop).result(timeout=5.0)

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    async def next_job(self) -> ScriptJob:
        """Wait for and return the next job, marking it as current.

        Returns
        -------
        ScriptJob
            The next job to run.
        """

        async with self._condition:
            await self._condition.wait_for(lambda: bool(self._pending))
            job = self._pending.pop(0)
        self.current_job = job
        self.cancel_event.clear()
        return job

    def finish_job(self) -> None:
        """Mark the current job as finished and reset the cancel event."""

        self.current_job = None
        self.cancel_event.clear()

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_current_only(self) -> None:
        """Cancel the active job; leave pending jobs in the queue."""

        self.cancel_event.set()

    async def clear_queue(self) -> None:
        """Cancel the active job and drain all pending jobs from the queue."""

        self.cancel_event.set()
        async with self._condition:
            self._pending.clear()

    async def cancel_by_name(self, name: str) -> int:
        """Cancel all pending jobs whose filename matches ``name``.

        If the currently running job also matches, it is cancelled via
        :attr:`cancel_event`.

        Parameters
        ----------
        name:
            Filename (basename only) to match against pending jobs.

        Returns
        -------
        int
            Total number of jobs cancelled (pending + active if matched).
        """

        cancelled = 0
        async with self._condition:
            before = len(self._pending)
            self._pending = [j for j in self._pending if j.name != name]
            cancelled += before - len(self._pending)

        if self.current_job is not None and self.current_job.name == name:
            self.cancel_event.set()
            cancelled += 1

        return cancelled

    def cancel_by_name_from_thread(self, name: str) -> int:
        """Thread-safe wrapper for :meth:`cancel_by_name`.

        Blocks the calling thread until the operation completes on the event
        loop.  Requires :meth:`set_loop` to have been called first.

        Parameters
        ----------
        name:
            Filename to match.

        Returns
        -------
        int
            Number of jobs cancelled.
        """

        if self._loop is None:
            raise RuntimeError("set_loop() must be called before cancel_by_name_from_thread().")
        return asyncio.run_coroutine_threadsafe(
            self.cancel_by_name(name), self._loop
        ).result(timeout=5.0)

    def clear_queue_from_thread(self) -> None:
        """Thread-safe wrapper for :meth:`clear_queue`.

        Blocks the calling thread until the queue is cleared.  Requires
        :meth:`set_loop` to have been called first.
        """

        if self._loop is None:
            raise RuntimeError("set_loop() must be called before clear_queue_from_thread().")
        asyncio.run_coroutine_threadsafe(self.clear_queue(), self._loop).result(timeout=5.0)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

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

