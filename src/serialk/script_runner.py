"""Plain-text and conditional script execution for serialk."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
import threading
import time
from typing import Callable

from serialk.serial_session import SerialSession


class ScriptSyntaxError(ValueError):
    """Raised when a script file contains invalid conditional syntax."""


class ScriptCancelledError(Exception):
    """Raised when a script is cancelled via a cancel event."""


@dataclass(frozen=True, slots=True)
class CommandNode:
    """One plain device command in a parsed script."""

    command: str
    line_number: int


@dataclass(frozen=True, slots=True)
class ConditionalNode:
    """One conditional block in a parsed script."""

    substring: str
    line_number: int
    timeout: float | None
    then_branch: list["ScriptNode"]
    else_branch: list["ScriptNode"]


@dataclass(frozen=True, slots=True)
class DirectiveNode:
    """One queue-control directive in a parsed script.

    Directives begin with ``/`` and affect the script queue rather than sending
    a command to the device.

    Attributes
    ----------
    kind:
        ``"queue"`` or ``"cancel"``.
    argument:
        For ``"queue"``: the script path string.
        For ``"cancel"``: ``None`` (clear all), ``"current"`` (active only),
        or a filename string (cancel by name).
    delay:
        Explicit delay override for a ``"queue"`` directive, or ``None`` to
        inherit from the current job.
    condition_timeout:
        Explicit condition_timeout override for a ``"queue"`` directive, or
        ``None`` to inherit.
    line_number:
        Source line number for error reporting.
    """

    kind: str
    argument: str | None
    delay: float | None
    condition_timeout: float | None
    line_number: int


ScriptNode = CommandNode | ConditionalNode | DirectiveNode


@dataclass(frozen=True, slots=True)
class QueueControl:
    """Callable hooks that connect ``run_script`` to the live ``ScriptQueue``.

    All callables must be thread-safe: they are invoked from the worker thread
    that runs ``run_script``, not from the async event loop.

    Attributes
    ----------
    prepend_job:
        Insert a new job as the next job to run (ahead of the pending queue).
        Signature: ``(path, delay, condition_timeout) -> None``.
    cancel_all:
        Cancel the active script and drain the entire queue.
    cancel_current:
        Cancel only the active script; pending jobs remain.
    cancel_by_name:
        Cancel all pending (and active if matching) jobs by filename.
        Signature: ``(name: str) -> int`` — returns count cancelled.
    """

    prepend_job: Callable[[Path, float, float], None]
    cancel_all: Callable[[], None]
    cancel_current: Callable[[], None]
    cancel_by_name: Callable[[str], int]


@dataclass(slots=True)
class _ExecutionState:
    """Mutable execution state shared across recursive script evaluation."""

    sent_commands: list[str]
    inter_command_delay: float
    default_condition_timeout: float
    cancel_event: threading.Event | None
    queue_control: QueueControl | None
    script_dir: Path
    has_sent_command: bool = False


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
            if _is_control_line(stripped):
                raise ScriptSyntaxError(
                    "Conditional scripts cannot be flattened into a plain command list. "
                    "Use 'parse_script()' or 'run_script()' instead."
                )
            commands.append(stripped)
    return commands


def parse_script(script_path: Path) -> list[ScriptNode]:
    """Parse a script file into command, conditional, and directive nodes.

    Parameters
    ----------
    script_path:
        Script file path.

    Returns
    -------
    list[ScriptNode]
        Parsed script tree.

    Raises
    ------
    FileNotFoundError
        If the script file does not exist.
    ScriptSyntaxError
        If the script contains malformed control-flow or directive syntax.
    """

    entries: list[tuple[int, str]] = []
    with script_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            entries.append((line_number, stripped))

    parser = _ScriptParser(entries)
    return parser.parse()


def run_script(
    session: SerialSession,
    script_path: Path,
    *,
    inter_command_delay: float = 0.0,
    condition_timeout: float = 5.0,
    cancel_event: threading.Event | None = None,
    queue_control: QueueControl | None = None,
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
    condition_timeout:
        Default timeout in seconds used by conditional waits that do not
        define their own ``timeout=...`` value.
    cancel_event:
        Optional threading event checked before each command and each
        conditional wait.  When set, execution stops and
        :class:`ScriptCancelledError` is raised.
    queue_control:
        Optional queue control hooks.  Required for scripts that use
        ``/queue`` or ``/cancel`` directives.  If ``None`` and a directive is
        encountered, a ``ValueError`` is raised at runtime.

    Returns
    -------
    list[str]
        Commands that were sent.

    Raises
    ------
    ScriptCancelledError
        If ``cancel_event`` is set before or during execution.
    ValueError
        If a directive is encountered but ``queue_control`` is ``None``.
    """

    if inter_command_delay < 0:
        raise ValueError("inter_command_delay must be non-negative.")
    if condition_timeout < 0:
        raise ValueError("condition_timeout must be non-negative.")

    nodes = parse_script(script_path)
    state = _ExecutionState(
        sent_commands=[],
        inter_command_delay=inter_command_delay,
        default_condition_timeout=condition_timeout,
        cancel_event=cancel_event,
        queue_control=queue_control,
        script_dir=script_path.parent,
    )
    _execute_nodes(session, nodes, state)
    return state.sent_commands


