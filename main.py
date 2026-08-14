#!/usr/bin/env python3
"""Open Orion — autonomous Linux shell agent. Entry point."""

import sys

from orion.cli import main as cli_main


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--gui" in argv or "-g" in argv:
        rest = [a for a in argv if a not in ("--gui", "-g")]
        try:
            import tkinter  # noqa: F401
        except ImportError as exc:
            print(
                "open-orion: GUI needs tkinter, which this Python lacks:\n"
                f"  {exc}\n"
                "Install it with:  sudo pacman -S tk\n"
                "or run via the bundled GUI venv:  ./.venv-gui/bin/python main.py --gui",
                file=sys.stderr,
            )
            return 3
        from orion.gui import main as gui_main
        return gui_main(rest)
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
