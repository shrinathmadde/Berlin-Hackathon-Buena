from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.routers.llm import (
    DocumentExtraction,
    _csv_extraction,
    _document_ingest_system_prompt,
    _document_ingest_user_prompt,
)

SUPPORTED_EXTS = {".eml", ".pdf", ".txt", ".md", ".json", ".xml", ".csv"}
def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def iter_files(base_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(base_dir.rglob("*")):
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in SUPPORTED_EXTS:
            files.append(path)
    return files


def codex_prompt(rel_path: str, text: str) -> str:
    return (
        f"{_document_ingest_system_prompt()}\n\n"
        "Return only valid JSON matching the provided output schema.\n"
        "Do not explain your reasoning.\n"
        f"{_document_ingest_user_prompt(text, rel_path)}"
    )


def run_codex_extraction(rel_path: str, text: str) -> tuple[str, DocumentExtraction]:
    prompt = codex_prompt(rel_path, text)
    with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as out_file:
        output_path = Path(out_file.name)

    cmd = [
        "codex",
        "-a",
        "never",
        "-s",
        "read-only",
        "exec",
        "-C",
        str(REPO_ROOT),
        "--skip-git-repo-check",
        "-o",
        str(output_path),
        prompt,
    ]
    try:
        subprocess.run(cmd, check=True, cwd=REPO_ROOT)
        content = output_path.read_text(encoding="utf-8").strip()
    finally:
        output_path.unlink(missing_ok=True)

    extraction = DocumentExtraction.model_validate_json(content)
    return "codex", extraction


def append_jsonl(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a fine-tuning dataset using local CSV extraction and Codex for non-CSV files."
    )
    parser.add_argument("--data-root", type=Path, default=REPO_ROOT / "data")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=REPO_ROOT / "data" / "finetune_exports" / datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-chars", type=int, default=50_000)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    records_path = out_dir / "records.jsonl"
    chat_path = out_dir / "chat_finetune.jsonl"
    manifest_path = out_dir / "manifest.json"
    errors_path = out_dir / "errors.jsonl"

    seen: set[str] = set()
    if args.resume and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        seen = set(manifest.get("completed_files", []))
    else:
        manifest = {
            "created_at": datetime.now().isoformat(),
            "data_root": str(data_root),
            "completed_files": [],
            "failed_files": [],
            "successful": 0,
            "failed": 0,
        }

    all_files = iter_files(data_root)
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
            model_text = text if path.suffix.lower() == ".csv" else text[: args.max_chars]
            if path.suffix.lower() == ".csv":
                model_name = "local-csv-loader"
                extraction = _csv_extraction(model_text, rel_path)
                handler = "local-csv-loader"
            else:
                model_name, extraction = run_codex_extraction(rel_path, model_text)
                handler = "codex-document-extract"

            record = {
                "document_path": rel_path,
                "handler": handler,
                "model": model_name,
                "text": model_text,
                "target": extraction.model_dump(mode="json"),
            }
            append_jsonl(records_path, record)

            chat_record = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": _document_ingest_user_prompt(model_text, rel_path)},
                    {"role": "assistant", "content": extraction.model_dump_json(indent=2)},
                ],
                "metadata": {
                    "document_path": rel_path,
                    "handler": handler,
                    "model": model_name,
                },
            }
            append_jsonl(chat_path, chat_record)

            manifest["completed_files"].append(rel_path)
            manifest["successful"] += 1
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            append_jsonl(
                errors_path,
                {"document_path": rel_path, "error": f"{type(e).__name__}: {e}"},
            )
            manifest["failed_files"].append(rel_path)
            manifest["failed"] += 1
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(
        f"done. successful={manifest['successful']} failed={manifest['failed']} records={records_path} chat={chat_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
