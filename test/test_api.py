"""Validation harness for the /api/llm endpoint.

Walks the data folder, picks the FIRST supported file (alphabetical) inside each
sub-folder, sends every pick through `/api/llm`, and dumps each response into
test/results/<timestamp>/ so we can eyeball how well the model handles each
document type (emails, PDFs, CSVs, JSON, …).

Run from the repo root:

    .venv/bin/python test/test_api.py                       # all picks
    .venv/bin/python test/test_api.py --limit 10            # first 10 picks only
    .venv/bin/python test/test_api.py --base http://...     # remote backend
    .venv/bin/python test/test_api.py --data data/hackathon/incremental
    .venv/bin/python test/test_api.py --mode raw            # bypass the ingest prompt

The summary lives at `test/results/<timestamp>/summary.json`; per-file responses
sit alongside it as `<flattened_path>.txt`. This is intentionally not a pytest
test — it's a model-evaluation script you can re-run after swapping providers.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_DATA_DIR = REPO_ROOT / "data"
DEFAULT_OUT_DIR = REPO_ROOT / "test" / "results"

TEXT_EXTS = {".txt", ".csv", ".json", ".md", ".xml", ".eml"}
PDF_EXT = ".pdf"
SUPPORTED = TEXT_EXTS | {PDF_EXT}
MAX_CHARS = 50_000


def first_supported_file(folder: Path) -> Path | None:
    candidates = sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith(".")
        and f.suffix.lower() in SUPPORTED
    )
    return candidates[0] if candidates else None


def find_top_one_per_folder(root: Path) -> list[Path]:
    """One representative file per sub-folder, plus the root itself.

    A folder counts only if it directly contains supported files — pure parent
    directories with subfolders only are skipped (their children get picked).
    """
    picks: list[Path] = []
    root_pick = first_supported_file(root) if root.is_dir() else None
    if root_pick is not None:
        picks.append(root_pick)
    for folder in sorted(p for p in root.rglob("*") if p.is_dir() and not p.name.startswith(".")):
        pick = first_supported_file(folder)
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
    except Exception as e:  # noqa: BLE001 — we want the message, not the type
        return "", f"{type(e).__name__}: {e}"


def safe_filename(rel_path: Path) -> str:
    return str(rel_path).replace("/", "__").replace("\\", "__")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send the first file of every data folder through /api/llm and save the responses."
    )
    parser.add_argument("--base", default=DEFAULT_BASE_URL, help="Backend base URL")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR, help="Data root to walk")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR, help="Where to write results")
    parser.add_argument("--mode", default="ingest", choices=["ingest", "raw"], help="Mode to send to /api/llm")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N files (0 = no cap)")
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout (seconds)")
    args = parser.parse_args()

    data_dir = args.data.resolve()
    if not data_dir.exists():
        print(f"data dir not found: {data_dir}", file=sys.stderr)
        return 1

    files = find_top_one_per_folder(data_dir)
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print("no supported files found", file=sys.stderr)
        return 1

    run_dir = args.out / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"selected {len(files)} files; writing to {run_dir}")

    manifest: list[dict] = []
    with httpx.Client(timeout=args.timeout) as client:
        try:
            client.get(f"{args.base}/health").raise_for_status()
        except Exception as e:
            print(f"backend not reachable at {args.base}: {e}", file=sys.stderr)
            return 1

        for i, fpath in enumerate(files, start=1):
            rel = fpath.relative_to(data_dir)
            print(f"[{i}/{len(files)}] {rel}", flush=True)

            text, err = extract_text(fpath)
            if err:
                manifest.append({"path": str(rel), "error": f"extraction: {err}"})
                (run_dir / (safe_filename(rel) + ".error.txt")).write_text(err)
                continue

            t0 = time.perf_counter()
            try:
                r = client.post(
                    f"{args.base}/api/llm",
                    json={"text": text, "mode": args.mode},
                )
                llm_ms = (time.perf_counter() - t0) * 1000
                r.raise_for_status()
                body = r.json()
            except Exception as e:  # noqa: BLE001
                err_msg = f"{type(e).__name__}: {e}"
                manifest.append({"path": str(rel), "error": f"llm: {err_msg}"})
                (run_dir / (safe_filename(rel) + ".error.txt")).write_text(err_msg)
                continue

            response = body.get("response", "")
            model = body.get("model", "?")
            manifest.append({
                "path": str(rel),
                "size_bytes": fpath.stat().st_size,
                "chars_sent": len(text),
                "llm_ms": round(llm_ms, 2),
                "model": model,
                "mode": body.get("mode", args.mode),
                "response_chars": len(response),
            })

            (run_dir / (safe_filename(rel) + ".txt")).write_text(
                f"=== source : {rel}\n"
                f"=== model  : {model}\n"
                f"=== mode   : {body.get('mode', args.mode)}\n"
                f"=== latency: {llm_ms:.1f} ms\n"
                f"=== chars  : {len(text)} sent / {len(response)} returned\n\n"
                f"{response}\n"
            )

    summary = {
        "ran_at": datetime.now().isoformat(),
        "base_url": args.base,
        "data_dir": str(data_dir),
        "mode": args.mode,
        "total_files": len(files),
        "items": manifest,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    ok = sum(1 for m in manifest if "error" not in m)
    print(f"\nDone. {ok}/{len(manifest)} successful. Summary: {run_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
