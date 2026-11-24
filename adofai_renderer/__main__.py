"""Module entrypoint for `python -m adofai_renderer`."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import run_cli
        return run_cli(sys.argv[1:])
    from .gui import launch_gui
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
