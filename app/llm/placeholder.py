"""Placeholder provider — used when no real LLM is configured."""
from __future__ import annotations

import uuid

from app.llm.base import LLMProvider


def _is_sql_prompt(system: str | None) -> bool:
    return bool(
        system
        and "translate natural-language requests into sqlite sql" in system.lower()
    )


def _is_document_prompt(system: str | None) -> bool:
    return bool(
        system
        and "extract structured records from one german property-management document" in system.lower()
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


def _placeholder_document_sql(prompt: str) -> str:
    prompt_lower = prompt.lower()
    if "rechnung" in prompt_lower or "invoice" in prompt_lower:
        return (
            '{'
            '"summary":"placeholder invoice extraction",'
            '"records":['
            '{"table":"invoices","record":{"invoice_id":"INV-' + uuid.uuid4().hex[:8] + '","property_id":"LIE-001","provider_company":"PLACEHOLDER PROVIDER","invoice_date":"2026-01-01","recipient":"placeholder"}},'
            '{"table":"source_events","record":{"event_id":"NOTE-' + uuid.uuid4().hex[:8] + '","source_type":"pdf_invoice","property_id":"LIE-001","raw_content":"placeholder document"}}'
            "]}"
        )

    return (
        '{'
        '"summary":"placeholder communication extraction",'
        '"records":['
        '{"table":"source_events","record":{"event_id":"NOTE-' + uuid.uuid4().hex[:8] + '","source_type":"note","property_id":"LIE-001","raw_content":"placeholder document"}},'
        '{"table":"facts","record":{"fact_id":"FACT-' + uuid.uuid4().hex[:8] + '","property_id":"LIE-001","entity_type":"property","entity_id":"LIE-001","category":"note","statement":"placeholder extracted fact","status":"active"}}'
        "]}"
    )


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

        if _is_document_prompt(system):
            return _placeholder_document_sql(prompt)

        return (
            "[PLACEHOLDER LLM RESPONSE]\n"
            f"chars={len(prompt)} preview: {text_preview}\n"
            "Configure LLM_PROVIDER + LLM_API_KEY to swap this for a real model."
        )
