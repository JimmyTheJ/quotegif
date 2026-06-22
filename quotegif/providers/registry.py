from __future__ import annotations

from quotegif.config import AppConfig
from quotegif.providers.base import Provider

KNOWN_PROVIDERS = ("openai", "anthropic", "ollama")


def get_provider(
    config: AppConfig,
    override: str | None = None,
    model_override: str | None = None,
) -> Provider:
    """Return the configured provider instance, optionally overriding name and/or model."""
    name = override or config.provider.name

    if name == "openai":
        from quotegif.providers.openai_provider import OpenAIProvider
        return OpenAIProvider(config.provider.openai, model_override=model_override)

    if name == "anthropic":
        from quotegif.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(config.provider.anthropic, model_override=model_override)

    if name == "ollama":
        from quotegif.providers.ollama_provider import OllamaProvider
        return OllamaProvider(config.provider.ollama, model_override=model_override)

    raise ValueError(
        f"Unknown provider '{name}'. Choose one of: {', '.join(KNOWN_PROVIDERS)}"
    )


def get_active_model(config: AppConfig, provider_name: str, model_override: str | None = None) -> str:
    """Return the model name that will actually be used for the given provider."""
    if model_override:
        return model_override
    if provider_name == "openai":
        return config.provider.openai.model
    if provider_name == "anthropic":
        return config.provider.anthropic.model
    if provider_name == "ollama":
        return config.provider.ollama.model
    return "unknown"
