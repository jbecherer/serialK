"""Interactive prompt_toolkit console for serialk."""

from __future__ import annotations

import asyncio
from pathlib import Path
import shlex

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout

from serialk.script_runner import ScriptSyntaxError, run_script
from serialk.serial_session import SerialSession, SerialSessionError


class InteractiveConsole:
    """Interactive console that shares one serial session with script execution."""

    def __init__(
        self,
        session: SerialSession,
        *,
        history_path: Path,
        default_script_delay: float = 0.0,
        default_condition_timeout: float = 5.0,
    ) -> None:
        """Create a new terminal console.

        Parameters
        ----------
        session:
            Active serial session.
        history_path:
            Prompt history file path.
        default_script_delay:
            Default delay in seconds used by the ``/run`` command.
        default_condition_timeout:
            Default conditional wait timeout in seconds used by ``/run``.
        """

        history_path.parent.mkdir(parents=True, exist_ok=True)
        self._session = session
        self._default_script_delay = default_script_delay
        self._default_condition_timeout = default_condition_timeout
        self._prompt = PromptSession(
            history=FileHistory(str(history_path)),
        )
        self._running = True

    async def run(self) -> None:
        """Start the interactive prompt loop."""

        self.display_message(
            "Connected. Type device commands directly or use /help for console commands."
        )
        with patch_stdout(raw=True):
            while self._running:
                try:
                    user_input = await self._prompt.prompt_async("serialk> ")
                except KeyboardInterrupt:
                    self.display_message("Press Ctrl-D or use /quit to exit.")
                    continue
                except EOFError:
                    break

                stripped = user_input.strip()
                if not stripped:
                    continue

                if stripped.startswith("/"):
                    await self._handle_slash_command(stripped)
                    continue

                try:
                    self._session.send_command(stripped)
                except SerialSessionError as exc:
                    self.display_message(str(exc))

        self.display_message("Closing console.")

    def display_device_line(self, line: str) -> None:
        """Display one received device line without timestamps."""

        print(f"< {line}", flush=True)

    def display_message(self, message: str) -> None:
        """Display one console-side informational message."""

        print(f"! {message}", flush=True)

    async def _handle_slash_command(self, raw_command: str) -> None:
        """Dispatch one slash command from the prompt."""

        try:
            parts = shlex.split(raw_command)
        except ValueError as exc:
            self.display_message(f"Invalid command syntax: {exc}")
            return

        command = parts[0][1:].lower()
        if command == "help":
            self._show_help()
            return
        if command == "status":
            self._show_status()
            return
        if command == "reconnect":
            try:
                self._session.reconnect()
            except SerialSessionError as exc:
                self.display_message(str(exc))
                return
            self.display_message("Reconnected.")
            return
        if command in {"quit", "exit"}:
            self._running = False
            return
        if command == "run":
            await self._run_script_command(parts)
            return

        self.display_message(f"Unknown slash command: {raw_command}")

    def _show_help(self) -> None:
        """Display supported slash commands."""

        self.display_message(
            "Slash commands: /help, /status, /run <script> [delay_seconds] "
            "[condition_timeout_seconds], /reconnect, /quit"
        )

    def _show_status(self) -> None:
        """Display current session status."""

        status = self._session.status()
        self.display_message(
            " ".join(
                [
                    f"profile={status.profile_name}",
                    f"port={status.port}",
                    f"connected={status.connected}",
                    f"tx={status.commands_sent}",
                    f"rx={status.messages_received}",
                    f"log={status.log_path}",
                ]
                + ([f"last_error={status.last_error}"] if status.last_error else [])
            )
        )

    async def _run_script_command(self, parts: list[str]) -> None:
        """Execute one script from the prompt loop."""

        if len(parts) < 2:
            self.display_message(
                "Usage: /run <script_path> [delay_seconds] [condition_timeout_seconds]"
            )
            return

        script_path = Path(parts[1]).expanduser()
        delay = self._default_script_delay
        condition_timeout = self._default_condition_timeout
        if len(parts) >= 3:
            try:
                delay = float(parts[2])
            except ValueError:
                self.display_message(
                    f"Invalid delay '{parts[2]}'; expected a floating-point value."
                )
                return
        if len(parts) >= 4:
            try:
                condition_timeout = float(parts[3])
            except ValueError:
                self.display_message(
                    f"Invalid conditional timeout '{parts[3]}'; expected a floating-point value."
                )
                return

        try:
            commands = await asyncio.to_thread(
                run_script,
                self._session,
                script_path,
                inter_command_delay=delay,
                condition_timeout=condition_timeout,
            )
        except (OSError, SerialSessionError, ValueError, ScriptSyntaxError) as exc:
            self.display_message(str(exc))
            return

        self.display_message(
            f"Sent {len(commands)} commands from {script_path} with delay={delay:.3f}s "
            f"and condition_timeout={condition_timeout:.3f}s."
        )
