"""Tests for plain-text and conditional command script execution."""

from pathlib import Path

import pytest

from serialk.script_runner import (
    CommandNode,
    ConditionalNode,
    ScriptSyntaxError,
    load_script_commands,
    parse_script,
    run_script,
)


class DummySession:
    """Minimal session double collecting sent commands."""

    def __init__(self) -> None:
        """Initialize an empty command list."""

        self.commands: list[str] = []
        self.wait_calls: list[tuple[str, float]] = []
        self.matches: dict[str, str | None] = {}

    def send_command(self, command: str) -> None:
        """Record one sent command."""

        self.commands.append(command)

    def wait_for_substring(self, substring: str, timeout: float) -> str | None:
        """Return a pre-programmed match result for one conditional."""

        self.wait_calls.append((substring, timeout))
        return self.matches.get(substring)


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


def test_parse_script_supports_nested_conditionals(tmp_path: Path) -> None:
    """Build a nested parse tree for ``if`` / ``else`` / ``endif`` blocks."""

    script_path = tmp_path / "conditional.txt"
    script_path.write_text(
        """
if "READY" timeout=2.5
    start
    if "MEAS"
        ping
    else
        status
    endif
else
    stop
endif
""".strip(),
        encoding="utf-8",
    )

    nodes = parse_script(script_path)

    assert len(nodes) == 1
    outer = nodes[0]
    assert isinstance(outer, ConditionalNode)
    assert outer.substring == "READY"
    assert outer.timeout == 2.5
    assert outer.else_branch == [CommandNode(command="stop", line_number=9)]
    assert isinstance(outer.then_branch[0], CommandNode)
    assert isinstance(outer.then_branch[1], ConditionalNode)


def test_run_script_executes_else_branch_after_timeout(tmp_path: Path) -> None:
    """Execute the ``else`` branch when no incoming match arrives in time."""

    script_path = tmp_path / "conditional.txt"
    script_path.write_text(
        """
if "READY"
    start
else
    status
endif
""".strip(),
        encoding="utf-8",
    )
    session = DummySession()
    session.matches["READY"] = None

    sent_commands = run_script(
        session,  # type: ignore[arg-type]
        script_path,
        inter_command_delay=0.0,
        condition_timeout=1.25,
    )

    assert sent_commands == ["status"]
    assert session.commands == ["status"]
    assert session.wait_calls == [("READY", 1.25)]


def test_run_script_uses_per_block_timeout_override(tmp_path: Path) -> None:
    """Prefer one conditional block's timeout over the global default."""

    script_path = tmp_path / "conditional.txt"
    script_path.write_text(
        """
if "READY" timeout=0.5
    start
endif
""".strip(),
        encoding="utf-8",
    )
    session = DummySession()
    session.matches["READY"] = "READY"

    sent_commands = run_script(
        session,  # type: ignore[arg-type]
        script_path,
        inter_command_delay=0.0,
        condition_timeout=10.0,
    )

    assert sent_commands == ["start"]
    assert session.wait_calls == [("READY", 0.5)]


def test_parse_script_rejects_missing_endif(tmp_path: Path) -> None:
    """Raise a syntax error for incomplete conditional blocks."""

    script_path = tmp_path / "broken.txt"
    script_path.write_text('if "READY"\nstart\n', encoding="utf-8")

    with pytest.raises(ScriptSyntaxError, match="Missing 'endif'"):
        parse_script(script_path)
