"""LLM backend abstraction: local Ollama and cloud APIs via LiteLLM.

Both providers expose the same ``chat`` interface so the rest of the app is
provider-agnostic. ``get_provider`` resolves the configured ``ORION_PROVIDER``:

* ``ollama`` — always the local server at ``ollama_base_url``
* ``api``    — always the cloud provider (OpenAI/Anthropic via LiteLLM)
* ``auto``   — ping Ollama; use it if reachable, otherwise fall back to the API
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path

import httpx

from ..core.config import AppSettings

# Model-name markers that indicate an image-capable (multimodal) model.
_VISION_MARKERS = (
    "llava", "bakllava", "minicpm", "moondream", "qwen2.5-vl", "qwen2-vl",
    "yi-vl", "llama3.2-vision", "llama3.2v", "gemini", "gpt-4o", "gpt-4.1",
    "gpt-5", "claude", "-vl", "vision", "vlm205", "mmproj",
)


def _is_vision_model(name: str) -> bool:
    lowered = (name or "").lower()
    return any(marker in lowered for marker in _VISION_MARKERS)


def _image_data_uri(path: str) -> str:
    """Return a base64 data URI for an image file (used by the API provider)."""
    p = Path(path).expanduser()
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    mime = mimetypes.guess_type(str(p))[0] or "image/png"
    return f"data:{mime};base64,{data}"


def _image_b64(path: str) -> str:
    """Return raw base64 bytes for an image file (used by Ollama)."""
    return base64.b64encode(Path(path).expanduser().read_bytes()).decode("ascii")


def _build_ollama_messages(messages: list[dict]) -> list[dict]:
    """Convert orion messages (optionally with an ``image`` file path)
    into the Ollama wire format."""
    built: list[dict] = []
    for msg in messages:
        m = {"role": msg["role"], "content": msg.get("content", "")}
        image = msg.get("image")
        if image:
            try:
                m["images"] = [_image_b64(image)]
            except OSError as exc:
                m["content"] += (f"\n[screenshot attached: {image} — "
                                 f"could not embed ({exc})]")
        built.append(m)
    return built


def _build_litellm_messages(messages: list[dict]) -> list[dict]:
    """Convert orion messages (optionally with an ``image`` path) into the
    OpenAI/Anthropic content-part format LiteLLM expects."""
    built: list[dict] = []
    for msg in messages:
        image = msg.get("image")
        if not image:
            built.append({"role": msg["role"], "content": msg.get("content", "")})
            continue
        parts = [{"type": "text", "text": msg.get("content", "") or ""}]
        try:
            parts.append({"type": "image_url",
                          "image_url": {"url": _image_data_uri(image)}})
        except OSError as exc:
            parts[0]["text"] += f"\n[screenshot attached: {image} — could not embed ({exc})]"
        built.append({"role": msg["role"], "content": parts})
    return built


class LLMError(RuntimeError):
    """Raised when a model request fails."""


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.1,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self._client = httpx.Client(timeout=httpx.Timeout(timeout))

    @property
    def supports_vision(self) -> bool:
        return _is_vision_model(self.model)

    def ping(self) -> bool:
        try:
            resp = self._client.get(f"{self.base_url}/api/tags")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
        model: str | None = None,
    ) -> str:
        payload = {
            "model": model or self.model,
            "messages": _build_ollama_messages(self._with_system(messages,
                                                                 system)),
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": temperature if temperature is not None else self.temperature},
        }
        try:
            resp = self._client.post(f"{self.base_url}/api/chat", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMError(f"ollama request failed: {exc}") from exc
        content = resp.json().get("message", {}).get("content", "").strip()
        if not content:
            raise LLMError("ollama returned an empty response")
        return content

    def set_model(self, model: str) -> None:
        self.model = model

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _with_system(messages: list[dict], system: str | None) -> list[dict]:
        if not system:
            return list(messages)
        return [{"role": "system", "content": system}, *messages]


class LiteLLMProvider:
    name = "litellm"

    def __init__(
        self,
        model: str,
        api_key: str = "",
        api_base: str | None = None,
        temperature: float = 0.1,
        timeout: int = 120,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self.temperature = temperature
        self.timeout = timeout

    def ping(self) -> bool:
        return bool(self.api_key)

    @property
    def supports_vision(self) -> bool:
        return True

    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
        model: str | None = None,
    ) -> str:
        try:
            import litellm  # noqa: PLC0415 - lazily imported to keep core deps light
        except ImportError as exc:
            raise LLMError("litellm is required for API providers: pip install litellm") from exc
        kwargs = {
            "model": model or self.model,
            "messages": _build_litellm_messages(self._with_system(messages,
                                                                  system)),
            "temperature": temperature if temperature is not None else self.temperature,
            "timeout": timeout or self.timeout,
        }
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        try:
            resp = litellm.completion(**kwargs)
        except Exception as exc:
            raise LLMError(f"api request failed: {exc}") from exc
        try:
            return resp.choices[0].message.content.strip()
        except (IndexError, AttributeError) as exc:
            raise LLMError("api returned an unparsable response") from exc

    def set_model(self, model: str) -> None:
        self.model = model

    def close(self) -> None:
        pass

    @staticmethod
    def _with_system(messages: list[dict], system: str | None) -> list[dict]:
        if not system:
            return list(messages)
        return [{"role": "system", "content": system}, *messages]


def _litellm_model(settings: AppSettings) -> str:
    """Build the LiteLLM model name.

    ``ORION_API_MODEL`` may already carry a provider prefix (``openai/...``,
    ``anthropic/...``); if not, qualify it with ``ORION_API_PROVIDER`` so the
    setting is honoured instead of being dead config.
    """
    model = (settings.api_model or "").strip()
    if model and "/" not in model and settings.api_provider:
        return f"{settings.api_provider}/{model}"
    return model


def get_provider(settings: AppSettings) -> OllamaProvider | LiteLLMProvider:
    """Resolve the configured provider, with local-first auto-detection."""
    if settings.provider in ("auto", "ollama"):
        ollama = OllamaProvider(
            settings.ollama_base_url,
            settings.ollama_model,
            settings.temperature,
            settings.llm_timeout,
        )
        if settings.provider == "ollama":
            return ollama
        if ollama.ping():
            return ollama
        ollama.close()
        if settings.provider == "auto" and settings.api_key_value:
            return LiteLLMProvider(
                _litellm_model(settings),
                settings.api_key_value,
                settings.api_base_url,
                settings.temperature,
                settings.llm_timeout,
            )
        raise LLMError(
            "provider=auto: Ollama is unreachable and no ORION_API_KEY is set.\n"
            "Start the Ollama server (ollama serve) or configure a cloud API in .env."
        )

    return LiteLLMProvider(
        _litellm_model(settings),
        settings.api_key_value,
        settings.api_base_url,
        settings.temperature,
        settings.llm_timeout,
    )
