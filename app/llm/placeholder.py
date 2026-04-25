"""Placeholder provider — used when no real LLM is configured.

When the system prompt looks like the ingestion prompt, this returns a stub SQL
block so the rest of the pipeline (frontend rendering, downstream parsing) can be
exercised without a real model. Otherwise it just echoes the input.
"""
from __future__ import annotations

import uuid

from app.llm.base import LLMProvider


def _is_ingest_prompt(system: str | None) -> bool:
    return bool(system and "ingestion engine" in system.lower())


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

        if _is_ingest_prompt(system):
            stub_event = f"NOTE-{uuid.uuid4().hex[:8]}"
            stub_fact = f"FACT-{uuid.uuid4().hex[:8]}"
            return (
                "-- [PLACEHOLDER] no real LLM configured; emitting stub INSERTs.\n"
                f"-- preview: {text_preview}\n"
                "INSERT INTO source_events (event_id, source_type, raw_content)\n"
                f"VALUES ('{stub_event}', 'unknown', '<{len(prompt)} chars omitted>');\n"
                "INSERT INTO facts (fact_id, property_id, entity_type, entity_id, "
                "category, statement, source_event_id, status)\n"
                f"VALUES ('{stub_fact}', 'LIE-001', 'property', 'LIE-001', 'note', "
                f"'placeholder fact extracted from {len(prompt)}-char document', "
                f"'{stub_event}', 'active');"
            )

        return (
            "[PLACEHOLDER LLM RESPONSE]\n"
            f"chars={len(prompt)} preview: {text_preview}\n"
            "Configure LLM_PROVIDER + LLM_API_KEY to swap this for a real model."
        )
