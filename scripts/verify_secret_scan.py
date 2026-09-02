"""Fail the CI job for a finding absent from the reviewed secret baseline."""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.app.deployment.secrets import verify_secret_scan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    try:
        verify_secret_scan(args.scan, args.baseline)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        parser.error(str(exc))
    print("secret scan matches reviewed baseline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
