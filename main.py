#!/usr/bin/env python3
"""Open Orion — autonomous shell agent. Entry point."""

import sys

from orion.cli import main as cli_main
from orion.platform import is_windows


def _tk_hint(exc: Exception) -> str:
    if is_windows():
        return (
            "Install the standard Python from python.org (bundles tkinter), or\n"
            "run:  python -m pip install --upgrade pip\n"
        )
    return (
        "Install it with:  sudo pacman -S tk\n"
        "or run via the bundled GUI venv:  ./.venv-gui/bin/python main.py --gui"
    )


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--gui" in argv or "-g" in argv:
        rest = [a for a in argv if a not in ("--gui", "-g")]
        try:
            import tkinter  # noqa: F401, PLC0415 - availability probe before GUI import
        except ImportError as exc:
            print(
                "open-orion: GUI needs tkinter, which this Python lacks:\n"
                f"  {exc}\n"
                "%s" % _tk_hint(exc),
                file=sys.stderr,
            )
            return 3
        from orion.gui import main as gui_main  # noqa: PLC0415 - lazy, GUI-only dep
        return gui_main(rest)
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
