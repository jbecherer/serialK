"""Module entry point for ``python -m serialk``."""

from serialk.cli import main


def run() -> None:
    """Execute the CLI entry point."""

    main()


if __name__ == "__main__":
    run()