class _ScriptParser:
    """Recursive-descent parser for the conditional script grammar."""

    def __init__(self, entries: list[tuple[int, str]]) -> None:
        """Store the filtered script lines for later parsing."""

        self._entries = entries
        self._index = 0

    def parse(self) -> list[ScriptNode]:
        """Parse the entire script file."""

        nodes, stop_token = self._parse_nodes(stop_tokens=set())
        if stop_token is not None:
            line_number, _line = self._entries[self._index]
            raise ScriptSyntaxError(
                f"Unexpected '{stop_token}' at line {line_number}."
            )
        return nodes

    def _parse_nodes(self, stop_tokens: set[str]) -> tuple[list[ScriptNode], str | None]:
        """Parse nodes until one of the given stop tokens is reached."""

        nodes: list[ScriptNode] = []
        while self._index < len(self._entries):
            line_number, stripped = self._entries[self._index]
            lowered = stripped.lower()
            if lowered in stop_tokens:
                return nodes, lowered
            if lowered == "else":
                raise ScriptSyntaxError(f"Unexpected 'else' at line {line_number}.")
            if lowered == "endif":
                raise ScriptSyntaxError(f"Unexpected 'endif' at line {line_number}.")

            if lowered.startswith("if "):
                nodes.append(self._parse_conditional(line_number, stripped))
                continue

            if stripped.startswith("/"):
                nodes.append(_parse_directive(stripped, line_number))
                self._index += 1
                continue

            nodes.append(CommandNode(command=stripped, line_number=line_number))
            self._index += 1

        return nodes, None

    def _parse_conditional(self, line_number: int, stripped: str) -> ConditionalNode:
        """Parse one ``if`` block including nested branches."""

        substring, timeout = _parse_if_header(stripped, line_number)
        self._index += 1
        then_branch, stop_token = self._parse_nodes(stop_tokens={"else", "endif"})
        if stop_token is None:
            raise ScriptSyntaxError(
                f"Missing 'endif' for conditional starting at line {line_number}."
            )

        else_branch: list[ScriptNode] = []
        if stop_token == "else":
            self._index += 1
            else_branch, stop_token = self._parse_nodes(stop_tokens={"endif"})
            if stop_token != "endif":
                raise ScriptSyntaxError(
                    f"Missing 'endif' for conditional starting at line {line_number}."
                )

        self._index += 1
        return ConditionalNode(
            substring=substring,
            line_number=line_number,
            timeout=timeout,
            then_branch=then_branch,
            else_branch=else_branch,
        )


def _parse_directive(stripped: str, line_number: int) -> DirectiveNode:
    """Parse one ``/queue`` or ``/cancel`` directive line.

    Parameters
    ----------
    stripped:
        Non-empty script line starting with ``/``.
    line_number:
        Source line number for error reporting.

    Returns
    -------
    DirectiveNode
        Parsed directive.

    Raises
    ------
    ScriptSyntaxError
        If the directive is malformed or unrecognised.
    """

    try:
        tokens = shlex.split(stripped, posix=True)
    except ValueError as exc:
        raise ScriptSyntaxError(
            f"Invalid directive syntax at line {line_number}: {exc}"
        ) from exc

    command = tokens[0].lower()

    if command == "/queue":
        if len(tokens) < 2:
            raise ScriptSyntaxError(
                f"/queue at line {line_number} requires a script path argument."
            )
        path_arg = tokens[1]
        delay: float | None = None
        cond_timeout: float | None = None
        if len(tokens) >= 3:
            try:
                delay = float(tokens[2])
            except ValueError as exc:
                raise ScriptSyntaxError(
                    f"Invalid delay '{tokens[2]}' in /queue at line {line_number}."
                ) from exc
            if delay < 0:
                raise ScriptSyntaxError(
                    f"/queue delay must be non-negative at line {line_number}."
                )
        if len(tokens) >= 4:
            try:
                cond_timeout = float(tokens[3])
            except ValueError as exc:
                raise ScriptSyntaxError(
                    f"Invalid condition_timeout '{tokens[3]}' in /queue at line {line_number}."
                ) from exc
            if cond_timeout < 0:
                raise ScriptSyntaxError(
                    f"/queue condition_timeout must be non-negative at line {line_number}."
                )
        if len(tokens) > 4:
            raise ScriptSyntaxError(
                f"/queue at line {line_number} has unexpected extra arguments."
            )
        return DirectiveNode(
            kind="queue",
            argument=path_arg,
            delay=delay,
            condition_timeout=cond_timeout,
            line_number=line_number,
        )

    if command == "/cancel":
        argument: str | None = None
        if len(tokens) == 1:
            argument = None  # clear all
        elif len(tokens) == 2:
            argument = tokens[1]  # "current" or a filename
        else:
            raise ScriptSyntaxError(
                f"/cancel at line {line_number} takes at most one argument."
            )
        return DirectiveNode(
            kind="cancel",
            argument=argument,
            delay=None,
            condition_timeout=None,
            line_number=line_number,
        )

    raise ScriptSyntaxError(
        f"Unknown directive '{tokens[0]}' at line {line_number}. "
        "Supported directives: /queue, /cancel."
    )


