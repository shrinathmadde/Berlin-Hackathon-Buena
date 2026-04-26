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

    def complete_messages(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        # Default: flatten to a single prompt so providers without native chat
        # support still work. OpenAI-compatible providers override for fidelity.
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        body = "\n\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in messages
            if m["role"] != "system"
        )
        return self.complete(body, system=system, max_tokens=max_tokens, temperature=temperature)
