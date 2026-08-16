"""Unit tests for settings loading / validation."""

import pytest
from pydantic import ValidationError

from orion.core.config import AppSettings


def test_defaults():
    settings = AppSettings()
    assert settings.provider == "auto"
    assert settings.safety_level == "confirm"
    assert settings.max_output_chars == 8000


def test_env_prefix_overrides(monkeypatch):
    monkeypatch.setenv("ORION_OLLAMA_MODEL", "llama3.1:8b")
    settings = AppSettings()
    assert settings.ollama_model == "llama3.1:8b"


def test_api_provider_requires_key():
    with pytest.raises(ValidationError):
        AppSettings(provider="api", api_key="")


def test_api_provider_accepts_key():
    settings = AppSettings(provider="api", api_key="sk-test")
    assert settings.api_key_value == "sk-test"


def test_safety_level_validated():
    with pytest.raises(ValidationError):
        AppSettings(safety_level="nonsense")


def test_unknown_env_vars_ignored(monkeypatch):
    monkeypatch.setenv("ORION_TOTALLY_UNKNOWN", "x")
    AppSettings()  # should not raise
