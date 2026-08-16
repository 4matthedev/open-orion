#!/usr/bin/env bash
# Open Orion — portable Linux installer (for the .tar.gz bundle).
set -e

mkdir -p /usr/local/bin
install -Dm755 orion /usr/local/bin/orion
install -Dm755 orion-hud /usr/local/bin/orion-hud

echo "Open Orion installed."
echo "Run 'orion' (CLI), 'orion --gui' (JARVIS deck), or 'orion-hud'."