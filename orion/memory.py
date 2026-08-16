"""Permanent memory for Orion — survives restarts.

Notes the user (or the model) asks Orion to remember are stored as a JSON
file in the XDG data directory and re-injected into the system prompt every
session, so the assistant keeps long-term context.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .platform import data_dir


class Memory:
    """A minimal, thread-safe persistent note store."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            path = data_dir() / "memory.json"
        self.path = path
        self._lock = threading.Lock()
        self._notes: list[dict] = []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._notes = [n for n in data.get("notes", [])
                           if isinstance(n, dict) and n.get("text")]
        except (OSError, ValueError):
            self._notes = []

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps({"notes": self._notes}, indent=2,
                           ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass

    def remember(self, text: str) -> int:
        text = " ".join(text.split())
        if not text:
            return -1
        with self._lock:
            nid = max((n["id"] for n in self._notes), default=0) + 1
            self._notes.append({"id": nid, "text": text})
            self.save()
        return nid

    def forget(self, note_id: int) -> bool:
        with self._lock:
            before = len(self._notes)
            self._notes = [n for n in self._notes if n["id"] != note_id]
            changed = len(self._notes) != before
            if changed:
                self.save()
        return changed

    def clear(self) -> None:
        with self._lock:
            self._notes = []
            self.save()

    def items(self) -> list[dict]:
        with self._lock:
            return [dict(n) for n in self._notes]

    def render(self) -> str:
        lines = []
        for n in self.items():
            lines.append("  - [%d] %s" % (n["id"], n["text"]))
        return "\n".join(lines)

    def to_prompt(self) -> str:
        notes = self.render()
        if not notes:
            return ""
        return (
            "== Permanent memory (survives restarts) ==\n"
            "Notes below were recorded across sessions and stay valid. Follow "
            "them unless the user overrides. Recognise: the user can add notes "
            "with /remember, view with /memory, and remove with /forget <id>.\n"
            + notes
        )

    def __len__(self) -> int:
        return len(self._notes)