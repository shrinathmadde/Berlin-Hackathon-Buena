"""Provider-agnostic LLM interface.

Every concrete provider (placeholder, OpenAI-compatible, Anthropic, …) implements
this same surface so call sites never need to know which one is wired up.
Configuration is read from environment variables — see app/llm/factory.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(RuntimeError):
    """Raised by a provider when the upstream call fails."""


class LLMProvider(ABC):
    """One synchronous text-completion call. Keep the surface tiny on purpose —
    everything the routers need can be expressed as `complete(prompt, system=…)`.
    """

    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @property
    def provider_name(self) -> str:
        return type(self).__name__

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str: ...
