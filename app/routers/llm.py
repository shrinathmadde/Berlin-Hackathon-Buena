"""LLM endpoint — placeholder implementation.

Matches the contract expected by the Lovable frontend:
    POST /api/llm   body: {"text": "..."}   -> {"response": "...", "model": "..."}

Real LLM integration will replace `_placeholder_response` later.
"""
from pydantic import BaseModel, Field
from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["llm"])


class LLMRequest(BaseModel):
    text: str = Field(..., description="Raw text to send to the LLM")


class LLMResponse(BaseModel):
    response: str
    model: str


def _placeholder_response(text: str) -> str:
    char_count = len(text)
    word_count = len(text.split())
    preview = text.strip().replace("\n", " ")[:120]
    if len(text.strip()) > 120:
        preview += "…"
    return (
        f"[PLACEHOLDER LLM RESPONSE]\n"
        f"Received {char_count} chars / {word_count} words.\n"
        f"Preview: {preview}\n"
        f"Real LLM integration is wired in — this stub will be swapped for the "
        f"upstream model call without changing the API contract."
    )


@router.post("/llm", response_model=LLMResponse)
def call_llm(payload: LLMRequest) -> LLMResponse:
    return LLMResponse(
        response=_placeholder_response(payload.text),
        model="placeholder-v0",
    )
