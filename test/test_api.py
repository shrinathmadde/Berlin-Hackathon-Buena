"""Small smoke runner for the /api/sql endpoint.

Run from the repo root:

    .venv/bin/python test/test_api.py
    .venv/bin/python test/test_api.py --question "Count all invoices"
    .venv/bin/python test/test_api.py --base http://localhost:8000
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one natural-language request to /api/sql.")
    parser.add_argument("--base", default=DEFAULT_BASE_URL, help="Backend base URL")
    parser.add_argument(
        "--question",
        default="Show the latest 5 invoices with invoice_id, provider_company, gross_amount, and invoice_date.",
        help="Natural-language request to send",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout (seconds)")
    args = parser.parse_args()

    with httpx.Client(timeout=args.timeout) as client:
        try:
            client.get(f"{args.base}/health").raise_for_status()
        except Exception as e:
            print(f"backend not reachable at {args.base}: {e}", file=sys.stderr)
            return 1

        try:
            response = client.post(f"{args.base}/api/sql", json={"question": args.question})
            response.raise_for_status()
        except Exception as e:
            print(f"request failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    body = response.json()
    print(json.dumps(body, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
