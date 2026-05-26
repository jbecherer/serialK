"""Serial session management for interactive and scripted I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Callable, Protocol

import serial

from serialk.config import InstrumentProfile
from serialk.logging import SessionLogger
from serialk.simulator import SimulatedSerialTransport


class SerialSessionError(RuntimeError):
    """Raised when the serial session cannot complete an operation."""


class LineTransport(Protocol):
    """Protocol for line-oriented serial transports."""

    is_open: bool
    timeout: float | None
    write_timeout: float | None

    def write(self, payload: bytes) -> int:
        """Write raw bytes to the transport."""

    def readline(self) -> bytes:
        """Read one line of bytes from the transport."""

    def close(self) -> None:
        """Close the transport."""


@dataclass(frozen=True, slots=True)
class SessionStatus:
    """Runtime status summary for the active session."""

    profile_name: str
    port: str
    connected: bool
    commands_sent: int
    messages_received: int
    log_path: Path
    last_error: str | None


@dataclass(slots=True)
class _LineWaiter:
    """Thread-safe wait registration for one awaited substring."""

    substring: str
    event: threading.Event = field(default_factory=threading.Event)
    matched_line: str | None = None


class SerialSession:
    """Manage one text-based serial connection and its reader thread."""

    def __init__(
        self,
        profile: InstrumentProfile,
        logger: SessionLogger,
        *,
        display_callback: Callable[[str], None] | None = None,
        transport_factory: Callable[[InstrumentProfile], LineTransport] | None = None,
    ) -> None:
        """Create a new serial session.

        Parameters
        ----------
        profile:
            Active instrument profile.
        logger:
            Session logger receiving TX/RX events.
        display_callback:
            Callback invoked for each received line and asynchronous error message.
        transport_factory:
            Optional override used by tests to create a transport instance.
        """

        self.profile = profile
        self.logger = logger
        self._display_callback = display_callback or (lambda _message: None)
        self._transport_factory = transport_factory or open_transport
        self._transport: LineTransport | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._waiter_lock = threading.Lock()
        self._waiters: list[_LineWaiter] = []
        self._commands_sent = 0
        self._messages_received = 0
        self._last_error: str | None = None

    @property
    def is_connected(self) -> bool:
        """Return whether the transport is currently open."""

        return self._transport is not None and self._transport.is_open

    def connect(self) -> None:
        """Open the transport and start the background reader thread.

        Raises
        ------
        SerialSessionError
            If the transport cannot be opened.
        """

        if self.is_connected:
            return

        try:
            transport = self._transport_factory(self.profile)
        except serial.SerialException as exc:
            raise SerialSessionError(
                f"Unable to open serial port '{self.profile.port}': {exc}"
            ) from exc

        self._transport = transport
        self._reader_stop_event.clear()
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="serialk-reader",
            daemon=True,
        )
        self._reader_thread.start()
        self.logger.log_event(f"CONNECTED port={self.profile.port}")

    def set_display_callback(self, callback: Callable[[str], None]) -> None:
        """Replace the callback used for received lines and async errors.

        Parameters
        ----------
        callback:
            Callback invoked whenever the session needs to display a line.
        """

        self._display_callback = callback

    def disconnect(self) -> None:
        """Stop the reader thread and close the transport."""

        transport = self._transport
        if transport is None:
            return

        self._reader_stop_event.set()
        transport.close()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        self._transport = None
        self.logger.log_event(f"DISCONNECTED port={self.profile.port}")

    def reconnect(self) -> None:
        """Reconnect using the current session profile."""

        self.disconnect()
        self.connect()

    def send_command(self, command: str) -> None:
        """Send one text command to the connected device.

        Parameters
        ----------
        command:
            Command string without the configured line ending.

        Raises
        ------
        SerialSessionError
            If the session is not connected or the write fails.
        """

        if not command.strip():
            raise SerialSessionError("Refusing to send an empty command.")

        transport = self._transport
        if transport is None or not transport.is_open:
            raise SerialSessionError("Serial session is not connected.")

        payload = f"{command}{self.profile.line_ending}".encode(self.profile.encoding)
        try:
            transport.write(payload)
        except serial.SerialException as exc:
            raise SerialSessionError(f"Unable to write to serial port: {exc}") from exc

        with self._state_lock:
            self._commands_sent += 1
        self.logger.log_tx(command)

    def status(self) -> SessionStatus:
        """Return a snapshot of the current session state."""

        with self._state_lock:
            return SessionStatus(
                profile_name=self.profile.name,
                port=self.profile.port,
                connected=self.is_connected,
                commands_sent=self._commands_sent,
                messages_received=self._messages_received,
                log_path=self.logger.path,
                last_error=self._last_error,
            )

    def wait_for_substring(self, substring: str, timeout: float) -> str | None:
        """Wait for a future RX line containing the given substring.

        Parameters
        ----------
        substring:
            Case-sensitive substring to match in future received lines.
        timeout:
            Maximum wait time in seconds.

        Returns
        -------
        str | None
            The first matching line, or ``None`` if the timeout expires.

        Raises
        ------
        SerialSessionError
            If the session is disconnected or the arguments are invalid.
        """

        if not substring:
            raise SerialSessionError("Cannot wait for an empty substring.")
        if timeout < 0:
            raise SerialSessionError("Wait timeout must be non-negative.")
        if not self.is_connected:
            raise SerialSessionError("Serial session is not connected.")

        waiter = _LineWaiter(substring=substring)
        self.logger.log_event(f"WAIT substring={substring!r} timeout={timeout:.3f}")
        with self._waiter_lock:
            self._waiters.append(waiter)

        matched = waiter.event.wait(timeout)
        with self._waiter_lock:
            if waiter in self._waiters:
                self._waiters.remove(waiter)

        if matched and waiter.matched_line is not None:
            self.logger.log_event(
                f"MATCH substring={substring!r} line={waiter.matched_line!r}"
            )
            return waiter.matched_line

        self.logger.log_event(f"TIMEOUT substring={substring!r} timeout={timeout:.3f}")
        return None

    def close(self) -> None:
        """Close the session and its logger."""

        self.disconnect()
        self.logger.close()

    def _reader_loop(self) -> None:
        """Continuously read lines from the transport and fan them out."""

        assert self._transport is not None
        transport = self._transport

        while not self._reader_stop_event.is_set():
            try:
                payload = transport.readline()
            except serial.SerialException as exc:
                self._record_async_error(f"Serial read failed: {exc}")
                self._transport = None
                return

            if payload == b"":
                continue

            line = payload.decode(self.profile.encoding, errors="replace").rstrip("\r\n")
            with self._state_lock:
                self._messages_received += 1
            self.logger.log_rx(line)
            self._notify_waiters(line)
            self._display_callback(line)

    def _record_async_error(self, message: str) -> None:
        """Store, log, and display one asynchronous session error."""

        with self._state_lock:
            self._last_error = message
        self.logger.log_event(message)
        self._display_callback(f"[error] {message}")

    def _notify_waiters(self, line: str) -> None:
        """Wake registered waiters whose substring matches the received line."""

        with self._waiter_lock:
            for waiter in self._waiters:
                if waiter.matched_line is None and waiter.substring in line:
                    waiter.matched_line = line
                    waiter.event.set()


def open_transport(profile: InstrumentProfile) -> LineTransport:
    """Open a real or simulated transport for one profile.

    Parameters
    ----------
    profile:
        Instrument profile defining the connection settings.

    Returns
    -------
    LineTransport
        Open transport object implementing line-oriented reads.
    """

    if profile.port.startswith("sim://"):
        return SimulatedSerialTransport(
            port=profile.port,
            encoding=profile.encoding,
            timeout=profile.timeout,
        )

    return serial.serial_for_url(
        url=profile.port,
        baudrate=profile.baudrate,
        bytesize=profile.bytesize,
        parity=profile.parity,
        stopbits=profile.stopbits,
        timeout=profile.timeout,
        write_timeout=profile.write_timeout,
        do_not_open=False,
    )
