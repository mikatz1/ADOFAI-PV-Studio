"""Main entrypoint for ADOFAI PV Studio (GUI by default, CLI when arguments are provided)."""

from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) > 1:
        # In packaged GUI-only builds, dropping a .adofai/.zip/folder onto the EXE opens it in Studio GUI.
        if (
            getattr(sys, "frozen", False)
            and len(sys.argv) == 2
            and not sys.argv[1].startswith("-")
            and os.path.exists(sys.argv[1])
        ):
            from adofai_renderer.gui import launch_gui
            return launch_gui(initial_level=sys.argv[1])

        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        from adofai_renderer.cli import run_cli
        return run_cli(sys.argv[1:])

    from adofai_renderer.gui import launch_gui
    return launch_gui()


if __name__ == "__main__":
    raise SystemExit(main())
