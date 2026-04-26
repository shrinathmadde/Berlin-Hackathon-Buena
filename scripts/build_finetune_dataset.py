from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader
from sqlmodel import SQLModel, Session

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(REPO_ROOT / ".env")

from app.llm import LLMError, get_llm_provider, reset_provider_cache
from app.database import DATABASE_URL, engine
from app.routers.llm import (
    DocumentExtraction,
    TABLE_TO_MODEL,
    _csv_extraction,
    _document_ingest_system_prompt,
    _document_ingest_user_prompt,
    _extract_json_text,
    _prepare_record,
    _primary_key_name,
)

SUPPORTED_EXTS = {".eml", ".pdf", ".txt", ".md", ".json", ".xml", ".csv"}
TABLE_WRITE_ORDER = {
    "properties": 0,
    "owners": 1,
    "service_providers": 2,
    "buildings": 3,
    "source_events": 4,
    "units": 5,
    "tenants": 6,
    "bank_transactions": 7,
    "invoices": 8,
    "facts": 9,
}


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def iter_files(base_dir: Path, folders: list[str]) -> list[Path]:
    files: list[Path] = []
    for folder in folders:
        root = base_dir / folder
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in SUPPORTED_EXTS:
                files.append(path)
    return files


def build_target(text: str, rel_path: str) -> tuple[str, DocumentExtraction, str]:
    if rel_path.lower().endswith(".csv"):
        extraction = _csv_extraction(text, rel_path)
        raw_json = extraction.model_dump_json(indent=2)
        return "local-csv-loader", extraction, raw_json

    provider = get_llm_provider()
    raw_json = provider.complete(
        _document_ingest_user_prompt(text, rel_path),
        system=_document_ingest_system_prompt(),
        temperature=0,
    )
    json_text = _extract_json_text(raw_json)
    if not json_text:
        raise ValueError("LLM returned empty JSON")
    extraction = DocumentExtraction.model_validate_json(json_text)
    return provider.model_name, extraction, raw_json


