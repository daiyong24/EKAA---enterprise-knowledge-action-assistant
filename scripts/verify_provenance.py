"""Verify the structured provenance artifact produced by the CI image job."""

from __future__ import annotations

import argparse
from pathlib import Path

from backend.app.deployment.release import PromotionError, ProvenanceRecord


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        record = ProvenanceRecord.from_file(args.artifact)
        record.validate()
        if record.image_digest != args.digest or record.commit_sha != args.commit:
            raise PromotionError("provenance does not match release identity")
    except PromotionError as exc:
        parser.error(str(exc))
    print("verified structured BuildKit provenance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
