"""Tests for plain-text command script execution."""

from pathlib import Path

from serialk.script_runner import load_script_commands, run_script


class DummySession:
    """Minimal session double collecting sent commands."""

    def __init__(self) -> None:
        """Initialize an empty command list."""

        self.commands: list[str] = []

    def send_command(self, command: str) -> None:
        """Record one sent command."""

        self.commands.append(command)


def test_load_script_commands_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    """Return only executable command lines from a plain-text script."""

    script_path = tmp_path / "commands.txt"
    script_path.write_text(
        """
# comment
ping

status
""".strip(),
        encoding="utf-8",
    )

    assert load_script_commands(script_path) == ["ping", "status"]


def test_run_script_uses_shared_send_pipeline(tmp_path: Path) -> None:
    """Send all script commands through the provided session object."""

    script_path = tmp_path / "commands.txt"
    script_path.write_text("ping\nstatus\n", encoding="utf-8")
    session = DummySession()

    sent_commands = run_script(session, script_path, inter_command_delay=0.0)  # type: ignore[arg-type]

    assert sent_commands == ["ping", "status"]
    assert session.commands == ["ping", "status"]
