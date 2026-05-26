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
    matched_line = session.wait_for_substring("MEAS index=", 1.0)
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

    assert matched_line is not None
    assert matched_line.startswith("MEAS ")
    assert status.connected is True
    assert status.commands_sent == 2
    assert any("PONG" in line for line in received_lines)
    assert any(line.startswith("MEAS ") for line in received_lines)
    assert logger.path.exists()


def test_simulator_conditional_script_logs_wait_and_timeout(tmp_path: Path) -> None:
    """Run a conditional script and verify wait-related events are logged."""

    from serialk.script_runner import run_script

    profile = InstrumentProfile(
        name="simulator",
        port="sim://default",
        baudrate=115200,
        timeout=0.05,
        log_dir=tmp_path,
    )
    logger = SessionLogger(profile.name, tmp_path)
    session = SerialSession(profile, logger)
    script_path = tmp_path / "conditional.txt"
    script_path.write_text(
        """
if "MEAS index=" timeout=1.0
    ping
    if "DOES NOT EXIST" timeout=0.2
        start
    else
        status
    endif
else
    stop
endif
""".strip(),
        encoding="utf-8",
    )

    session.connect()
    sent_commands = run_script(
        session,
        script_path,
        inter_command_delay=0.0,
        condition_timeout=5.0,
    )
    session.close()

    log_content = logger.path.read_text(encoding="utf-8")

    assert sent_commands == ["ping", "status"]
    assert "WAIT substring='MEAS index='" in log_content
    assert "MATCH substring='MEAS index='" in log_content
    assert "TIMEOUT substring='DOES NOT EXIST'" in log_content
