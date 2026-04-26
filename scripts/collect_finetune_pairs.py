from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPORT_ROOT = REPO_ROOT / "data" / "finetune_exports"
DEFAULT_OUTPUT = DEFAULT_EXPORT_ROOT / "chat_sft.jsonl"


def collect_chat_records(export_root: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []

    for path in sorted(export_root.glob("*/per_file/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        system_prompt = data.get("system_prompt")
        user_prompt = data.get("user_prompt")
        raw_model_output = data.get("raw_model_output")

        if not all(isinstance(value, str) and value for value in (system_prompt, user_prompt, raw_model_output)):
            continue

        records.append(
            {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": raw_model_output},
                ],
            }
        )

    return records


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect per_file exports into Decoder/Chat SFT JSONL."
    )
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    records = collect_chat_records(args.export_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Wrote {len(records)} chat SFT records to {args.output}")


if __name__ == "__main__":
    main()
