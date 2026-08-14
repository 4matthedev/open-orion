#!/usr/bin/env bash
# Launch Open Orion's JARVIS-style GUI.
# Prefers the bundled GUI venv (Python 3.12 with Tk); falls back to the
# main venv if it has tkinter available.

set -e
cd "$(dirname "$0")"

if [[ -x .venv-gui/bin/python ]] && .venv-gui/bin/python -c 'import tkinter' >/dev/null 2>&1; then
    exec .venv-gui/bin/python main.py --gui "$@"
elif [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import tkinter' >/dev/null 2>&1; then
    exec .venv/bin/python main.py --gui "$@"
else
    echo "Open Orion GUI needs tkinter. Install it with:  sudo pacman -S tk" >&2
    exit 3
fi
