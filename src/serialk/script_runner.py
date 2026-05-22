"""Plain-text script execution for serialk."""

from __future__ import annotations

from pathlib import Path
import time

from serialk.serial_session import SerialSession


def load_script_commands(script_path: Path) -> list[str]:
    """Load a script file and return executable command lines.

    Parameters
    ----------
    script_path:
        Plain-text script file with one command per line.

    Returns
    -------
    list[str]
        Commands after blank lines and ``#`` comments are removed.

    Raises
    ------
    FileNotFoundError
        If the script file does not exist.
    """

    commands: list[str] = []
    with script_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            commands.append(stripped)
    return commands


def run_script(
    session: SerialSession,
    script_path: Path,
    *,
    inter_command_delay: float = 0.0,
) -> list[str]:
    """Send all commands from one script through the shared session pipeline.

    Parameters
    ----------
    session:
        Active serial session used for sending commands.
    script_path:
        Script file path.
    inter_command_delay:
        Optional fixed delay in seconds between commands.

    Returns
    -------
    list[str]
        Commands that were sent.
    """

    commands = load_script_commands(script_path)
    for index, command in enumerate(commands):
        session.send_command(command)
        if inter_command_delay > 0 and index < len(commands) - 1:
            time.sleep(inter_command_delay)
    return commands
