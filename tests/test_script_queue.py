"""Tests for ScriptQueue: enqueue, status_text, cancellation, and clear."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from serialk.script_queue import ScriptJob, ScriptQueue


def _make_job(name: str = "test.txt") -> ScriptJob:
    return ScriptJob(path=Path(name), delay=0.0, condition_timeout=5.0)


# ---------------------------------------------------------------------------
# ScriptJob helpers
# ---------------------------------------------------------------------------


def test_script_job_name_returns_filename() -> None:
    """name property returns only the filename, not the full path."""

    job = ScriptJob(path=Path("/some/dir/script.txt"), delay=0.0, condition_timeout=1.0)
    assert job.name == "script.txt"


# ---------------------------------------------------------------------------
# Idle state
# ---------------------------------------------------------------------------


def test_status_text_idle_when_empty() -> None:
    """status_text returns 'scripts: idle' for a fresh queue."""

    queue = ScriptQueue()
    assert queue.status_text() == "scripts: idle"


# ---------------------------------------------------------------------------
# enqueue / enqueue_nowait
# ---------------------------------------------------------------------------


def test_enqueue_nowait_adds_to_pending() -> None:
    """enqueue_nowait makes job appear in the pending list immediately."""

    queue = ScriptQueue()
    job = _make_job("a.txt")
    queue.enqueue_nowait(job)
    assert "a.txt" in queue.status_text()


def test_enqueue_async_adds_to_pending() -> None:
    """async enqueue also adds job to the visible pending list."""

    queue = ScriptQueue()
    job = _make_job("b.txt")
    asyncio.run(queue.enqueue(job))
    assert "b.txt" in queue.status_text()


# ---------------------------------------------------------------------------
# next_job / finish_job lifecycle
# ---------------------------------------------------------------------------


def test_next_job_sets_current_and_clears_cancel() -> None:
    """next_job returns the enqueued job and marks it as current."""

    queue = ScriptQueue()
    queue.cancel_event.set()  # pre-set to verify it is cleared
    queue.enqueue_nowait(_make_job("run.txt"))

    async def _run() -> None:
        job = await queue.next_job()
        assert job.name == "run.txt"
        assert queue.current_job is job
        assert not queue.cancel_event.is_set()
        queue.finish_job()
        assert queue.current_job is None

    asyncio.run(_run())


def test_status_text_shows_running_job() -> None:
    """status_text reflects a currently executing job."""

    queue = ScriptQueue()
    queue.enqueue_nowait(_make_job("active.txt"))

    async def _run() -> None:
        await queue.next_job()
        text = queue.status_text()
        assert "[running: active.txt]" in text
        queue.finish_job()

    asyncio.run(_run())


def test_status_text_shows_queued_jobs() -> None:
    """Pending jobs after the active one appear in status_text."""

    queue = ScriptQueue()
    queue.enqueue_nowait(_make_job("first.txt"))
    queue.enqueue_nowait(_make_job("second.txt"))

    async def _run() -> None:
        await queue.next_job()
        text = queue.status_text()
        assert "[running: first.txt]" in text
        assert "[queued: second.txt]" in text
        queue.finish_job()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_current_only_sets_event_but_keeps_queue() -> None:
    """cancel_current_only sets cancel_event without draining pending jobs."""

    queue = ScriptQueue()
    queue.enqueue_nowait(_make_job("a.txt"))
    queue.enqueue_nowait(_make_job("b.txt"))

    async def _run() -> None:
        await queue.next_job()
        queue.cancel_current_only()
        assert queue.cancel_event.is_set()
        # second job is still pending
        assert "b.txt" in queue.status_text()
        queue.finish_job()

    asyncio.run(_run())


def test_clear_queue_drains_pending_and_sets_event() -> None:
    """clear_queue empties pending jobs and sets cancel_event."""

    queue = ScriptQueue()
    queue.enqueue_nowait(_make_job("a.txt"))
    queue.enqueue_nowait(_make_job("b.txt"))

    async def _run() -> None:
        await queue.next_job()
        await queue.clear_queue()
        assert queue.cancel_event.is_set()
        assert queue.status_text() == "scripts: [cancelling: a.txt]"
        queue.finish_job()
        assert queue.status_text() == "scripts: idle"

    asyncio.run(_run())


def test_status_text_shows_cancelling_label() -> None:
    """While cancel_event is set, the running label changes to 'cancelling'."""

    queue = ScriptQueue()
    queue.enqueue_nowait(_make_job("active.txt"))

    async def _run() -> None:
        await queue.next_job()
        queue.cancel_current_only()
        assert "[cancelling: active.txt]" in queue.status_text()
        queue.finish_job()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# run_script cancel_event integration
# ---------------------------------------------------------------------------


def test_run_script_raises_on_cancel_event(tmp_path: Path) -> None:
    """run_script raises ScriptCancelledError when cancel_event is pre-set."""

    from serialk.script_runner import ScriptCancelledError, run_script

    script_path = tmp_path / "script.txt"
    script_path.write_text("ping\nstatus\n", encoding="utf-8")

    class _DummySession:
        commands: list[str] = []

        def send_command(self, cmd: str) -> None:
            self.commands.append(cmd)

    event = threading.Event()
    event.set()

    with pytest.raises(ScriptCancelledError):
        run_script(_DummySession(), script_path, cancel_event=event)  # type: ignore[arg-type]


def test_run_script_stops_mid_run_on_cancel(tmp_path: Path) -> None:
    """cancel_event set after first command aborts remaining commands."""

    from serialk.script_runner import ScriptCancelledError, run_script

    script_path = tmp_path / "script.txt"
    script_path.write_text("ping\nstatus\nstop\n", encoding="utf-8")

    event = threading.Event()

    class _DummySession:
        commands: list[str] = []

        def send_command(self, cmd: str) -> None:
            self.commands.append(cmd)
            # cancel after the first command
            if len(self.commands) == 1:
                event.set()

    session = _DummySession()
    with pytest.raises(ScriptCancelledError):
        run_script(session, script_path, cancel_event=event)  # type: ignore[arg-type]

    assert session.commands == ["ping"]
