#!/usr/bin/env python3
"""
Batch-process property-management documents (PDFs + EMLs) via OpenAI Batch API.

Uses the same system/user prompt as /api/process-file so the output is identical
and suitable for both query answering and fine-tuning a smaller model.

Results are saved per-file and also in JSONL fine-tuning format.
Progress is checkpointed so a restart can resume from where it stopped.

Usage:
    python scripts/batch_process_openai.py \
        --api-key sk-proj-... \
        --model gpt-5.5 \
        --folders emails briefe rechnungen \
        --chunk-size 200 \
        --max-chars 50000

    # or set OPENAI_API_KEY env var and omit --api-key
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from app.routers.llm import (
    DocumentExtraction,
    _document_ingest_system_prompt,
    _document_ingest_user_prompt,
    _extract_json_text,
)

SUPPORTED_EXTS = {".eml", ".pdf", ".txt", ".md", ".json", ".xml", ".csv"}
POLL_INTERVAL_SECONDS = 30
MAX_POLL_MINUTES = 1440  # 24 h


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def extract_text(path: Path, max_chars: int) -> str:
    if path.suffix.lower() == ".pdf":
        pages = PdfReader(str(path)).pages
        text = "\n".join((page.extract_text() or "") for page in pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return text[:max_chars]


def iter_files(data_root: Path, folders: list[str]) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        root = data_root / folder
        if not root.exists():
            print(f"  [warn] folder not found: {root}", flush=True)
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in SUPPORTED_EXTS:
                files.append(path)
    return files


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------


def build_batch_request(custom_id: str, text: str, document_path: str, system_prompt: str) -> dict:
    user_prompt = _document_ingest_user_prompt(text, document_path)
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": "",  # filled in by caller
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_completion_tokens": 4096,
        },
    }


def write_batch_jsonl(requests: list[dict], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for req in requests:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")


def submit_batch(client: "openai.OpenAI", batch_file_path: Path) -> tuple[str, str]:
    """Upload file and create batch. Returns (batch_id, input_file_id)."""
    with batch_file_path.open("rb") as f:
        uploaded = client.files.create(file=f, purpose="batch")
    batch = client.batches.create(
        input_file_id=uploaded.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    return batch.id, uploaded.id


def poll_batch(client: "openai.OpenAI", batch_id: str) -> "openai.types.Batch":
    """Poll until batch is done (completed, failed, cancelled, expired)."""
    terminal = {"completed", "failed", "cancelled", "expired"}
    deadline = time.time() + MAX_POLL_MINUTES * 60
    while time.time() < deadline:
        batch = client.batches.retrieve(batch_id)
        status = batch.status
        counts = batch.request_counts
        total = counts.total if counts else "?"
        done = (counts.completed + counts.failed) if counts else "?"
        print(
            f"  [batch {batch_id[:12]}] status={status} "
            f"done={done}/{total}",
            flush=True,
        )
        if status in terminal:
            return batch
        time.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Batch {batch_id} did not finish within {MAX_POLL_MINUTES} minutes")


def summarize_batch_errors(batch: "openai.types.Batch") -> str:
    """Return a human-readable summary of validation-time errors on a failed batch."""
    errs = getattr(batch, "errors", None)
    if not errs:
        return ""
    data = getattr(errs, "data", None) or []
    lines: list[str] = []
    for item in data[:5]:
        code = getattr(item, "code", None) or ""
        msg = getattr(item, "message", None) or ""
        line_no = getattr(item, "line", None)
        prefix = f"line {line_no}: " if line_no is not None else ""
        lines.append(f"{prefix}[{code}] {msg}")
    if len(data) > 5:
        lines.append(f"... and {len(data) - 5} more")
    return "\n    ".join(lines)


def download_results(client: "openai.OpenAI", batch: "openai.types.Batch") -> list[dict]:
    """Download and parse output + error files into a flat list."""
    results: list[dict] = []

    if batch.output_file_id:
        raw = client.files.content(batch.output_file_id).text
        for line in raw.splitlines():
            line = line.strip()
            if line:
                results.append(json.loads(line))

    if batch.error_file_id:
        raw = client.files.content(batch.error_file_id).text
        for line in raw.splitlines():
            line = line.strip()
            if line:
                results.append(json.loads(line))

    return results


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def save_result(
    result_row: dict,
    metadata_by_id: dict[str, dict],
    *,
    out_dir: Path,
    system_prompt: str,
    records_path: Path,
    chat_path: Path,
    raw_io_path: Path,
    per_file_dir: Path,
    manifest: dict,
    manifest_path: Path,
) -> None:
    custom_id = result_row.get("custom_id", "")
    meta = metadata_by_id.get(custom_id)
    if meta is None:
        return

    rel_path = meta["rel_path"]
    user_prompt = meta["user_prompt"]
    text = meta["text"]

    error = result_row.get("error")
    response = result_row.get("response", {})
    status_code = response.get("status_code", 0) if response else 0
    body = response.get("body", {}) if response else {}

    if error or status_code >= 400:
        err_detail = error or body
        append_jsonl(
            out_dir / "errors.jsonl",
            {"document_path": rel_path, "custom_id": custom_id, "error": str(err_detail)},
        )
        manifest.setdefault("failed_files", []).append(rel_path)
        manifest["failed"] = manifest.get("failed", 0) + 1
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"  [error] {rel_path}: {str(err_detail)[:120]}", flush=True)
        return

    raw_model_output = ""
    if body and "choices" in body:
        raw_model_output = body["choices"][0]["message"]["content"]

    model_name = body.get("model", meta.get("model", "unknown"))

    json_text = _extract_json_text(raw_model_output)
    extraction: DocumentExtraction | None = None
    parse_error: str | None = None
    if json_text:
        try:
            extraction = DocumentExtraction.model_validate_json(json_text)
        except Exception as exc:
            parse_error = str(exc)

    # Per-file save (safe stem for filename)
    safe_stem = rel_path.replace("/", "__").replace("\\", "__")
    per_file_path = per_file_dir / f"{safe_stem}.json"
    per_file_path.write_text(
        json.dumps(
            {
                "document_path": rel_path,
                "model": model_name,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "raw_model_output": raw_model_output,
                "validated_output": extraction.model_dump(mode="json") if extraction else None,
                "parse_error": parse_error,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if extraction is None:
        append_jsonl(
            out_dir / "errors.jsonl",
            {
                "document_path": rel_path,
                "custom_id": custom_id,
                "error": f"parse_error: {parse_error}",
                "raw": raw_model_output[:500],
            },
        )
        manifest.setdefault("failed_files", []).append(rel_path)
        manifest["failed"] = manifest.get("failed", 0) + 1
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return

    assistant_json = extraction.model_dump_json(indent=2)

    record = {
        "document_path": rel_path,
        "handler": "llm-document-extract",
        "model": model_name,
        "text": text,
        "target": extraction.model_dump(mode="json"),
        "raw_model_output": raw_model_output,
    }
    append_jsonl(records_path, record)

    chat_record = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": assistant_json},
        ],
        "metadata": {"document_path": rel_path, "handler": "llm-document-extract", "model": model_name},
    }
    append_jsonl(chat_path, chat_record)

    append_jsonl(
        raw_io_path,
        {
            "document_path": rel_path,
            "handler": "llm-document-extract",
            "model": model_name,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "raw_model_output": raw_model_output,
            "validated_output": extraction.model_dump(mode="json"),
        },
    )

    manifest.setdefault("completed_files", []).append(rel_path)
    manifest["successful"] = manifest.get("successful", 0) + 1
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"  [ok] {rel_path} → {len(extraction.records)} records", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-process documents via OpenAI Batch API.")
    parser.add_argument("--api-key", default="", help="OpenAI API key (or set OPENAI_API_KEY)")
    parser.add_argument("--model", default="gpt-5.5", help="OpenAI model (default: gpt-5.5)")
    parser.add_argument("--data-root", type=Path, default=REPO_ROOT / "data" / "hackathon")
    parser.add_argument(
        "--folders",
        nargs="+",
        default=["briefe"],
        help="Subfolders under data-root to process.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "data" / "finetune_exports" / f"openai_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
    )
    parser.add_argument("--chunk-size", type=int, default=200, help="Requests per batch chunk (max 50000).")
    parser.add_argument("--max-chars", type=int, default=50_000, help="Max chars from each document.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N files total (0=all).")
    parser.add_argument("--resume", action="store_true", help="Skip files already in manifest.")
    parser.add_argument(
        "--inspect-batch",
        default="",
        help="Print status + validation errors for a batch_id, then exit. No new submissions.",
    )
    args = parser.parse_args()

    import os

    api_key = args.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("ERROR: provide --api-key or set OPENAI_API_KEY", file=sys.stderr)
        return 1

    try:
        import openai
    except ImportError:
        print("ERROR: openai package not installed. Run: pip install openai", file=sys.stderr)
        return 1

    client = openai.OpenAI(api_key=api_key)

    if args.inspect_batch:
        batch = client.batches.retrieve(args.inspect_batch)
        print(f"batch_id: {batch.id}")
        print(f"status:   {batch.status}")
        print(f"counts:   {batch.request_counts}")
        err_summary = summarize_batch_errors(batch)
        if err_summary:
            print(f"errors:\n    {err_summary}")
        if batch.error_file_id:
            raw = client.files.content(batch.error_file_id).text
            print("--- error_file (first 2000 chars) ---")
            print(raw[:2000])
        return 0

    data_root = args.data_root.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    per_file_dir = out_dir / "per_file"
    per_file_dir.mkdir(exist_ok=True)
    tmp_dir = out_dir / "tmp_batch_files"
    tmp_dir.mkdir(exist_ok=True)

    records_path = out_dir / "records.jsonl"
    chat_path = out_dir / "chat_finetune.jsonl"
    raw_io_path = out_dir / "raw_model_io.jsonl"
    manifest_path = out_dir / "manifest.json"

    # Load or init manifest
    seen: set[str] = set()
    if args.resume and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        seen = set(manifest.get("completed_files", []) + manifest.get("failed_files", []))
    else:
        manifest = {
            "created_at": datetime.now().isoformat(),
            "model": args.model,
            "data_root": str(data_root),
            "folders": args.folders,
            "completed_files": [],
            "failed_files": [],
            "successful": 0,
            "failed": 0,
            "batches": [],
        }

    all_files = iter_files(data_root, args.folders)
    if args.limit > 0:
        all_files = all_files[: args.limit]
    remaining = [p for p in all_files if str(p.relative_to(data_root)) not in seen]

    print(f"Found {len(all_files)} total files, {len(remaining)} to process → {out_dir}", flush=True)
    if not remaining:
        print("Nothing to do.", flush=True)
        return 0

    system_prompt = _document_ingest_system_prompt()

    # Split into chunks
    chunks = [remaining[i : i + args.chunk_size] for i in range(0, len(remaining), args.chunk_size)]
    print(f"Submitting {len(chunks)} batch chunk(s) of up to {args.chunk_size} files each.", flush=True)

    total_successful = manifest.get("successful", 0)
    total_failed = manifest.get("failed", 0)
    consecutive_batch_failures = 0
    MAX_CONSECUTIVE_FAILURES = 2

    for chunk_idx, chunk_files in enumerate(chunks, start=1):
        print(f"\n=== Chunk {chunk_idx}/{len(chunks)} ({len(chunk_files)} files) ===", flush=True)

        # Build requests and metadata
        requests: list[dict] = []
        metadata_by_id: dict[str, dict] = {}

        for path in chunk_files:
            rel_path = str(path.relative_to(data_root))
            try:
                text = extract_text(path, args.max_chars)
            except Exception as exc:
                print(f"  [skip] {rel_path}: text extraction failed: {exc}", flush=True)
                append_jsonl(
                    out_dir / "errors.jsonl",
                    {"document_path": rel_path, "error": f"text_extraction: {exc}"},
                )
                total_failed += 1
                continue

            user_prompt = _document_ingest_user_prompt(text, rel_path)
            custom_id = rel_path.replace("/", "__").replace("\\", "__")

            req = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": args.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_completion_tokens": 4096,
                },
            }
            requests.append(req)
            metadata_by_id[custom_id] = {
                "rel_path": rel_path,
                "user_prompt": user_prompt,
                "text": text,
                "model": args.model,
            }

        if not requests:
            continue

        # Write batch input file
        batch_file = tmp_dir / f"chunk_{chunk_idx:04d}.jsonl"
        write_batch_jsonl(requests, batch_file)
        print(f"  Written {len(requests)} requests to {batch_file.name}", flush=True)

        # Submit
        try:
            batch_id, input_file_id = submit_batch(client, batch_file)
            print(f"  Submitted → batch_id={batch_id}", flush=True)
        except Exception as exc:
            err = str(exc)
            print(f"  [ERROR] Batch submission failed: {err}", flush=True)
            # If it's a billing/credit error, stop entirely
            if "insufficient" in err.lower() or "billing" in err.lower() or "quota" in err.lower():
                print("  Budget exhausted – stopping.", flush=True)
                break
            # Otherwise log and continue with next chunk
            for path in chunk_files:
                rel_path = str(path.relative_to(data_root))
                append_jsonl(
                    out_dir / "errors.jsonl",
                    {"document_path": rel_path, "error": f"submission_failed: {err}"},
                )
                total_failed += 1
            continue

        manifest["batches"].append(
            {"chunk": chunk_idx, "batch_id": batch_id, "input_file_id": input_file_id, "status": "submitted"}
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Poll
        try:
            batch = poll_batch(client, batch_id)
        except TimeoutError as exc:
            print(f"  [TIMEOUT] {exc}", flush=True)
            manifest["batches"][-1]["status"] = "timeout"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            continue

        manifest["batches"][-1]["status"] = batch.status
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        if batch.status == "failed":
            err_summary = summarize_batch_errors(batch)
            if err_summary:
                print(f"  [batch failed] validation errors:\n    {err_summary}", flush=True)
                manifest["batches"][-1]["errors"] = err_summary
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                low = err_summary.lower()
                if "token_limit_exceeded" in low or "enqueued token limit" in low:
                    print(
                        "  Enqueued-token limit hit for this model/org. "
                        "Re-run with a much smaller --chunk-size (e.g. 20–40) "
                        "or switch --model to one with a higher batch quota.",
                        flush=True,
                    )
                    break
                fatal_markers = (
                    "model_not_found",
                    "does not exist",
                    "no access",
                    "not allowed",
                    "invalid_request_error",
                    "model `",
                    "model '",
                )
                if any(m in low for m in fatal_markers):
                    print(
                        "  Fatal validation error (model/permission). Stopping before more chunks fail.",
                        flush=True,
                    )
                    break
            consecutive_batch_failures += 1
            if consecutive_batch_failures >= MAX_CONSECUTIVE_FAILURES:
                print(
                    f"  [STOP] {consecutive_batch_failures} consecutive batch failures — "
                    "fix the underlying error before retrying.",
                    flush=True,
                )
                break
            continue

        if batch.status != "completed":
            print(f"  [WARN] Batch ended with status={batch.status}", flush=True)
            if batch.status == "expired":
                print("  Batch expired – likely insufficient credits. Stopping.", flush=True)
                break
            consecutive_batch_failures += 1
            if consecutive_batch_failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"  [STOP] {consecutive_batch_failures} consecutive non-completions.", flush=True)
                break
            continue

        consecutive_batch_failures = 0

        # Download results
        results = download_results(client, batch)
        print(f"  Downloaded {len(results)} result rows", flush=True)

        # Parse and save each result immediately
        for result_row in results:
            save_result(
                result_row,
                metadata_by_id,
                out_dir=out_dir,
                system_prompt=system_prompt,
                records_path=records_path,
                chat_path=chat_path,
                raw_io_path=raw_io_path,
                per_file_dir=per_file_dir,
                manifest=manifest,
                manifest_path=manifest_path,
            )

        total_successful = manifest.get("successful", 0)
        total_failed = manifest.get("failed", 0)
        print(
            f"  Chunk {chunk_idx} done. cumulative: {total_successful} ok, {total_failed} failed",
            flush=True,
        )

    print(
        f"\nAll done. successful={manifest.get('successful', 0)} "
        f"failed={manifest.get('failed', 0)} "
        f"records={records_path} "
        f"chat={chat_path} "
        f"raw_io={raw_io_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
