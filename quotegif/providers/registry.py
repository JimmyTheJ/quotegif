from __future__ import annotations

from quotegif.config import AppConfig
from quotegif.providers.base import Provider


def get_provider(config: AppConfig, override: str | None = None) -> Provider:
    """Return the configured provider instance, optionally overridden by name."""
    name = override or config.provider.name

    if name == "openai":
        from quotegif.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(config.provider.openai)

    if name == "anthropic":
        from quotegif.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(config.provider.anthropic)

    if name == "ollama":
        from quotegif.providers.ollama_provider import OllamaProvider
        return OllamaProvider(config.provider.ollama)

    raise ValueError(
        f"Unknown provider '{name}'. Choose one of: openai, anthropic, ollama"
    )
