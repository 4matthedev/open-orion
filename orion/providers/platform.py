"""Cross-platform helpers: OS detection, data directories, shell launcher.

Open Orion runs on Linux (default) and Windows. Everything platform-specific
funnels through this module so the rest of the codebase stays neutral:

* ``is_windows`` / ``is_linux`` — platform detection
* ``shell_name`` / ``shell_args`` — the shell used to run model commands
  (``powershell.exe`` on Windows, ``bash`` everywhere else)
* ``data_dir`` and friends — where app data (memory, themes, screenshots,
  voice models) is stored: ``%LOCALAPPDATA%\\open-orion`` on Windows,
  ``~/.local/share/open-orion`` on Linux
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_windows() -> bool:
    """True when running on native Windows (not WSL)."""
    return sys.platform == "win32" or os.name == "nt"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def shell_name() -> str:
    """The interactive shell the LLM's generated commands run in."""
    return "powershell" if is_windows() else "bash"


def shell_args(command: str) -> list[str]:
    """Build the ``subprocess`` argv that runs ``command`` in the right shell."""
    if is_windows():
        return [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ]
    return ["bash", "-c", command]


def syntax_check_args(command: str) -> list[str] | None:
    """Argv for a syntax-only pre-check (``bash -n``), or None on Windows.

    PowerShell has no cheap parse-only mode, so on Windows the pre-flight
    syntax gate is skipped and errors surface when the command runs.
    """
    if is_windows():
        return None
    return ["bash", "-n", "-c", command]


def data_dir() -> Path:
    """Base directory for Open Orion's persistent data (memory, ui.json, …)."""
    if is_windows():
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(base) / "open-orion"
    return Path.home() / ".local" / "share" / "open-orion"


def screenshots_dir() -> Path:
    return data_dir() / "screenshots"


def kokoro_dir() -> Path:
    return data_dir() / "kokoro"


def piper_voices_dir() -> Path:
    return data_dir() / "piper_voices"


__all__ = [
    "data_dir",
    "is_linux",
    "is_windows",
    "kokoro_dir",
    "piper_voices_dir",
    "screenshots_dir",
    "shell_args",
    "shell_name",
    "syntax_check_args",
]
