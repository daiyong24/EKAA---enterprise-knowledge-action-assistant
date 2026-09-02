"""Fail-closed comparison for detect-secrets scan output and its reviewed baseline."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _finding_key(finding: Mapping[str, Any]) -> tuple[str, str]:
    return (str(finding.get("type", "")), str(finding.get("hashed_secret", "")))


def verify_secret_scan(scan_path: str | Path, baseline_path: str | Path) -> None:
    scan = json.loads(Path(scan_path).read_text(encoding="utf-8"))
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    scan_results = scan.get("results", {})
    baseline_results = baseline.get("results", {})
    approved: set[tuple[str, str]] = set()
    for findings in baseline_results.values():
        for finding in findings:
            if finding.get("is_secret", True):
                raise ValueError("baseline contains an unaudited secret finding")
            approved.add(_finding_key(finding))
    unapproved = [
        finding
        for findings in scan_results.values()
        for finding in findings
        if _finding_key(finding) not in approved
    ]
    if unapproved:
        raise ValueError("secret scan found unapproved findings")
