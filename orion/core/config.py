"""Central configuration for Open Orion.

All settings are loaded from environment variables with the ``ORION_`` prefix
and/or a local ``.env`` file, using pydantic-settings for validation.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORION_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- LLM provider -------------------------------------------------
    provider: Literal["auto", "ollama", "api"] = "auto"
    ollama_base_url: str = "http://localhost:11434"
    # Empty = auto-pick the best model installed in Ollama (zero-config).
    ollama_model: str = ""

    api_provider: Literal["openai", "anthropic"] = "openai"
    api_model: str = "openai/gpt-4o-mini"
    api_key: SecretStr = SecretStr("")
    api_base_url: str | None = None

    temperature: float = 0.1
    llm_timeout: int = 120

    # --- Safety -----------------------------------------------------------
    # strict     = confirm before EVERY command
    # confirm    = confirm risky/destructive commands (default)
    # permissive = auto-run risky commands (forbidden hard-blocks still enforced)
    # auto       = fully autonomous, no prompts (forbidden hard-blocks still enforced)
    safety_level: Literal["strict", "confirm", "permissive", "auto"] = "confirm"
    # When True, force a confirmation prompt before every command regardless
    # of level (overrides safety_level=auto/permissive).
    always_confirm: bool = False

    # --- Execution limits -----------------------------------------------
    dry_run: bool = False
    max_command_length: int = 4096
    shell_timeout: int = 120
    max_output_chars: int = 8000

    # --- Theme (GUI / HUD palette) ---------------------------------------
    # Builtin names: jarvis, orion, matrix, solarized, nord, amber.
    # May also be a path to a custom JSON theme file.
    theme: str = ""

    # --- Vision -----------------------------------------------------------
    # When True, screenshots are attached as real images to vision-capable
    # models (e.g. llama3.2-vision, qwen2.5-vl). Text-only models see the
    # captured file path instead.
    vision: bool = True
    # Separate model used to describe screenshots (e.g. "qwen2.5-vl:7b").
    # Empty = use the provider's main model.
    vision_model: str = ""

    # --- Conversation context -----------------------------------------
    max_history_turns: int = 20
    max_context_chars: int = 12000

    # --- Voice (talk mode) ----------------------------------------------
    voice_enabled: bool = False
    stt_model: str = "small.en"
    stt_device: str = "default"
    # Minimum absolute RMS threshold (int16 sample amplitude) before a frame
    # counts as speech. Lower it for quiet mics, raise it if the environment
    # is noisy. The real threshold is max(noise_floor * 2.5, this value).
    stt_vad_floor: float = 10.0
    tts_model: str = ""
    tts_engine: Literal["piper", "kokoro", "xtts"] = "piper"
    tts_reference: str = ""
    tts_language: str = "en"
    tts_device: str = "cpu"
    tts_length_scale: float = 1.3
    tts_kokoro_model: str = ""
    tts_kokoro_voices: str = ""
    tts_kokoro_voice: str = "am_michael"
    tts_kokoro_speed: float = 0.95
    voice_timeout: int = 12

    working_dir: str | None = None

    @model_validator(mode="after")
    def _require_api_key_when_explicit(self) -> AppSettings:
        if self.provider == "api" and not self.api_key.get_secret_value():
            raise ValueError(
                "provider='api' requires ORION_API_KEY (set it in .env or the environment)"
            )
        return self

    @property
    def api_key_value(self) -> str:
        return self.api_key.get_secret_value()


@lru_cache
def get_settings() -> AppSettings:
    """Return a cached AppSettings instance (safe to call repeatedly)."""
    return AppSettings()
