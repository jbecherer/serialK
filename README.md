# serialk

`serialk` is a Linux-focused Python CLI for working with scientific instruments over a serial port. It provides one interactive terminal console for live command entry and streaming device output, supports plain-text command scripts, and writes a timestamped TX/RX log for each session to an explicit external log directory.

## Purpose

This tool targets instruments that expose both:

1. an interactive shell-like serial interface
2. a measurement mode that continuously emits textual status or measurement lines while still accepting commands

The first implementation is text-oriented, profile-driven, and designed for reproducible instrument workflows.

## Features

- installable CLI entry point: `serialk`
- TOML configuration file with multiple named instrument profiles
- per-session log files with timestamps and `TX` / `RX` markers
- interactive terminal console powered by `prompt_toolkit`
- persistent command history in XDG state storage
- plain-text script execution with optional global inter-command delay
- simulator transport for local development without physical hardware
- command-line overrides for serial settings

## Requirements

- Linux
- Python 3.12+
- `uv` for environment and dependency management

Runtime dependencies:

- `pyserial`
- `prompt_toolkit`
- `platformdirs`

## Configuration

By default, `serialk` reads its configuration from:

```text
~/.config/serialk/config.toml
```

The configuration file must define one or more profiles under `[profiles.<name>]`.

Example:

```toml
[profiles.instrument_shell]
port = "/dev/ttyUSB0"
baudrate = 115200
bytesize = 8
parity = "N"
stopbits = 1.0
timeout = 0.2
write_timeout = 1.0
encoding = "utf-8"
line_ending = "crlf"
log_dir = "/data/instrument_logs"

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
log_dir = "/data/simulator_logs"
```

### Required profile fields

| Field | Meaning |
| --- | --- |
| `port` | Serial device path or URL such as `/dev/ttyUSB0` or `sim://default` |
| `log_dir` | External directory where per-session logs are written |

### Optional profile fields

| Field | Default |
| --- | --- |
| `baudrate` | `9600` |
| `bytesize` | `8` |
| `parity` | `"N"` |
| `stopbits` | `1.0` |
| `timeout` | `0.2` |
| `write_timeout` | `1.0` |
| `encoding` | `"utf-8"` |
| `line_ending` | `"lf"` |

## Usage

Install dependencies:

```bash
uv sync
```

Write an example config file:

```bash
uv run serialk --write-example-config ~/.config/serialk/config.toml
```

Start an interactive session:

```bash
uv run serialk --profile instrument_shell
```

Override the log directory at runtime:

```bash
uv run serialk --profile instrument_shell --log-dir /mnt/lab/logs
```

Run a startup script before entering the console:

```bash
uv run serialk --profile instrument_shell --script scripts/startup.txt --script-delay 0.5 --script-condition-timeout 5.0
```

Run against the built-in simulator:

```bash
uv run serialk --profile instrument_simulator
```

### Interactive console behavior

Type raw device commands directly at the prompt. These are sent exactly once with the configured line ending appended by `serialk`.

Built-in console commands:

- `/help`
- `/status`
- `/run <script_path> [delay_seconds] [condition_timeout_seconds]` — enqueue a script; runs immediately if idle, otherwise queues behind the active script
- `/cancel` — cancel the active script and clear all pending scripts from the queue
- `/reconnect`
- `/quit`

A persistent **bottom toolbar** shows the current queue state at all times:

```
scripts: [running: startup.txt] [queued: calibrate.txt, measure.txt]
scripts: idle
```

**Ctrl-C** cancels only the currently running script. The next queued script starts automatically. `/cancel` cancels the active script **and** empties the entire queue.

Incoming device lines are shown live in the terminal without timestamps. Timestamps are preserved in the session log file instead.

## Script format

Scripts are plain UTF-8 text files with one command per line.

- blank lines are ignored
- lines starting with `#` are ignored
- all remaining lines are sent verbatim through the same session send path used by interactive commands
- delays are defined per script run, not per line inside the script file
- use `--script-delay <seconds>` for startup scripts or `/run <script_path> [delay_seconds]` in the interactive console to apply one fixed delay between all sent commands
- line-specific delays are not supported in v1
- conditional blocks are supported with `if` / `else` / `endif`
- conditional matching waits on future incoming RX lines and uses case-sensitive substring matching
- use `--script-condition-timeout <seconds>` or `/run <script_path> [delay_seconds] [condition_timeout_seconds]` to set the default timeout for conditional blocks
- one conditional block can override the default timeout with `timeout=<seconds>`

Example:

```text
# initialization commands
ping
status
start
```

Conditional example:

```text
if "READY"
    start
else
    status
endif

if "MODE=IDLE" timeout=2.0
    ping
    if "MEAS index="
        status
    else
        stop
    endif
endif
```

Conditional behavior notes:

- the `if` match only considers lines received after that `if` starts waiting
- if no matching line arrives before timeout, the `else` branch runs if present
- nested conditionals are supported

### In-script queue directives

Scripts can control the queue directly using `/queue` and `/cancel` directives.

**`/queue <path> [delay] [condition_timeout]`** — enqueue another script as the *next* job
(jumps ahead of anything already pending). Relative paths are resolved relative to the
currently executing script file.

```text
# run calibration immediately after this script finishes
/queue scripts/calibrate.txt

# override delay and condition timeout for the queued job
/queue scripts/measure.txt 0.5 10.0
```

**`/cancel`** — cancel every pending script **and** raise `ScriptCancelledError` to stop
the current script.

**`/cancel current`** — stop only the current script; leave the queue untouched.

**`/cancel <name>`** — remove all pending jobs whose filename matches `<name>`, and stop
the current script if its filename also matches.

```text
# stop this script and clear everything
/cancel

# stop only this script; next queued job continues
/cancel current

# drop any pending 'old_calibrate.txt' jobs from the queue
/cancel old_calibrate.txt
```

## Output data and logging

Each session creates one log file in the resolved log directory:

```text
<profile_name>_YYYYMMDDTHHMMSS.log
```

Each log entry contains:

- local timestamp with timezone offset
- direction marker: `EVENT`, `TX`, or `RX`
- payload text

Example:

```text
[2026-05-22T10:23:41.125+02:00] EVENT CONNECTED port=/dev/ttyUSB0
[2026-05-22T10:23:42.018+02:00] TX    status
[2026-05-22T10:23:42.032+02:00] RX    STATUS mode=MEASUREMENT measurements=12
```

## XDG file locations

`serialk` uses XDG locations on Linux for local application state:

| Purpose | Default path |
| --- | --- |
| Config file | `~/.config/serialk/config.toml` |
| Command history | `~/.local/state/serialk/history.txt` |

Session logs are intentionally **not** written to XDG state by default; an explicit external log directory must be configured in the profile or provided with `--log-dir`.

## Development and simulator workflow

Use the simulator profile to exercise both shell-like commands and continuous measurement output without a physical instrument. The simulator emits measurement lines automatically and responds to commands like `ping`, `status`, `shell`, `start`, and `stop`.

## Computational requirements

- memory: low for ordinary line-oriented serial traffic
- runtime overhead: dominated by terminal I/O and log writes
- scaling: suitable for continuous text streams; no structured parsing or large in-memory buffering is performed in v1

## Testing

Run the automated tests with:

```bash
uv run pytest
```