def append_jsonl(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def reset_sqlite_database() -> None:
    if not DATABASE_URL.startswith("sqlite"):
        raise RuntimeError(f"--reset-db only supports sqlite DATABASE_URL, got {DATABASE_URL}")
    from app import models  # noqa: F401  ensure SQLModel metadata is registered

    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def iter_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    records: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def apply_records_to_db(records_path: Path, *, reset_db: bool, out_dir: Path) -> dict[str, object]:
    if reset_db:
        reset_sqlite_database()

    records = iter_jsonl(records_path)
    operations: list[dict[str, object]] = []
    for record in records:
        target = record.get("target")
        if not isinstance(target, dict):
            continue
        target_records = target.get("records")
        if not isinstance(target_records, list):
            continue
        for index, operation in enumerate(target_records):
            if not isinstance(operation, dict):
                continue
            table = operation.get("table")
            if not isinstance(table, str):
                continue
            operations.append(
                {
                    "sort_key": TABLE_WRITE_ORDER.get(table, 999),
                    "document_path": record.get("document_path"),
                    "text": record.get("text"),
                    "operation_index": index,
                    "operation": operation,
                }
            )

    operations.sort(key=lambda item: (item["sort_key"], str(item["document_path"]), int(item["operation_index"])))

    writes: list[dict[str, str]] = []
    errors: list[dict[str, object]] = []
    by_table: dict[str, int] = {}
    with Session(engine) as session:
        for item in operations:
            operation = item["operation"]
            assert isinstance(operation, dict)
            table = operation.get("table")
            record = operation.get("record")
            rel_path = item.get("document_path")
            text = item.get("text")
            if not isinstance(table, str) or not isinstance(record, dict):
                continue
            if table not in TABLE_TO_MODEL:
                errors.append(
                    {
                        "document_path": rel_path,
                        "table": table,
                        "error": "unsupported table",
                    }
                )
                continue

            try:
                model = TABLE_TO_MODEL[table]
                prepared = _prepare_record(
                    table,
                    record,
                    document_path=str(rel_path) if rel_path is not None else None,
                    document_text=str(text) if text is not None and not str(rel_path).lower().endswith(".csv") else None,
                )
                obj = model.model_validate(prepared)
                pk_name = _primary_key_name(model)
                pk_value = getattr(obj, pk_name, None)
                if not pk_value:
                    raise ValueError(f"Missing primary key for {table}")

                existing = session.get(model, pk_value)
                if existing is None:
                    session.add(obj)
                    status = "created"
                else:
                    for key, value in obj.model_dump(exclude_unset=False).items():
                        if key != pk_name:
                            setattr(existing, key, value)
                    session.add(existing)
                    status = "updated"
                session.commit()

                write = {"table": table, "primary_key": str(pk_value), "status": status}
                writes.append(write)
                by_table[table] = by_table.get(table, 0) + 1
            except Exception as e:  # noqa: BLE001
                session.rollback()
                errors.append(
                    {
                        "document_path": rel_path,
                        "table": table,
                        "operation": operation,
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

    summary = {
        "database_url": DATABASE_URL,
        "reset_db": reset_db,
        "records_path": str(records_path),
        "operations_seen": len(operations),
        "writes": len(writes),
        "errors": len(errors),
        "by_table": by_table,
    }
    (out_dir / "db_apply_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    db_writes_path = out_dir / "db_writes.jsonl"
    db_errors_path = out_dir / "db_errors.jsonl"
    for write in writes:
        append_jsonl(db_writes_path, write)
    for error in errors:
        append_jsonl(db_errors_path, error)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a fine-tuning dataset with file text plus JSON extraction targets."
    )
    parser.add_argument("--data-root", type=Path, default=REPO_ROOT / "data" / "hackathon")
    parser.add_argument(
        "--folders",
        nargs="+",
        default=["briefe", "emails", "rechnungen"],
        help="Top-level dataset folders to process.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "data" / "finetune_exports" / datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    parser.add_argument("--limit", type=int, default=0, help="Stop after N files (0 = all).")
    parser.add_argument("--max-chars", type=int, default=50_000, help="Maximum extracted chars sent to the model.")
    parser.add_argument("--batch-size", type=int, default=25, help="Write a batch checkpoint every N files.")
    parser.add_argument("--resume", action="store_true", help="Skip files already present in the manifest.")
    parser.add_argument(
        "--populate-db",
        action="store_true",
        help="After all model processing is done, replay validated extractions into the configured database.",
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Drop and recreate the local SQLite schema before replaying validated extractions. Use with --populate-db.",
    )
    args = parser.parse_args()

    reset_provider_cache()
    if args.reset_db and not args.populate_db:
        parser.error("--reset-db requires --populate-db")

    data_root = args.data_root.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    records_path = out_dir / "records.jsonl"
    chat_path = out_dir / "chat_finetune.jsonl"
    raw_io_path = out_dir / "raw_model_io.jsonl"
    manifest_path = out_dir / "manifest.json"
    errors_path = out_dir / "errors.jsonl"

    seen: set[str] = set()
    if args.resume and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        seen = set(manifest.get("completed_files", []))
        manifest.setdefault("batches_completed", 0)
        manifest.setdefault(
            "database",
            {
                "url": DATABASE_URL,
                "populate_db": args.populate_db,
                "reset_db": args.reset_db,
                "apply_timing": "after_model_processing",
            },
        )
    else:
        manifest = {
            "created_at": datetime.now().isoformat(),
            "data_root": str(data_root),
            "folders": args.folders,
            "completed_files": [],
            "failed_files": [],
            "successful": 0,
            "failed": 0,
            "batches_completed": 0,
            "database": {
                "url": DATABASE_URL,
                "populate_db": args.populate_db,
                "reset_db": args.reset_db,
                "apply_timing": "after_model_processing",
            },
        }

    all_files = iter_files(data_root, args.folders)
    if args.limit > 0:
        all_files = all_files[: args.limit]

    remaining = [path for path in all_files if str(path.relative_to(data_root)) not in seen]
    print(f"processing {len(remaining)} files into {out_dir}", flush=True)

    system_prompt = _document_ingest_system_prompt()
    for index, path in enumerate(remaining, start=1):
        rel_path = str(path.relative_to(data_root))
        print(f"[{index}/{len(remaining)}] {rel_path}", flush=True)
        try:
            text = extract_text(path)
            model_text = text[: args.max_chars]
            user_prompt = _document_ingest_user_prompt(model_text, rel_path)
            model_name, extraction, raw_model_output = build_target(model_text, rel_path)
            assistant_json = extraction.model_dump_json(indent=2)

            record = {
                "document_path": rel_path,
                "handler": "local-csv-loader" if rel_path.lower().endswith(".csv") else "llm-document-extract",
                "model": model_name,
                "text": model_text,
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
                "metadata": {
                    "document_path": rel_path,
                    "handler": record["handler"],
                    "model": model_name,
                },
            }
            append_jsonl(chat_path, chat_record)
            append_jsonl(
                raw_io_path,
                {
                    "document_path": rel_path,
                    "handler": record["handler"],
                    "model": model_name,
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                    "raw_model_output": raw_model_output,
                    "validated_output": extraction.model_dump(mode="json"),
                },
            )

            manifest["completed_files"].append(rel_path)
            manifest["successful"] += 1
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if args.batch_size > 0 and index % args.batch_size == 0:
                manifest["batches_completed"] = index // args.batch_size
                manifest["last_batch_completed_at"] = datetime.now().isoformat()
                manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except (LLMError, ValueError, Exception) as e:  # noqa: BLE001
            append_jsonl(
                errors_path,
                {
                    "document_path": rel_path,
                    "error": f"{type(e).__name__}: {e}",
                },
            )
            manifest["failed_files"].append(rel_path)
            manifest["failed"] += 1
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if args.populate_db:
        print("model processing complete; replaying validated records into database", flush=True)
        manifest["database"]["applied_at"] = datetime.now().isoformat()
        manifest["database"]["apply_summary"] = apply_records_to_db(
            records_path,
            reset_db=args.reset_db,
            out_dir=out_dir,
        )
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(
        f"done. successful={manifest['successful']} failed={manifest['failed']} records={records_path} chat={chat_path} raw_io={raw_io_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
