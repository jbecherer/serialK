"""Command-line interface for serialk."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

from serialk.config import (
    ConfigurationError,
    default_config_path,
    default_history_path,
    load_config,
    parse_line_ending,
    resolve_log_dir,
    resolve_profile,
    write_example_config,
)
from serialk.console import InteractiveConsole
from serialk.logging import SessionLogger
from serialk.script_runner import ScriptSyntaxError, run_script
from serialk.serial_session import SerialSession, SerialSessionError


def main() -> None:
    """Run the serialk command-line interface."""

    parser = build_parser()
    args = parser.parse_args()

    if args.write_example_config is not None:
        try:
            _handle_write_example_config(args.write_example_config)
        except FileExistsError as exc:
            _exit_with_error(str(exc))
        return

    if not args.profile:
        parser.error("--profile is required unless --write-example-config is used.")

    try:
        config = load_config(args.config)
        base_profile = resolve_profile(config, args.profile)
        profile = base_profile.merged(
            port=args.port,
            baudrate=args.baudrate,
            bytesize=args.bytesize,
            parity=args.parity.upper() if args.parity else None,
            stopbits=args.stopbits,
            timeout=args.timeout,
            write_timeout=args.write_timeout,
            encoding=args.encoding,
            line_ending=parse_line_ending(args.line_ending)
            if args.line_ending is not None
            else None,
        )
        log_dir = resolve_log_dir(profile, args.log_dir)
        history_path = default_history_path()
    except (ConfigurationError, FileExistsError) as exc:
        _exit_with_error(str(exc))
        return

    logger = SessionLogger(profile.name, log_dir)
    session = SerialSession(profile, logger)
    console = InteractiveConsole(
        session,
        history_path=history_path,
        default_script_delay=args.script_delay,
        default_condition_timeout=args.script_condition_timeout,
    )
    session.set_display_callback(console.display_device_line)

    try:
        session.connect()
        if args.script is not None:
            sent_commands = run_script(
                session,
                args.script,
                inter_command_delay=args.script_delay,
                condition_timeout=args.script_condition_timeout,
            )
            console.display_message(
                f"Sent {len(sent_commands)} startup commands from {args.script}."
            )
        asyncio.run(console.run())
    except (ConfigurationError, OSError, SerialSessionError, ValueError, ScriptSyntaxError) as exc:
        logger.log_event(f"ERROR {exc}")
        _exit_with_error(str(exc))
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""

    parser = argparse.ArgumentParser(
        prog="serialk",
        description=(
            "Interactive serial-port console with per-session logging, "
            "instrument profiles, and plain-text script execution."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="Path to the TOML configuration file.",
    )
    parser.add_argument(
        "--profile",
        help="Named instrument profile from the configuration file.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Override the session log directory.",
    )
    parser.add_argument(
        "--script",
        type=Path,
        help="Run a plain-text command script before entering the console.",
    )
    parser.add_argument(
        "--script-delay",
        type=float,
        default=0.0,
        help="Optional fixed delay in seconds between scripted commands.",
    )
    parser.add_argument(
        "--script-condition-timeout",
        type=float,
        default=5.0,
        help=(
            "Default timeout in seconds for conditional script blocks that wait "
            "for incoming instrument text."
        ),
    )
    parser.add_argument("--port", help="Override the serial port or URL.")
    parser.add_argument(
        "--baudrate",
        type=int,
        help="Override the serial baud rate.",
    )
    parser.add_argument(
        "--bytesize",
        type=int,
        choices=[5, 6, 7, 8],
        help="Override the serial byte size.",
    )
    parser.add_argument(
        "--parity",
        choices=["N", "E", "O", "M", "S", "n", "e", "o", "m", "s"],
        help="Override the serial parity.",
    )
    parser.add_argument(
        "--stopbits",
        type=float,
        choices=[1.0, 1.5, 2.0],
        help="Override the serial stop bits.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Override the serial read timeout in seconds.",
    )
    parser.add_argument(
        "--write-timeout",
        type=float,
        help="Override the serial write timeout in seconds.",
    )
    parser.add_argument(
        "--encoding",
        help="Override the text encoding used for serial I/O.",
    )
    parser.add_argument(
        "--line-ending",
        help=r"Override the command line ending (e.g. lf, crlf, \n, \r\n).",
    )
    parser.add_argument(
        "--write-example-config",
        type=Path,
        help="Write an example config file and exit.",
    )
    return parser


def _handle_write_example_config(destination: Path) -> None:
    """Write an example config file and report the output path."""

    written_path = write_example_config(destination)
    print(f"Wrote example configuration to {written_path}")


def _exit_with_error(message: str) -> None:
    """Print one CLI error to stderr and exit with status code 1."""

    print(f"Error: {message}", file=sys.stderr)
    raise SystemExit(1)
