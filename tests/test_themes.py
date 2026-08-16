"""Unit tests for theme resolution and persistence."""

import json

import pytest

from orion.ui.themes import (
    Theme,
    ThemeError,
    load_theme,
    read_saved_theme,
    resolve_theme_name,
    save_theme,
    themes_list,
)


def test_default_theme_is_jarvis():
    theme = load_theme()
    assert theme.name == "jarvis"
    assert theme.bg == "#04060c"


def test_builtin_palettes_resolve():
    for name in themes_list():
        theme = load_theme(name)
        assert theme.name == name
        assert theme.accent  # non-empty accent


def test_custom_theme_overrides_subset(monkeypatch, tmp_path):
    path = tmp_path / "theme.json"
    path.write_text(json.dumps({"base": "orion", "name": "my-deck",
                                "accent": "#ff0000"}))
    theme = load_theme(str(path))
    assert theme.name == "my-deck"
    assert theme.accent == "#ff0000"
    # Inherited from the orion base palette.
    assert theme.bg == "#060312"


def test_unknown_theme_raises():
    with pytest.raises(ThemeError):
        load_theme("does-not-exist")


def test_bad_theme_file_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json")
    with pytest.raises(ThemeError):
        load_theme(str(path))


def test_resolve_theme_precedence():
    assert resolve_theme_name("cli", "cfg", default="d") == "cli"
    assert resolve_theme_name(None, "cfg", default="d") == "cfg"
    assert resolve_theme_name(None, None, default="d") == "d"


def test_saved_theme_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr("orion.ui.themes._ui_state_path", lambda: tmp_path / "ui.json")
    save_theme("matrix")
    assert read_saved_theme() == "matrix"


def test_saved_theme_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr("orion.ui.themes._ui_state_path", lambda: tmp_path / "ui.json")
    assert read_saved_theme() == ""


def test_theme_is_frozen_dataclass():
    theme = Theme()
    with pytest.raises(AttributeError):
        theme.bg = "#000000"  # type: ignore[misc]
