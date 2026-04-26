"""Anthropic Messages API provider."""
from __future__ import annotations

import httpx

from app.llm.base import LLMError, LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 100.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
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
        body: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._base_url}/v1/messages",
                    headers={
                        "x-api-key": self._api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=body,
                )
            if r.status_code >= 400:
                raise LLMError(f"{r.status_code}: {r.text[:500]}")
            data = r.json()
            return "".join(
                block.get("text", "")
                for block in data.get("content", [])
                if block.get("type") == "text"
            )
        except httpx.TimeoutException as e:
            raise LLMError(f"{self._model} timed out after {self._timeout:g}s") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{self._model} transport error: {type(e).__name__}: {e}") from e
        except (KeyError, ValueError) as e:
            raise LLMError(f"unexpected response shape: {e}") from e
