"""Serial transport simulator used for local development and tests."""

from __future__ import annotations

from queue import Empty, Queue
import threading
import time


class SimulatedSerialTransport:
    """In-memory serial transport with command responses and live measurements."""

    def __init__(
        self,
        *,
        port: str,
        encoding: str,
        timeout: float,
        measurement_interval: float = 0.2,
    ) -> None:
        """Create a simulated transport.

        Parameters
        ----------
        port:
            Port or URL string shown in status output.
        encoding:
            Text encoding used by the session.
        timeout:
            Read timeout in seconds.
        measurement_interval:
            Interval between synthetic measurement lines.
        """

        self.port = port
        self.encoding = encoding
        self.timeout = timeout
        self.write_timeout = 1.0
        self.is_open = True
        self._measurement_enabled = True
        self._measurement_interval = measurement_interval
        self._counter = 0
        self._incoming_lines: Queue[bytes] = Queue()
        self._stop_event = threading.Event()
        self._measurement_thread = threading.Thread(
            target=self._emit_measurements,
            name="serialk-simulator",
            daemon=True,
        )
        self._incoming_lines.put(self._encode_line("SIM READY"))
        self._measurement_thread.start()

    def write(self, payload: bytes) -> int:
        """Accept one outbound command and enqueue a response."""

        command = payload.decode(self.encoding, errors="replace").strip()
        if not command:
            return len(payload)

        response_lines = self._handle_command(command)
        for line in response_lines:
            self._incoming_lines.put(self._encode_line(line))
        return len(payload)

    def readline(self) -> bytes:
        """Return one line of simulator output."""

        if not self.is_open:
            return b""
        try:
            return self._incoming_lines.get(timeout=self.timeout)
        except Empty:
            return b""

    def close(self) -> None:
        """Stop the simulator and close the transport."""

        if not self.is_open:
            return
        self.is_open = False
        self._stop_event.set()
        self._measurement_thread.join(timeout=1.0)

    def _emit_measurements(self) -> None:
        """Continuously enqueue synthetic measurement lines."""

        while not self._stop_event.is_set():
            time.sleep(self._measurement_interval)
            if not self._measurement_enabled or not self.is_open:
                continue
            self._counter += 1
            self._incoming_lines.put(
                self._encode_line(f"MEAS index={self._counter} value={20.0 + self._counter:.1f}")
            )

    def _handle_command(self, command: str) -> list[str]:
        """Return response lines for one simulator command."""

        lowered = command.lower()
        if lowered == "help":
            return ["COMMANDS: help, ping, status, shell, start, stop"]
        if lowered == "ping":
            return ["PONG"]
        if lowered == "status":
            mode = "MEASUREMENT" if self._measurement_enabled else "IDLE"
            return [f"STATUS mode={mode} measurements={self._counter}"]
        if lowered == "shell":
            self._measurement_enabled = False
            return ["SHELL READY"]
        if lowered == "start":
            self._measurement_enabled = True
            return ["MEASUREMENT STARTED"]
        if lowered == "stop":
            self._measurement_enabled = False
            return ["MEASUREMENT STOPPED"]
        return [f"OK {command}"]

    def _encode_line(self, text: str) -> bytes:
        """Encode one simulator output line."""

        return f"{text}\n".encode(self.encoding)
