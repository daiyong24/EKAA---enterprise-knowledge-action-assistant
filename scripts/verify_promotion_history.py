"""Verify that a rollback digest is present in an immutable promotion history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("history", type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.history.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        parser.error("promotion history is unreadable")
        raise AssertionError from exc
    if not isinstance(payload, list):
        parser.error("promotion history must be a list")
    expected = {
        "environment",
        "digest",
        "commit_sha",
        "provenance_verified",
        "status",
    }
    valid = any(
        isinstance(item, dict)
        and set(item) == expected
        and item["environment"] == args.environment
        and item["digest"] == args.digest
        and item["commit_sha"] == args.commit
        and item["provenance_verified"] is True
        and item["status"] == "promoted"
        for item in payload
    )
    if not valid:
        parser.error("rollback digest is not a previously promoted attested artifact")
    print("verified previously promoted attested artifact")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
