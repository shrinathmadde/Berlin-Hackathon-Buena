"""Provider factory — reads env vars, returns the right LLMProvider.

Environment variables:
    LLM_PROVIDER   placeholder | openai | pioneer | anthropic   (default: placeholder)
    LLM_API_KEY    required for openai/anthropic
    LLM_BASE_URL   override the OpenAI-compatible endpoint
                   (default: https://api.openai.com/v1)
                   common alternates:
                     - https://openrouter.ai/api/v1
                     - https://api.groq.com/openai/v1
                     - https://api.together.xyz/v1
                     - http://localhost:11434/v1   (ollama)
    LLM_MODEL      model id (default depends on provider)

Comparison-specific variables:
    GPT_LLM_API_KEY    optional override for the GPT-5.5 provider
    GPT_LLM_BASE_URL   default: https://api.openai.com/v1
    GPT_LLM_MODEL      default: gpt-5.5
    QWEN_LLM_API_KEY   optional override for the Qwen/Pioneer provider
    QWEN_LLM_BASE_URL  default: https://api.pioneer.ai/v1
    QWEN_LLM_MODEL     default: eaf2d9b9-04b9-411f-a7cd-7e202c4270cc
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


def _openai_compatible_provider_from_env(
    *,
    label: str,
    key_names: tuple[str, ...],
    base_url_name: str,
    default_base_url: str,
    model_name: str,
    default_model: str,
) -> OpenAICompatibleProvider:
    api_key = next((_env(name) for name in key_names if _env(name)), None)
    if not api_key:
        raise RuntimeError(f"{key_names[0]} is required for the {label} provider")

    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=_env(base_url_name, default_base_url) or default_base_url,
        model=_env(model_name, default_model) or default_model,
    )


@lru_cache(maxsize=1)
def get_gpt_provider() -> LLMProvider:
    return _openai_compatible_provider_from_env(
        label="GPT-5.5",
        key_names=("GPT_LLM_API_KEY", "OPENAI_API_KEY"),
        base_url_name="GPT_LLM_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        model_name="GPT_LLM_MODEL",
        default_model="gpt-5.5",
    )


@lru_cache(maxsize=1)
def get_qwen_provider() -> LLMProvider:
    return _openai_compatible_provider_from_env(
        label="Qwen",
        key_names=("QWEN_LLM_API_KEY", "PIONEER_API_KEY"),
        base_url_name="QWEN_LLM_BASE_URL",
        default_base_url="https://api.pioneer.ai/v1",
        model_name="QWEN_LLM_MODEL",
        default_model="eaf2d9b9-04b9-411f-a7cd-7e202c4270cc",
    )


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    provider = (_env("LLM_PROVIDER", "placeholder") or "placeholder").lower()

    if provider == "placeholder":
        return PlaceholderProvider()

    if provider in {"openai", "openai_compat", "openrouter", "groq", "together", "ollama"}:
        api_key = _env("LLM_API_KEY")
        base_url = _env("LLM_BASE_URL", "https://api.openai.com/v1")
        model = _env("LLM_MODEL", "gpt-5.5")
        # Ollama runs locally and ignores the key, so we don't fail if it's empty.
        if not api_key and provider != "ollama":
            raise RuntimeError(
                f"LLM_API_KEY is required when LLM_PROVIDER={provider!r}"
            )
        return OpenAICompatibleProvider(
            api_key=api_key or "ollama",
            base_url=base_url or "https://api.openai.com/v1",
            model=model or "gpt-5.5",
        )

    if provider == "pioneer":
        api_key = _env("LLM_API_KEY")
        base_url = _env("LLM_BASE_URL", "https://api.pioneer.ai/v1")
        model = _env("LLM_MODEL", "eaf2d9b9-04b9-411f-a7cd-7e202c4270cc")
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER='pioneer'")
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or "https://api.pioneer.ai/v1",
            model=model or "eaf2d9b9-04b9-411f-a7cd-7e202c4270cc",
        )

    if provider == "gemini":
        # Google's Gemini API exposes an OpenAI-compatible endpoint, so we reuse
        # the same provider — only the base URL and default model change.
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL", "gemini-2.5-flash-lite")
        base_url = _env(
            "LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
        )
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER='gemini'")
        return OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or "https://generativelanguage.googleapis.com/v1beta/openai",
            model=model or "gemini-2.5-flash-lite",
        )

    if provider == "anthropic":
        api_key = _env("LLM_API_KEY")
        model = _env("LLM_MODEL", "claude-sonnet-4-6")
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER='anthropic'")
        return AnthropicProvider(api_key=api_key, model=model or "claude-sonnet-4-6")

    raise RuntimeError(
        f"Unknown LLM_PROVIDER: {provider!r}. "
        "Use one of: placeholder, openai, pioneer, anthropic, openrouter, groq, together, ollama."
    )


def reset_provider_cache() -> None:
    """Clear the cached provider — call this if env vars change at runtime."""
    get_llm_provider.cache_clear()
    get_gpt_provider.cache_clear()
    get_qwen_provider.cache_clear()
