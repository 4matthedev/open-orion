#!/usr/bin/env bash
# Open Orion — portable Linux installer (for the .tar.gz bundle).
#
# Usage:
#   sudo ./install.sh              # install app + bootstrap Ollama/qwen3.5:4b
#   sudo ./install.sh --no-model   # install only; skip Ollama + model download
#
# Parity with the Windows installer:
#   * installs the CLI (orion) and the HUD (orion-hud)
#   * installs Ollama if it isn't already present
#   * pulls the default model (qwen3.5:4b) so the first launch just works
#   * adds a desktop-menu entry with the app icon

set -euo pipefail

MODEL="${ORION_MODEL:-qwen3.5:4b}"
want_model=1
for arg in "$@"; do
    case "$arg" in
        --no-model) want_model=0 ;;
        --help|-h)
            sed -n '1,20p' "$0"
            exit 0
            ;;
    esac
done

# Run from wherever the bundle was unpacked (binaries live beside this script).
cd "$(dirname "$0")"

prefix="${DESTDIR:-/usr/local}"
echo "-> installing Open Orion into $prefix/bin"

install -Dm755 orion "$prefix/bin/orion"
install -Dm755 orion-hud "$prefix/bin/orion-hud"

# Desktop-menu entry + icon so the HUD shows up in the app menu.
if [[ -z "${DESTDIR:-}" ]]; then
    install -Dm644 packaging_extra/open-orion.svg \
        /usr/share/icons/hicolor/scalable/apps/open-orion.svg 2>/dev/null || true
    install -Dm644 packaging_extra/open-orion-hud.desktop \
        /usr/share/applications/open-orion-hud.desktop 2>/dev/null || true
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
    fi
fi

# --- Ollama -----------------------------------------------------------------
install_ollama=0
if command -v ollama >/dev/null 2>&1; then
    echo "-> Ollama already installed ($(ollama --version 2>/dev/null | head -1))"
else
    install_ollama=1
    if [[ "$want_model" -eq 1 ]]; then
        echo "-> installing Ollama (official script, needs sudo)"
        if [[ "$(id -u)" -ne 0 ]]; then
            echo "   rerun with sudo so the installer can set up Ollama: sudo ./install.sh"
            install_ollama=0
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
    fi
fi

# --- default model -----------------------------------------------------------
pull_model=0
if [[ "$want_model" -eq 1 ]] && (command -v ollama >/dev/null 2>&1 || [[ "$install_ollama" -eq 1 ]]); then
    if command -v ollama >/dev/null 2>&1 && ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
        echo "-> model $MODEL already present"
    else
        pull_model=1
        echo "-> pulling $MODEL (this can take a while; run 'ollama pull $MODEL' later to resume)"
        ollama pull "$MODEL"
    fi
fi

# --- default via /etc/profile.d so non-login shells see the env --------------
if [[ -z "${DESTDIR:-}" ]]; then
    cat > /etc/profile.d/open-orion.sh <<EOF
# Open Orion defaults (set by its installer)
export ORION_OLLAMA_MODEL="$MODEL"
EOF
fi

echo
echo "✓ Open Orion installed."
echo "  Run 'orion' (CLI) or 'orion-hud' (HUD)."
if command -v ollama >/dev/null 2>&1; then
    echo "  (ollama is running locally; pull any model with 'ollama pull <name>')"
fi