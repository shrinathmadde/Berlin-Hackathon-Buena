"""Evaluation harness for the /api/sql endpoint.

Randomly selects one supported file from each folder, extracts the text, sends
that text through the SQL endpoint, and writes the input/output pair to
`test/results/<timestamp>/` for review.

Run from the repo root:

    .venv/bin/python test/test_api.py
    .venv/bin/python test/test_api.py --seed 7
    .venv/bin/python test/test_api.py --base http://localhost:8000
    .venv/bin/python test/test_api.py --data data/hackathon/incremental
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL = ""
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_OUT_DIR = REPO_ROOT / "test" / "results"

TEXT_EXTS = {".txt", ".csv", ".json", ".md", ".xml", ".eml"}
PDF_EXT = ".pdf"
SUPPORTED = TEXT_EXTS | {PDF_EXT}
MAX_CHARS = 50_000


def random_supported_file(folder: Path, rng: random.Random) -> Path | None:
    candidates = sorted(
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in SUPPORTED
    )
    if not candidates:
        return None
    return rng.choice(candidates)


def sample_one_per_folder(root: Path, rng: random.Random) -> list[Path]:
    picks: list[Path] = []
    if root.is_dir():
        root_pick = random_supported_file(root, rng)
        if root_pick is not None:
            picks.append(root_pick)
    for folder in sorted(path for path in root.rglob("*") if path.is_dir() and not path.name.startswith(".")):
        pick = random_supported_file(folder, rng)
        if pick is not None:
            picks.append(pick)
    return picks


def extract_text(path: Path) -> tuple[str, str | None]:
    ext = path.suffix.lower()
    try:
        if ext == PDF_EXT:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
        return text[:MAX_CHARS], None
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"


def safe_filename(rel_path: Path) -> str:
    return str(rel_path).replace("/", "__").replace("\\", "__")


def build_question(rel_path: Path, text: str) -> str:
    return (
        "Read the following document and generate the exact SQL statement that should be "
        "executed to store the most important structured information from it in the database. "
        "Return executable SQL only.\n\n"
        f"Document path: {rel_path}\n"
        "Document text:\n"
        f"\"\"\"\n{text}\n\"\"\""
    )


def _post_with_http(base_url: str, question: str, timeout: float) -> tuple[int, str]:
    with httpx.Client(timeout=timeout) as client:
        client.get(f"{base_url}/health").raise_for_status()
        response = client.post(f"{base_url}/api/sql", json={"question": question})
        return response.status_code, response.text


def _post_in_process(question: str) -> tuple[int, str]:
    from app.main import app

    with TestClient(app) as client:
        response = client.post("/api/sql", json={"question": question})
        return response.status_code, response.text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Randomly sample one file per folder, send each through /api/sql, and save results."
    )
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE_URL,
        help="Backend base URL. Leave empty to run against the local FastAPI app in-process.",
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR, help="Data root to walk")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Where to write results")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N sampled files (0 = no cap)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible sampling")
    parser.add_argument(
        "--max-chars",
        type=int,
        default=MAX_CHARS,
        help="Maximum characters of extracted text to send per file",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout (seconds)")
    args = parser.parse_args()

    data_dir = args.data.resolve()
    if not data_dir.exists():
        print(f"data dir not found: {data_dir}", file=sys.stderr)
        return 1

    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 10**9)
    rng = random.Random(seed)
    files = sample_one_per_folder(data_dir, rng)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print("no supported files found", file=sys.stderr)
        return 1

    run_dir = args.out / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"selected {len(files)} files with seed {seed}; writing to {run_dir}")

    manifest: list[dict[str, object]] = []
    ok = 0
    for index, fpath in enumerate(files, start=1):
        rel = fpath.relative_to(data_dir)
        print(f"[{index}/{len(files)}] {rel}", flush=True)

        text, extraction_error = extract_text(fpath)
        text = text[: args.max_chars]
        if extraction_error:
            manifest.append({"path": str(rel), "error": f"extraction: {extraction_error}"})
            (run_dir / f"{safe_filename(rel)}.error.txt").write_text(extraction_error)
            continue

        question = build_question(rel, text)
        t0 = time.perf_counter()
        try:
            if args.base:
                status_code, body_text = _post_with_http(args.base, question, args.timeout)
            else:
                status_code, body_text = _post_in_process(question)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
        except Exception as e:  # noqa: BLE001
            err_msg = f"{type(e).__name__}: {e}"
            manifest.append({"path": str(rel), "error": f"request: {err_msg}"})
            (run_dir / f"{safe_filename(rel)}.error.txt").write_text(err_msg)
            continue

        record: dict[str, object] = {
            "path": str(rel),
            "size_bytes": fpath.stat().st_size,
            "chars_sent": len(text),
            "latency_ms": elapsed_ms,
            "status_code": status_code,
        }

        parsed_body: object
        try:
            parsed_body = json.loads(body_text)
        except json.JSONDecodeError:
            parsed_body = body_text

        if status_code >= 400:
            record["error"] = parsed_body
            (run_dir / f"{safe_filename(rel)}.error.txt").write_text(
                f"=== source : {rel}\n"
                f"=== status : {status_code}\n"
                f"=== latency: {elapsed_ms:.2f} ms\n\n"
                f"--- QUESTION ---\n{question}\n\n"
                f"--- INPUT TEXT ---\n{text}\n\n"
                f"--- ERROR ---\n{json.dumps(parsed_body, indent=2) if not isinstance(parsed_body, str) else parsed_body}\n"
            )
            manifest.append(record)
            continue

        ok += 1
        record["response"] = parsed_body
        if isinstance(parsed_body, dict):
            record["model"] = parsed_body.get("model", "?")
            record["sql"] = parsed_body.get("sql", "")
            record["row_count"] = parsed_body.get("row_count", 0)
        manifest.append(record)

        pretty_body = json.dumps(parsed_body, indent=2) if not isinstance(parsed_body, str) else parsed_body
        (run_dir / f"{safe_filename(rel)}.txt").write_text(
            f"=== source : {rel}\n"
            f"=== status : {status_code}\n"
            f"=== latency: {elapsed_ms:.2f} ms\n\n"
            f"--- QUESTION ---\n{question}\n\n"
            f"--- INPUT TEXT ---\n{text}\n\n"
            f"--- API OUTPUT ---\n{pretty_body}\n"
        )

    summary = {
        "ran_at": datetime.now().isoformat(),
        "base_url": args.base or "in-process",
        "data_dir": str(data_dir),
        "seed": seed,
        "total_files": len(files),
        "successful": ok,
        "items": manifest,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    print(f"\nDone. {ok}/{len(files)} successful. Summary: {run_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
