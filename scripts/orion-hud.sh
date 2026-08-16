#!/usr/bin/env bash
# Open Orion HUD desktop launcher.
# Detached from any terminal; picks a Python that has tkinter, starts the
# futuristic HUD. Voice is activated with the on-screen mic button
# (push to talk) — the engine is loaded at startup and stays passive.

set -e
# This script lives in scripts/; run from the repo root so orion.* imports work.
cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import tkinter' >/dev/null 2>&1; then
    PY=.venv/bin/python
else
    echo "Open Orion needs tkinter. Install it with:  sudo pacman -S tk" >&2
    exit 3
fi

exec "$PY" -m orion.ui.jarvis_hud --provider ollama "$@"