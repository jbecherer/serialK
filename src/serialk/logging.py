"""Session logging for serial device communication."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import threading
from typing import TextIO


class SessionLogger:
    """Append timestamped TX/RX entries to one log file per session."""

    def __init__(self, profile_name: str, log_dir: Path) -> None:
        """Create a new per-session logger.

        Parameters
        ----------
        profile_name:
            Name of the active instrument profile.
        log_dir:
            Directory where the per-session log file will be created.
        """

        self._lock = threading.Lock()
        self.path = self._build_log_path(profile_name, log_dir)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: TextIO = self.path.open("a", encoding="utf-8")

    def log_event(self, message: str) -> None:
        """Write a session event entry."""

        self._write("EVENT", message)

    def log_tx(self, command: str) -> None:
        """Write one transmitted command entry."""

        self._write("TX", command)

    def log_rx(self, message: str) -> None:
        """Write one received line entry."""

        self._write("RX", message)

    def close(self) -> None:
        """Flush and close the session log file."""

        with self._lock:
            if self._handle.closed:
                return
            self._handle.flush()
            self._handle.close()

    def _write(self, marker: str, payload: str) -> None:
        """Write one formatted entry to disk."""

        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        normalized_payload = payload.replace("\n", "\\n")
        with self._lock:
            self._handle.write(f"[{timestamp}] {marker:<5} {normalized_payload}\n")
            self._handle.flush()

    @staticmethod
    def _build_log_path(profile_name: str, log_dir: Path) -> Path:
        """Construct a unique session log filename for one profile."""

        safe_profile = re.sub(r"[^A-Za-z0-9._-]+", "-", profile_name).strip("-")
        timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S")
        return log_dir / f"{safe_profile}_{timestamp}.log"
