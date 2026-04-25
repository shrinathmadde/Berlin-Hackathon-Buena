"""Provider factory — reads env vars, returns the right LLMProvider.

Environment variables:
    LLM_PROVIDER   placeholder | openai | anthropic   (default: placeholder)
    LLM_API_KEY    required for openai/anthropic
    LLM_BASE_URL   override the OpenAI-compatible endpoint
                   (default: https://api.openai.com/v1)
                   common alternates:
                     - https://openrouter.ai/api/v1
                     - https://api.groq.com/openai/v1
                     - https://api.together.xyz/v1
                     - http://localhost:11434/v1   (ollama)
    LLM_MODEL      model id (default depends on provider)
"""
from __future__ import annotations

import os
from functools import lru_cache

from app.llm.anthropic import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.openai_compat import OpenAICompatibleProvider
from app.llm.placeholder import PlaceholderProvider


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    provider = (_env("LLM_PROVIDER", "placeholder") or "placeholder").lower()

    if provider == "placeholder":
        return PlaceholderProvider()

    if provider in {"openai", "openai_compat", "openrouter", "groq", "together", "ollama"}:
        api_key = _env("LLM_API_KEY")
        base_url = _env("LLM_BASE_URL", "https://api.openai.com/v1")
        model = _env("LLM_MODEL", "gpt-4o-mini")
        # Ollama runs locally and ignores the key, so we don't fail if it's empty.
        if not api_key and provider != "ollama":
            raise RuntimeError(
                f"LLM_API_KEY is required when LLM_PROVIDER={provider!r}"
            )
        return OpenAICompatibleProvider(
            api_key=api_key or "ollama",
            base_url=base_url or "https://api.openai.com/v1",
            model=model or "gpt-4o-mini",
        )

    if provider == "gemini":
        # Google's Gemini API exposes an OpenAI-compatible endpoint, so we reuse
        # the same provider — only the base URL and default model change.
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL", "gemini-2.0-flash")
        base_url = _env(
            "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
        )
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER='gemini'")
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or "https://generativelanguage.googleapis.com/v1beta/openai",
            model=model or "gemini-2.0-flash",
        )

    if provider == "anthropic":
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL", "claude-sonnet-4-6")
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER='anthropic'")
        return AnthropicProvider(api_key=api_key, model=model or "claude-sonnet-4-6")

    raise RuntimeError(
        f"Unknown LLM_PROVIDER: {provider!r}. "
        "Use one of: placeholder, openai, anthropic, openrouter, groq, together, ollama."
    )


def reset_provider_cache() -> None:
    """Clear the cached provider — call this if env vars change at runtime."""
    get_llm_provider.cache_clear()
