"""LLM endpoint — thin shim over the provider abstraction in app.llm.

Both /api/llm and /api/scan use the SAME ingest prompt so the model treats inputs
the same way regardless of whether they came from a file or a textarea.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.llm import LLMError, get_llm_provider
from app.llm.prompts import build_ingest_prompt

router = APIRouter(prefix="/api", tags=["llm"])


class LLMRequest(BaseModel):
    text: str = Field(..., description="Document text or free-text note to ingest")
    mode: str = Field(
        "ingest",
        description="ingest = run through the ingestion prompt (default); raw = pass through unchanged",
    )


class LLMResponse(BaseModel):
    response: str
    model: str
    mode: str


@router.post("/llm", response_model=LLMResponse)
def call_llm(payload: LLMRequest) -> LLMResponse:
    provider = get_llm_provider()
    try:
        if payload.mode == "ingest":
            system, user = build_ingest_prompt(payload.text)
            result = provider.complete(user, system=system)
        elif payload.mode == "raw":
            result = provider.complete(payload.text)
        else:
            raise HTTPException(400, f"Unknown mode: {payload.mode}")
    except LLMError as e:
        raise HTTPException(502, f"LLM call failed: {e}")
    return LLMResponse(response=result, model=provider.model_name, mode=payload.mode)
