"""Color themes for the Orion desktop UIs.

Both the tkinter GUI (``orion.gui``) and the standalone HUD (``jarvis_hud.py``)
pull their palettes from a shared :class:`Theme`. Pick a palette with
``--theme <name>`` on the command line, the ``ORION_THEME`` environment
variable, or ``ORION_THEME`` in ``.env``. You can also point either at a
custom JSON file that describes your own palette (any keys you omit fall
back to the "jarvis" palette).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .platform import data_dir

#: Every color role the UIs understand. Custom themes may override any subset.
THEME_ROLES = (
    "bg", "bg_panel", "bg_deep", "bg_edge", "grid",
    "fg", "accent", "accent_dim", "accent_faint", "accent_2",
    "text_dim", "text_faint", "ok", "warn", "error", "command", "muted",
)

THEMES: dict[str, dict[str, str]] = {
    # Iron-Man / JARVIS cyan deck (default for orion.gui)
    "jarvis": {
        "bg": "#04060c", "bg_panel": "#0a101d", "bg_deep": "#030109",
        "bg_edge": "#122238", "grid": "#122238",
        "fg": "#cfe2f3", "accent": "#35e0ff", "accent_dim": "#0e3a4a",
        "accent_faint": "#0e3a4a", "accent_2": "#4db2ff",
        "text_dim": "#cfe2f3", "text_faint": "#6f86a0",
        "ok": "#56f79a", "warn": "#ffc857", "error": "#ff5c5c",
        "command": "#ffb84d", "muted": "#6f86a0",
    },
    # Purple arc-reactor HUD (default for jarvis_hud.py)
    "orion": {
        "bg": "#060312", "bg_panel": "#0e0620", "bg_deep": "#030109",
        "bg_edge": "#0e0620", "grid": "#2a1245",
        "fg": "#c9b3f2", "accent": "#c06bff", "accent_dim": "#8b3aff",
        "accent_faint": "#4b2a7a", "accent_2": "#4db2ff",
        "text_dim": "#c9b3f2", "text_faint": "#7d66ad",
        "ok": "#56f79a", "warn": "#ffb84d", "error": "#ff5c5c",
        "command": "#c06bff", "muted": "#7d66ad",
    },
    # Matrix green phosphor on black
    "matrix": {
        "bg": "#000000", "bg_panel": "#001510", "bg_deep": "#000000",
        "bg_edge": "#003d2a", "grid": "#003d2a",
        "fg": "#b8ffd8", "accent": "#00ff66", "accent_dim": "#008844",
        "accent_faint": "#003d20", "accent_2": "#00ffaa",
        "text_dim": "#b8ffd8", "text_faint": "#2e7d46",
        "ok": "#00ff66", "warn": "#ffe066", "error": "#ff5c5c",
        "command": "#00ffaa", "muted": "#2e7d46",
    },
    # Solarized dark
    "solarized": {
        "bg": "#002b36", "bg_panel": "#073642", "bg_deep": "#00212b",
        "bg_edge": "#0a4a57", "grid": "#0a4a57",
        "fg": "#93a1a1", "accent": "#2aa198", "accent_dim": "#19767c",
        "accent_faint": "#0f4f55", "accent_2": "#268bd2",
        "text_dim": "#93a1a1", "text_faint": "#586e75",
        "ok": "#859900", "warn": "#b58900", "error": "#dc322f",
        "command": "#cb4b16", "muted": "#586e75",
    },
    # Nord
    "nord": {
        "bg": "#2e3440", "bg_panel": "#3b4252", "bg_deep": "#242933",
        "bg_edge": "#434c5e", "grid": "#434c5e",
        "fg": "#d8dee9", "accent": "#88c0d0", "accent_dim": "#5e81ac",
        "accent_faint": "#434c5e", "accent_2": "#81a1c1",
        "text_dim": "#d8dee9", "text_faint": "#4c566a",
        "ok": "#a3be8c", "warn": "#ebcb8b", "error": "#bf616a",
        "command": "#d08770", "muted": "#4c566a",
    },
    # Warm amber-on-black retro console
    "amber": {
        "bg": "#0a0a0a", "bg_panel": "#141414", "bg_deep": "#050505",
        "bg_edge": "#2a2a2a", "grid": "#2a2a2a",
        "fg": "#d8d8c0", "accent": "#ffaa2a", "accent_dim": "#7a4a00",
        "accent_faint": "#3a2400", "accent_2": "#ffcc66",
        "text_dim": "#d8d8c0", "text_faint": "#6e6e5c",
        "ok": "#7fd07f", "warn": "#ffb84d", "error": "#ff5c5c",
        "command": "#ffcc66", "muted": "#6e6e5c",
    },
}


class ThemeError(RuntimeError):
    """Raised when a theme cannot be resolved or parsed."""


@dataclass(frozen=True)
class Theme:
    """A resolved color palette. Defaults mirror the "jarvis" palette."""

    name: str = "jarvis"
    bg: str = "#04060c"
    bg_panel: str = "#0a101d"
    bg_deep: str = "#030109"
    bg_edge: str = "#122238"
    grid: str = "#122238"
    fg: str = "#cfe2f3"
    accent: str = "#35e0ff"
    accent_dim: str = "#0e3a4a"
    accent_faint: str = "#0e3a4a"
    accent_2: str = "#4db2ff"
    text_dim: str = "#cfe2f3"
    text_faint: str = "#6f86a0"
    ok: str = "#56f79a"
    warn: str = "#ffc857"
    error: str = "#ff5c5c"
    command: str = "#ffb84d"
    muted: str = "#6f86a0"
    extras: dict[str, str] = field(default_factory=dict)


def themes_list() -> list[str]:
    """Sorted names of the built-in palettes."""
    return sorted(THEMES)


def resolve_theme_name(
    cli: str | None = None,
    configured: str | None = None,
    default: str = "jarvis",
) -> str:
    """Pick the effective theme: CLI flag wins, then ``ORION_THEME`` env var,
    then the configured setting, then the per-UI ``default``."""
    for candidate in (cli, os.environ.get("ORION_THEME"), configured):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return default


def load_theme(name: str | None = None) -> Theme:
    """Resolve a theme by builtin name or by path to a custom JSON file."""
    name = (name or "").strip()
    if not name:
        return Theme()
    path = Path(name).expanduser()
    if path.is_file():
        return _load_custom_theme(path)
    if name in THEMES:
        return Theme(name=name, **THEMES[name])
    raise ThemeError(
        f"unknown theme {name!r}; available: {', '.join(themes_list())}"
    )


def _load_custom_theme(path: Path) -> Theme:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ThemeError(f"cannot read theme file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ThemeError(f"theme file {path} must contain a JSON object")

    base = str(data.get("base") or "jarvis")
    if base not in THEMES:
        raise ThemeError(f"theme file {path}: unknown base theme {base!r}")
    palette = dict(THEMES[base])
    for key in THEME_ROLES:
        if key in data and isinstance(data[key], (str, int)):
            palette[key] = str(data[key]).strip()
    extras = {
        str(k): str(v)
        for k, v in data.items()
        if k not in THEME_ROLES and k not in ("name", "base")
    }
    return Theme(
        name=str(data.get("name") or path.stem),
        extras=extras,
        **palette,
    )


# ---------------------------------------------------------------------------
# Persisted UI preferences (so a palette picked in the settings menu survives
# across launches).
# ---------------------------------------------------------------------------

def _ui_state_path() -> Path:
    return data_dir() / "ui.json"


def read_saved_theme() -> str:
    """Last palette picked in the GUI settings menu ("" if none)."""
    try:
        data = json.loads(_ui_state_path().read_text(encoding="utf-8"))
        name = data.get("theme")
        if isinstance(name, str) and name.strip():
            return name.strip()
    except (OSError, ValueError):
        pass
    return ""


def save_theme(name: str) -> None:
    """Persist the active palette for the next launch (best effort)."""
    try:
        path = _ui_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data["theme"] = name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass