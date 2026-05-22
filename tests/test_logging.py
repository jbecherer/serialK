"""Tests for session log file creation and content."""

from pathlib import Path

from serialk.logging import SessionLogger


def test_session_logger_writes_entries(tmp_path: Path) -> None:
    """Write event, TX, and RX entries to one session log file."""

    logger = SessionLogger("instrument", tmp_path)
    logger.log_event("CONNECTED")
    logger.log_tx("status")
    logger.log_rx("STATUS ok")
    logger.close()

    content = logger.path.read_text(encoding="utf-8")

    assert logger.path.parent == tmp_path
    assert "EVENT" in content
    assert "TX" in content
    assert "RX" in content
