#!/usr/bin/env bash
# Open Orion — one-command installer for Linux.
#
# Creates a virtual environment and installs Open Orion. By default it
# installs everything (cloud API + voice + the streaming voice-cloning
# pipeline); pass a variant to install less:
#
#     ./install.sh                 # everything (default)
#     ./install.sh --core          # CLI only (no cloud API, no voice)
#     ./install.sh --api           # + cloud API backends (LiteLLM)
#     ./install.sh --voice         # + local STT/TTS
#     ./install.sh --voice-pipeline  # + streaming voice-cloning tool
#
# The GUI / HUD need tkinter (a system package): sudo pacman -S tk
# on Arch, or sudo apt install python3-tk on Debian/Ubuntu.

set -u

# This script lives in scripts/; every path is relative to the repo root.
cd "$(dirname "$0")/.."

VENV=".venv"
VARIANT="all"
MODE="install"

for arg in "$@"; do
    case "$arg" in
        --core)          VARIANT="core" ;;
        --api)           VARIANT="api" ;;
        --voice)         VARIANT="voice" ;;
        --voice-pipeline) VARIANT="voice-pipeline" ;;
        --venv-prefix)   VENV=".venv-${VARIANT}" ;;
        --recreate)      MODE="recreate" ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *)
            echo "install.sh: unknown option: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

# --- find a Python 3.11+ --------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
            PY="$candidate"
            break
        fi
    fi
done
if [[ -z "$PY" ]]; then
    echo "install.sh: no Python >= 3.11 found. Install one (e.g. python3.11) and retry." >&2
    exit 1
fi
echo "→ using $PY"

# --- create / refresh the venv --------------------------------------------
if [[ "$MODE" == "recreate" ]]; then
    echo "→ removing existing $VENV"
    rm -rf "$VENV"
fi
if [[ ! -d "$VENV" ]]; then
    echo "→ creating virtual environment in $VENV"
    "$PY" -m venv "$VENV" || { echo "install.sh: venv creation failed" >&2; exit 1; }
fi
"$VENV/bin/python" -m pip install --upgrade pip -q

# --- install the package --------------------------------------------------
case "$VARIANT" in
    core)
        echo "→ installing core (no cloud API, no voice)"
        "$VENV/bin/pip" install -e .
        ;;
    api)
        echo "→ installing core + cloud API backends"
        "$VENV/bin/pip" install -e ".[api]"
        ;;
    voice)
        echo "→ installing core + local voice"
        "$VENV/bin/pip" install -e ".[voice]"
        ;;
    voice-pipeline)
        echo "→ installing core + streaming voice-cloning pipeline"
        "$VENV/bin/pip" install -e ".[voice-pipeline]"
        ;;
    all)
        echo "→ installing everything"
        "$VENV/bin/pip" install -e ".[all]"
        ;;
esac || { echo "install.sh: package install failed" >&2; exit 1; }

# --- optional first-run niceties ------------------------------------------
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "→ created .env from .env.example (edit it to tweak settings)"
fi

echo
echo "✓ Open Orion installed in $VENV"
echo
echo "Next steps:"
echo "  1. Start a local model:     ollama serve && ollama pull qwen3.5:4b"
echo "  2. Run the CLI:             $VENV/bin/python main.py"
echo "  3. Run the HUD:             $VENV/bin/python main.py --gui"
echo "                              (or $VENV/bin/python -m orion.ui.jarvis_hud)"
echo "     (tkinter is a system package — see the top of install.sh)"
echo "  4. Voice-cloning pipeline:  $VENV/bin/python tools/voice_pipeline.py --interactive"