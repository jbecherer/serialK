"""Configuration models and loading for serialk."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import tomllib

from platformdirs import user_config_path, user_state_path


class ConfigurationError(ValueError):
    """Raised when the application configuration is invalid."""


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    """Configuration for one serial-connected instrument."""

    name: str
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    parity: str = "N"
    stopbits: float = 1.0
    timeout: float = 0.2
    write_timeout: float = 1.0
    encoding: str = "utf-8"
    line_ending: str = "\n"
    log_dir: Path | None = None

    def merged(self, **overrides: object) -> InstrumentProfile:
        """Return a copy of the profile with non-``None`` overrides applied.

        Parameters
        ----------
        **overrides:
            Candidate replacement values for dataclass fields.

        Returns
        -------
        InstrumentProfile
            A new profile containing the requested overrides.

        Examples
        --------
        >>> profile = InstrumentProfile(name="demo", port="/dev/ttyUSB0")
        >>> profile.merged(baudrate=115200).baudrate
        115200
        """

        filtered_overrides = {
            key: value for key, value in overrides.items() if value is not None
        }
        if not filtered_overrides:
            return self
        return replace(self, **filtered_overrides)


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Application configuration containing named instrument profiles."""

    source_path: Path
    profiles: dict[str, InstrumentProfile]


def default_config_path() -> Path:
    """Return the default XDG configuration path for serialk.

    Returns
    -------
    Path
        The default TOML configuration file path.
    """

    return user_config_path("serialk") / "config.toml"


def default_history_path() -> Path:
    """Return the prompt history file path and ensure its parent exists.

    Returns
    -------
    Path
        The prompt history file path under the XDG state directory.
    """

    history_path = user_state_path("serialk") / "history.txt"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    return history_path


def parse_line_ending(value: str) -> str:
    """Normalize a line ending token into its runtime representation.

    Parameters
    ----------
    value:
        Line ending string or token such as ``"lf"`` or ``"\\r\\n"``.

    Returns
    -------
    str
        The normalized line ending string.
    """

    normalized = value.lower()
    aliases = {
        "lf": "\n",
        r"\n": "\n",
        "cr": "\r",
        r"\r": "\r",
        "crlf": "\r\n",
        r"\r\n": "\r\n",
    }
    return aliases.get(normalized, value)


def load_config(config_path: Path) -> AppConfig:
    """Load the application configuration from a TOML file.

    Parameters
    ----------
    config_path:
        TOML configuration file containing a ``[profiles]`` table.

    Returns
    -------
    AppConfig
        Parsed configuration containing all named instrument profiles.

    Raises
    ------
    ConfigurationError
        If the file is missing, malformed, or contains invalid settings.

    Examples
    --------
    >>> path = Path("config.toml")
    >>> load_config(path)  # doctest: +SKIP
    AppConfig(source_path=path, profiles={...})
    """

    if not config_path.exists():
        raise ConfigurationError(
            f"Configuration file not found: {config_path}. "
            f"Create one with '--write-example-config {config_path}'."
        )

    try:
        with config_path.open("rb") as handle:
            raw_config = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"Invalid TOML in configuration file {config_path}: {exc}"
        ) from exc

    raw_profiles = raw_config.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        raise ConfigurationError(
            "Configuration file must define at least one profile under [profiles.<name>]."
        )

    profiles = {
        profile_name: _parse_profile(profile_name, raw_profile)
        for profile_name, raw_profile in raw_profiles.items()
    }
    return AppConfig(source_path=config_path, profiles=profiles)


def resolve_profile(config: AppConfig, profile_name: str) -> InstrumentProfile:
    """Return one named profile from the loaded application config.

    Parameters
    ----------
    config:
        Loaded application configuration.
    profile_name:
        Name of the desired profile.

    Returns
    -------
    InstrumentProfile
        The selected profile.

    Raises
    ------
    ConfigurationError
        If the named profile is not defined.
    """

    try:
        return config.profiles[profile_name]
    except KeyError as exc:
        known_profiles = ", ".join(sorted(config.profiles))
        raise ConfigurationError(
            f"Unknown profile '{profile_name}'. Available profiles: {known_profiles}"
        ) from exc


def resolve_log_dir(
    profile: InstrumentProfile,
    cli_log_dir: Path | None,
) -> Path:
    """Resolve the session log directory from CLI and profile settings.

    Parameters
    ----------
    profile:
        Instrument profile selected for the current run.
    cli_log_dir:
        Command-line override for the log directory.

    Returns
    -------
    Path
        Resolved log directory path.

    Raises
    ------
    ConfigurationError
        If neither the CLI nor the profile defines a log directory.
    """

    resolved_path = cli_log_dir or profile.log_dir
    if resolved_path is None:
        raise ConfigurationError(
            "A log directory is required. Set it in the profile or pass --log-dir."
        )
    return resolved_path.expanduser()