def _parse_if_header(stripped: str, line_number: int) -> tuple[str, float | None]:
    """Parse one conditional header line.

    Parameters
    ----------
    stripped:
        Non-empty script line beginning with ``if ``.
    line_number:
        Source line number for error reporting.

    Returns
    -------
    tuple[str, float | None]
        Match substring and optional timeout override.
    """

    try:
        tokens = shlex.split(stripped, comments=False, posix=True)
    except ValueError as exc:
        raise ScriptSyntaxError(
            f"Invalid conditional syntax at line {line_number}: {exc}"
        ) from exc

    if len(tokens) < 2:
        raise ScriptSyntaxError(
            f"Conditional at line {line_number} must define a match string."
        )

    substring = tokens[1]
    if not substring:
        raise ScriptSyntaxError(
            f"Conditional at line {line_number} must use a non-empty match string."
        )

    timeout: float | None = None
    for token in tokens[2:]:
        if not token.startswith("timeout="):
            raise ScriptSyntaxError(
                f"Unsupported conditional option '{token}' at line {line_number}."
            )
        if timeout is not None:
            raise ScriptSyntaxError(
                f"Conditional at line {line_number} defines timeout more than once."
            )
        timeout_text = token.split("=", maxsplit=1)[1]
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ScriptSyntaxError(
                f"Invalid timeout value '{timeout_text}' at line {line_number}."
            ) from exc
        if timeout < 0:
            raise ScriptSyntaxError(
                f"Conditional timeout must be non-negative at line {line_number}."
            )

    return substring, timeout


def _execute_nodes(
    session: SerialSession,
    nodes: list[ScriptNode],
    state: _ExecutionState,
) -> None:
    """Execute parsed script nodes recursively."""

    for node in nodes:
        if state.cancel_event is not None and state.cancel_event.is_set():
            raise ScriptCancelledError("Script cancelled.")

        if isinstance(node, CommandNode):
            _execute_command(session, node.command, state)
            continue

        if isinstance(node, DirectiveNode):
            _execute_directive(node, state)
            # /cancel directives that cancel the current script raise here
            if state.cancel_event is not None and state.cancel_event.is_set():
                raise ScriptCancelledError("Script cancelled by directive.")
            continue

        # ConditionalNode
        timeout = (
            state.default_condition_timeout if node.timeout is None else node.timeout
        )
        matched_line = session.wait_for_substring(node.substring, timeout)

        if state.cancel_event is not None and state.cancel_event.is_set():
            raise ScriptCancelledError("Script cancelled.")

        branch = node.then_branch if matched_line is not None else node.else_branch
        _execute_nodes(session, branch, state)


def _execute_directive(node: DirectiveNode, state: _ExecutionState) -> None:
    """Execute one queue-control directive.

    Parameters
    ----------
    node:
        Parsed directive node.
    state:
        Current execution state carrying the queue control hooks.

    Raises
    ------
    ValueError
        If a directive is encountered but ``state.queue_control`` is ``None``.
    """

    if state.queue_control is None:
        raise ValueError(
            f"Script directive '{node.kind}' at line {node.line_number} requires "
            "a queue context. Pass queue_control= to run_script()."
        )

    qc = state.queue_control

    if node.kind == "queue":
        assert node.argument is not None
        raw_path = Path(node.argument)
        resolved = raw_path if raw_path.is_absolute() else state.script_dir / raw_path
        delay = node.delay if node.delay is not None else state.inter_command_delay
        cond_to = (
            node.condition_timeout
            if node.condition_timeout is not None
            else state.default_condition_timeout
        )
        qc.prepend_job(resolved, delay, cond_to)
        return

    # kind == "cancel"
    if node.argument is None:
        # /cancel — clear everything including current script
        qc.cancel_all()
    elif node.argument.lower() == "current":
        # /cancel current — stop only this script
        qc.cancel_current()
    else:
        # /cancel <name> — remove by filename
        qc.cancel_by_name(node.argument)

    raise ScriptCancelledError("Script cancelled by /cancel directive.")

def _execute_command(
    session: SerialSession,
    command: str,
    state: _ExecutionState,
) -> None:
    """Send one command while preserving the global inter-command delay."""

    if state.cancel_event is not None and state.cancel_event.is_set():
        raise ScriptCancelledError("Script cancelled.")
    if state.has_sent_command and state.inter_command_delay > 0:
        time.sleep(state.inter_command_delay)
    session.send_command(command)
    state.sent_commands.append(command)
    state.has_sent_command = True


def _is_control_line(stripped: str) -> bool:
    """Return whether one stripped script line is a control directive."""

    lowered = stripped.lower()
    return (
        lowered.startswith("if ")
        or lowered in {"else", "endif"}
        or stripped.startswith("/")
    )
