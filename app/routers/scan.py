"""Server-side data folder scan.

The frontend pushes a single button -> we walk DATA_DIR, diff against the last-seen
mtimes stored in STATE_FILE, extract text from any new/changed file, and route each
through the LLM placeholder. Returns per-file timings so the UI can render them.

First scan with no prior state is treated as a baseline: we record every file's
mtime but don't process anything (otherwise pressing the button would fan out
thousands of LLM calls on the seeded archive).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.llm import LLMError, get_llm_provider
from app.llm.prompts import build_ingest_prompt

router = APIRouter(prefix="/api/scan", tags=["scan"])

DATA_DIR = Path(os.getenv("CONTEXT_DATA_DIR", "data")).resolve()
STATE_FILE = Path(os.getenv("CONTEXT_SCAN_STATE", "scan_state.json")).resolve()

TEXT_EXTS = {".txt", ".csv", ".json", ".md", ".xml", ".eml"}
PDF_EXT = ".pdf"
MAX_CHARS = 50_000


class ScanResultItem(BaseModel):
    path: str
    size_bytes: int
    extraction_ms: float
    llm_ms: float
    llm_response: Optional[str] = None
    error: Optional[str] = None


class ScanResponse(BaseModel):
    total_ms: float
    data_dir: str
    files_seen: int
    new_or_changed: int
    processed: int
    baselined: bool
    model: str
    results: list[ScanResultItem]


def _load_state() -> dict[str, int]:
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text())
        return {str(k): int(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_state(state: dict[str, int]) -> None:
    STATE_FILE.write_text(json.dumps(state))


def _walk(root: Path) -> list[Path]:
    return [
        p for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    ]


def _extract_text(path: Path) -> tuple[str, Optional[str]]:
    """Return (text_truncated, error)."""
    ext = path.suffix.lower()
    try:
        if ext == PDF_EXT:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            chunks: list[str] = []
            for page in reader.pages:
                chunks.append(page.extract_text() or "")
                if sum(len(c) for c in chunks) > MAX_CHARS:
                    break
            text = "\n".join(chunks)
        elif ext in TEXT_EXTS:
            text = path.read_text(encoding="utf-8", errors="replace")
        else:
            return "", f"unsupported extension {ext or '(none)'}"
        return text[:MAX_CHARS], None
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"


def _display_path(p: Path) -> str:
    try:
        return str(p.relative_to(DATA_DIR.parent))
    except ValueError:
        return str(p)


@router.post("", response_model=ScanResponse)
def scan_data_folder(
    limit: int = Query(50, ge=1, le=1000, description="Max changed files to process per scan"),
    force: bool = Query(False, description="Process all changed files even on first scan"),
) -> ScanResponse:
    if not DATA_DIR.exists():
        raise HTTPException(404, f"Data folder not found at {DATA_DIR}")

    overall_start = time.perf_counter()
    provider = get_llm_provider()
    prior = _load_state()
    is_first_scan = len(prior) == 0

    files = _walk(DATA_DIR)
    new_state: dict[str, int] = {}
    changed: list[Path] = []
    for f in files:
        key = str(f.resolve())
        mtime_ns = f.stat().st_mtime_ns
        new_state[key] = mtime_ns
        if prior.get(key) != mtime_ns:
            changed.append(f)

    if is_first_scan and not force:
        _save_state(new_state)
        return ScanResponse(
            total_ms=(time.perf_counter() - overall_start) * 1000,
            data_dir=str(DATA_DIR),
            files_seen=len(files),
            new_or_changed=len(changed),
            processed=0,
            baselined=True,
            model=provider.model_name,
            results=[],
        )

    results: list[ScanResultItem] = []
    for f in changed[:limit]:
        size = f.stat().st_size
        ext_start = time.perf_counter()
        text, err = _extract_text(f)
        extraction_ms = (time.perf_counter() - ext_start) * 1000

        if err:
            results.append(ScanResultItem(
                path=_display_path(f),
                size_bytes=size,
                extraction_ms=extraction_ms,
                llm_ms=0.0,
                error=err,
            ))
            continue

        system, user = build_ingest_prompt(text)
        llm_start = time.perf_counter()
        try:
            llm_text = provider.complete(user, system=system)
            llm_err: Optional[str] = None
        except LLMError as e:
            llm_text = None
            llm_err = f"LLM error: {e}"
        llm_ms = (time.perf_counter() - llm_start) * 1000

        results.append(ScanResultItem(
            path=_display_path(f),
            size_bytes=size,
            extraction_ms=extraction_ms,
            llm_ms=llm_ms,
            llm_response=llm_text,
            error=llm_err,
        ))

    _save_state(new_state)
    return ScanResponse(
        total_ms=(time.perf_counter() - overall_start) * 1000,
        data_dir=str(DATA_DIR),
        files_seen=len(files),
        new_or_changed=len(changed),
        processed=len(results),
        baselined=False,
        model=provider.model_name,
        results=results,
    )


@router.delete("/state", status_code=204)
def reset_scan_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    return None