def write_example_config(destination: Path) -> Path:
    """Write an example multi-profile TOML configuration file.

    Parameters
    ----------
    destination:
        Output path for the example configuration.

    Returns
    -------
    Path
        The written configuration path.

    Raises
    ------
    FileExistsError
        If the destination file already exists.
    """

    destination = destination.expanduser()
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite existing config file: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_example_config_text(), encoding="utf-8")
    return destination


def _parse_profile(profile_name: str, raw_profile: object) -> InstrumentProfile:
    """Validate and convert one raw profile section into a dataclass."""

    if not isinstance(raw_profile, dict):
        raise ConfigurationError(
            f"Profile '{profile_name}' must be defined as a TOML table."
        )

    port = raw_profile.get("port")
    if not isinstance(port, str) or not port.strip():
        raise ConfigurationError(
            f"Profile '{profile_name}' must define a non-empty string 'port'."
        )

    baudrate = _require_type(raw_profile, profile_name, "baudrate", int, default=9600)
    bytesize = _require_type(raw_profile, profile_name, "bytesize", int, default=8)
    parity = _require_type(raw_profile, profile_name, "parity", str, default="N").upper()
    stopbits = _require_number(raw_profile, profile_name, "stopbits", default=1.0)
    timeout = _require_number(raw_profile, profile_name, "timeout", default=0.2)
    write_timeout = _require_number(
        raw_profile, profile_name, "write_timeout", default=1.0
    )
    encoding = _require_type(
        raw_profile, profile_name, "encoding", str, default="utf-8"
    )
    line_ending = parse_line_ending(
        _require_type(raw_profile, profile_name, "line_ending", str, default="\n")
    )
    log_dir_raw = raw_profile.get("log_dir")

    if bytesize not in {5, 6, 7, 8}:
        raise ConfigurationError(
            f"Profile '{profile_name}' has invalid bytesize {bytesize}; expected 5, 6, 7, or 8."
        )
    if parity not in {"N", "E", "O", "M", "S"}:
        raise ConfigurationError(
            f"Profile '{profile_name}' has invalid parity '{parity}'."
        )
    if stopbits not in {1.0, 1.5, 2.0}:
        raise ConfigurationError(
            f"Profile '{profile_name}' has invalid stopbits {stopbits}; expected 1, 1.5, or 2."
        )
    if timeout < 0 or write_timeout < 0:
        raise ConfigurationError(
            f"Profile '{profile_name}' must use non-negative timeout values."
        )

    log_dir = None
    if log_dir_raw is not None:
        if not isinstance(log_dir_raw, str) or not log_dir_raw.strip():
            raise ConfigurationError(
                f"Profile '{profile_name}' has invalid 'log_dir'; expected a non-empty string."
            )
        log_dir = Path(log_dir_raw).expanduser()

    return InstrumentProfile(
        name=profile_name,
        port=port,
        baudrate=baudrate,
        bytesize=bytesize,
        parity=parity,
        stopbits=stopbits,
        timeout=timeout,
        write_timeout=write_timeout,
        encoding=encoding,
        line_ending=line_ending,
        log_dir=log_dir,
    )


def _require_type(
    mapping: dict[str, object],
    profile_name: str,
    key: str,
    expected_type: type[object],
    *,
    default: object,
) -> object:
    """Return a typed value from a profile mapping."""

    value = mapping.get(key, default)
    if not isinstance(value, expected_type):
        raise ConfigurationError(
            f"Profile '{profile_name}' field '{key}' must be of type "
            f"{expected_type.__name__}."
        )
    return value


def _require_number(
    mapping: dict[str, object],
    profile_name: str,
    key: str,
    *,
    default: float,
) -> float:
    """Return a numeric profile value as ``float``."""

    value = mapping.get(key, default)
    if not isinstance(value, (int, float)):
        raise ConfigurationError(
            f"Profile '{profile_name}' field '{key}' must be numeric."
        )
    return float(value)


def _example_config_text() -> str:
    """Return example TOML content for a new configuration file."""

    return """[profiles.instrument_shell]
port = "/dev/ttyUSB0"
baudrate = 115200
bytesize = 8
parity = "N"
stopbits = 1.0
timeout = 0.2
write_timeout = 1.0
encoding = "utf-8"
line_ending = "crlf"
log_dir = "/absolute/path/to/instrument/logs"

[profiles.instrument_simulator]
port = "sim://default"
baudrate = 115200
bytesize = 8
parity = "N"
stopbits = 1.0
timeout = 0.1
write_timeout = 1.0
encoding = "utf-8"
line_ending = "lf"
log_dir = "/absolute/path/to/simulator/logs"
"""
