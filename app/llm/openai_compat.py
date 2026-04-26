"""OpenAI-compatible chat-completions provider.

Works for any endpoint that speaks the OpenAI Chat Completions schema:
OpenAI, OpenRouter, Groq, Together, Anyscale, vLLM, LM Studio, Ollama
(/v1/chat/completions), etc. Configure via LLM_BASE_URL.
"""
from __future__ import annotations

import time

import httpx

from app.llm.base import LLMError, LLMProvider

_MAX_RETRIES = 8
_BACKOFF_BASE = 2.0
_BACKOFF_CAP = 120.0


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    @property
    def model_name(self) -> str:
        return self._model

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self._model,
            "messages": messages,
        }
        if self._model.startswith("gpt-5"):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["max_tokens"] = max_tokens
            payload["temperature"] = temperature
        last_error: LLMError | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    r = client.post(
                        f"{self._base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", 0))
                    wait = max(retry_after, min(_BACKOFF_CAP, _BACKOFF_BASE ** attempt))
                    last_error = LLMError(f"{r.status_code}: {r.text[:500]}")
                    if attempt < _MAX_RETRIES:
                        time.sleep(wait)
                        continue
                    raise last_error
                if r.status_code >= 400:
                    raise LLMError(f"{r.status_code}: {r.text[:500]}")
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPError as e:
                raise LLMError(f"transport: {type(e).__name__}: {e}") from e
            except (KeyError, IndexError, ValueError) as e:
                raise LLMError(f"unexpected response shape: {e}") from e
        raise last_error  # type: ignore[misc]
