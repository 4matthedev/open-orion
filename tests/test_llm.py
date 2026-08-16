"""Unit tests for LLM message shaping and provider/model resolution."""

import base64

from orion.core.config import AppSettings
from orion.providers.llm import (
    OllamaProvider,
    _build_litellm_messages,
    _build_ollama_messages,
    _is_vision_model,
    _litellm_model,
)


def test_vision_model_detection():
    assert _is_vision_model("llama3.2-vision") is True
    assert _is_vision_model("qwen2.5-vl:7b") is True
    assert _is_vision_model("qwen3.5:9b") is False
    assert _is_vision_model("") is False


def test_ollama_messages_plain(tmp_path):
    out = _build_ollama_messages([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ])
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]


def test_ollama_messages_with_image(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG fake")
    out = _build_ollama_messages([
        {"role": "user", "content": "look", "image": str(img)},
    ])
    assert out[0]["images"][0] == base64.b64encode(b"\x89PNG fake").decode("ascii")


def test_ollama_messages_missing_image(tmp_path):
    out = _build_ollama_messages([
        {"role": "user", "content": "look", "image": str(tmp_path / "nope.png")},
    ])
    assert "could not embed" in out[0]["content"]


def test_litellm_messages_plain():
    out = _build_litellm_messages([{"role": "user", "content": "hi"}])
    assert out == [{"role": "user", "content": "hi"}]


def test_litellm_messages_with_image(tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG fake")
    out = _build_litellm_messages([
        {"role": "user", "content": "look", "image": str(img)},
    ])
    parts = out[0]["content"]
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_litellm_model_qualification():
    settings = AppSettings(api_provider="anthropic", api_model="claude-sonnet-4-5")
    assert _litellm_model(settings) == "anthropic/claude-sonnet-4-5"


def test_litellm_model_keeps_explicit_prefix():
    settings = AppSettings(api_provider="openai", api_model="openai/gpt-4o-mini")
    assert _litellm_model(settings) == "openai/gpt-4o-mini"


def test_auto_select_prefers_configured_model():
    p = OllamaProvider("http://localhost:11434", "qwen3.5:4b")
    p.list_models = lambda: ["llama3.1:8b", "qwen3.5:4b"]  # type: ignore[method-assign]
    assert p.auto_select_model("qwen3.5:4b") == "qwen3.5:4b"
    assert p.model == "qwen3.5:4b"


def test_auto_select_falls_back_to_installed():
    p = OllamaProvider("http://localhost:11434", "qwen3.5:4b")
    p.list_models = lambda: ["llama3.1:8b", "mistral:7b"]  # type: ignore[method-assign]
    chosen = p.auto_select_model("qwen3.5:4b")
    assert chosen == "llama3.1:8b"
    assert p.model == "llama3.1:8b"


def test_auto_select_prefers_qwen_family():
    p = OllamaProvider("http://localhost:11434", "")
    p.list_models = lambda: ["mistral:7b", "qwen2.5:7b", "tinyllama:1.1b"]  # type: ignore[method-assign]
    chosen = p.auto_select_model(None)
    assert chosen == "qwen2.5:7b"


def test_auto_select_prefers_biggest_variant_in_family():
    p = OllamaProvider("http://localhost:11434", "")
    p.list_models = lambda: ["qwen3.5:0.8b", "qwen3.5:9b", "qwen3.5:4b"]  # type: ignore[method-assign]
    assert p.auto_select_model(None) == "qwen3.5:9b"


def test_auto_select_empty_installed_returns_none():
    p = OllamaProvider("http://localhost:11434", "")
    p.list_models = lambda: []  # type: ignore[method-assign]
    assert p.auto_select_model(None) is None
