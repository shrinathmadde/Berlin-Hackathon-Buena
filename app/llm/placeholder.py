"""Placeholder provider — used when no real LLM is configured."""
from __future__ import annotations

from app.llm.base import LLMProvider


def _is_sql_prompt(system: str | None) -> bool:
    return bool(
        system
        and "translate natural-language requests into sqlite sql" in system.lower()
    )


def _placeholder_sql(prompt: str) -> str:
    prompt_lower = prompt.lower()
    table_names = [
        ("invoice", "invoices"),
        ("tenant", "tenants"),
        ("owner", "owners"),
        ("provider", "service_providers"),
        ("transaction", "bank_transactions"),
        ("building", "buildings"),
        ("unit", "units"),
        ("event", "source_events"),
        ("fact", "facts"),
        ("property", "properties"),
    ]

    if "table" in prompt_lower or "schema" in prompt_lower:
        return "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;"

    for keyword, table_name in table_names:
        if keyword in prompt_lower or table_name in prompt_lower:
            if "count" in prompt_lower or "how many" in prompt_lower:
                return f"SELECT COUNT(*) AS count FROM {table_name};"
            return f"SELECT * FROM {table_name} LIMIT 10;"

    return "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name;"


class PlaceholderProvider(LLMProvider):
    @property
    def model_name(self) -> str:
        return "placeholder-v0"

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> str:
        text_preview = prompt.strip().replace("\n", " ")[:120]
        if len(prompt) > 120:
            text_preview += "…"

        if _is_sql_prompt(system):
            return _placeholder_sql(prompt)

        return (
            "[PLACEHOLDER LLM RESPONSE]\n"
            f"chars={len(prompt)} preview: {text_preview}\n"
            "Configure LLM_PROVIDER + LLM_API_KEY to swap this for a real model."
        )
