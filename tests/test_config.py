"""Tests for configuration loading and example writing."""

from pathlib import Path

import pytest

from serialk.config import (
    ConfigurationError,
    load_config,
    parse_line_ending,
    resolve_profile,
    write_example_config,
)


def test_load_config_and_resolve_profile(tmp_path: Path) -> None:
    """Load a valid multi-profile configuration and resolve one profile."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[profiles.instrument]
port = "/dev/ttyUSB0"
baudrate = 115200
line_ending = "crlf"
log_dir = "/tmp/logs"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    profile = resolve_profile(config, "instrument")

    assert profile.port == "/dev/ttyUSB0"
    assert profile.baudrate == 115200
    assert profile.line_ending == "\r\n"
    assert profile.log_dir == Path("/tmp/logs")
    assert parse_line_ending(r"\n") == "\n"


def test_write_example_config_creates_toml_file(tmp_path: Path) -> None:
    """Write an example config file that can be parsed again."""

    config_path = tmp_path / "serialk.toml"
    written_path = write_example_config(config_path)
    config = load_config(written_path)

    assert written_path.exists()
    assert "instrument_shell" in config.profiles
    assert "instrument_simulator" in config.profiles


def test_load_config_rejects_missing_profiles(tmp_path: Path) -> None:
    """Reject TOML files that do not define any profiles."""

    config_path = tmp_path / "empty.toml"
    config_path.write_text("title = 'serialk'", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_config(config_path)
