"""Tests for simulator-backed session behavior."""

from pathlib import Path
import time

from serialk.config import InstrumentProfile
from serialk.logging import SessionLogger
from serialk.serial_session import SerialSession


def test_simulator_session_receives_measurements_and_replies(tmp_path: Path) -> None:
    """Connect to the simulator, receive streaming lines, and send commands."""

    received_lines: list[str] = []
    profile = InstrumentProfile(
        name="simulator",
        port="sim://default",
        baudrate=115200,
        timeout=0.05,
        log_dir=tmp_path,
    )
    logger = SessionLogger(profile.name, tmp_path)
    session = SerialSession(profile, logger, display_callback=received_lines.append)

    session.connect()
    session.send_command("ping")
    session.send_command("status")

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if any("PONG" in line for line in received_lines) and any(
            line.startswith("MEAS ") for line in received_lines
        ):
            break
        time.sleep(0.05)

    status = session.status()
    session.close()

    assert status.connected is True
    assert status.commands_sent == 2
    assert any("PONG" in line for line in received_lines)
    assert any(line.startswith("MEAS ") for line in received_lines)
    assert logger.path.exists()
