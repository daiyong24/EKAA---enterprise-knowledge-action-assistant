"""Validate and replay the self-contained Stage 20 portfolio package.

The commands intentionally use only the checked-in synthetic fixtures and standard
library code.  They do not contact providers, databases, cloud services, or APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FORBIDDEN_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
_REQUIRED_FILES = (
    "docs/PORTFOLIO.md",
    "docs/PORTFOLIO_ARCHITECTURE.md",
    "docs/PORTFOLIO_SETUP.md",
    "docs/PORTFOLIO_METRICS.md",
    "docs/PORTFOLIO_DECISIONS.md",
    "docs/PORTFOLIO_SECURITY.md",
    "docs/PORTFOLIO_LIMITATIONS.md",
    "docs/PORTFOLIO_WALKTHROUGH.md",
    "docs/PORTFOLIO_MANIFEST.json",
    "docs/evaluation/golden_dataset_v1.json",
    "docs/evaluation/synthetic_corpus_v1.json",
    "docs/evaluation/baseline_policy_v1.json",
    "docs/evaluation/artifacts/synthetic_retrieval_baseline_v1.json",
    "docs/evaluation/stage12_baseline_v1.json",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _root(value: str | None) -> Path:
    root = Path(value).resolve() if value else Path(__file__).resolve().parent.parent
    if not (root / "README.md").is_file() or not (root / "docs").is_dir():
        raise ValueError(f"not an EKAA repository root: {root}")
    return root


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _safe_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")):
        raise ValueError("manifest path must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("manifest path contains unsafe traversal")
    return value


def _reject_symlink_components(path: Path) -> Path:
    """Reject symlink path components before resolving or opening a path."""

    raw_path = Path(os.path.abspath(os.fspath(path)))
    for parent in (raw_path, *raw_path.parents):
        if parent.exists() and parent.is_symlink():
            raise ValueError(f"path contains a symlink component: {path}")
    return raw_path


def _validate_manifest(root: Path) -> str:
    manifest_path = root / "docs/PORTFOLIO_MANIFEST.json"
    _reject_symlink_components(manifest_path)
    manifest = _load_json(manifest_path)
    if (
        manifest.get("schema_version") != 1
        or manifest.get("manifest_id") != "ekaa-portfolio-manifest"
        or manifest.get("data_policy") != "synthetic_or_public_only"
    ):
        raise ValueError("portfolio manifest identity or policy is invalid")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("portfolio manifest has no files")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("portfolio manifest entry is invalid")
        path_value = _safe_manifest_path(entry.get("path"))
        checksum = entry.get("sha256")
        if (
            path_value in seen
            or not isinstance(checksum, str)
            or not _SHA256.fullmatch(checksum)
        ):
            raise ValueError("portfolio manifest contains duplicate or invalid entry")
        seen.add(path_value)
        path = root / path_value
        _reject_symlink_components(path)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"manifest file is missing or symlinked: {path_value}")
        if _sha256(path) != checksum:
            raise ValueError(f"manifest checksum mismatch: {path_value}")
    # The manifest cannot contain its own digest without a circular definition;
    # its own JSON syntax and SHA-256 are validated separately by the caller.
    missing = (set(_REQUIRED_FILES) - {"docs/PORTFOLIO_MANIFEST.json"}) - seen
    if missing:
        raise ValueError(f"manifest omits required files: {sorted(missing)}")
    return _sha256(manifest_path)


def _validate_links(root: Path) -> int:
    checked = 0
    for path in (root / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for target in _MARKDOWN_LINK.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            candidate = (path.parent / target).resolve()
            if root not in candidate.parents and candidate != root:
                raise ValueError(f"link escapes repository: {path}: {target}")
            if not candidate.is_file():
                raise ValueError(f"broken local Markdown link: {path}: {target}")
            checked += 1
    return checked


def _validate_secret_contract(root: Path) -> int:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
            text=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            "git is required for the tracked-file secret contract"
        ) from exc
    tracked = [Path(item) for item in result.stdout.decode().split("\0") if item]
    forbidden_names = {".env", ".secrets", ".secrets.baseline"}
    tracked_names = {path.name for path in tracked}
    if ".env" in tracked_names or ".secrets" in tracked_names:
        raise ValueError("environment secret file is tracked")
    try:
        history = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--all",
                "--format=",
                "--",
                ".env",
                ".secrets",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("could not inspect Git history for secret paths") from exc
    if history.stdout.strip():
        raise ValueError("environment secret path appears in Git history")
    try:
        secret_history = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                "--all",
                "--format=%H",
                "-G",
                r"sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|"
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
                "--",
                ".",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("could not inspect Git history for secret patterns") from exc
    if secret_history.stdout.strip():
        raise ValueError("possible secret pattern appears in Git history")
    findings: list[str] = []
    for relative in tracked:
        path = root / relative
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _FORBIDDEN_SECRET.search(content):
            findings.append(str(relative))
    if findings:
        raise ValueError(f"possible secret in tracked files: {findings}")
    return len(tracked) - len(forbidden_names.intersection(tracked_names))


def _validate_metric_sources(root: Path) -> dict[str, object]:
    retrieval_path = (
        root / "docs/evaluation/artifacts/synthetic_retrieval_baseline_v1.json"
    )
    retrieval = _load_json(retrieval_path)
    records = retrieval.get("records")
    provenance = retrieval.get("provenance")
    if not isinstance(records, list) or not isinstance(provenance, dict):
        raise ValueError("retrieval artifact is missing records or provenance")
    dataset = _load_json(root / "docs/evaluation/golden_dataset_v1.json")
    corpus = _load_json(root / "docs/evaluation/synthetic_corpus_v1.json")
    policy_path = root / "docs/evaluation/baseline_policy_v1.json"
    policy = _load_json(policy_path)
    _validate_source_policy(dataset, corpus)
    _validate_policy(
        policy,
        dataset=dataset,
        corpus=corpus,
        corpus_path=root / "docs/evaluation/synthetic_corpus_v1.json",
    )
    dataset_hash = _sha256(root / "docs/evaluation/golden_dataset_v1.json")
    corpus_hash = _sha256(root / "docs/evaluation/synthetic_corpus_v1.json")
    policy_hash = _sha256(policy_path)
    if (
        retrieval.get("dataset_id") != dataset.get("dataset_id")
        or retrieval.get("dataset_version") != dataset.get("dataset_version")
        or retrieval.get("dataset_sha256") != dataset_hash
        or retrieval.get("corpus_sha256") != corpus_hash
        or provenance.get("policy_version") != policy.get("policy_version")
        or provenance.get("policy_sha256") != policy_hash
        or provenance.get("target_id") != "synthetic-corpus-retrieval"
        or provenance.get("target_version") != "1.0.0"
    ):
        raise ValueError("retrieval artifact provenance does not match sources")
    questions = dataset.get("questions")
    if not isinstance(questions, list) or len(records) != len(questions):
        raise ValueError("retrieval artifact coverage does not match dataset")
    question_ids = [
        item.get("question_id") for item in questions if isinstance(item, dict)
    ]
    record_ids = [item.get("question_id") for item in records if isinstance(item, dict)]
    if (
        len(question_ids) != len(questions)
        or len(record_ids) != len(records)
        or question_ids != record_ids
        or len(set(record_ids)) != len(record_ids)
    ):
        raise ValueError(
            "retrieval artifact question mapping is incomplete or reordered"
        )
    top_k = retrieval.get("top_k")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("retrieval artifact top_k is invalid")
    missing_records = _validate_metric_records(
        records, expected_ids=question_ids, top_k=top_k
    )
    hits = [
        1.0
        if isinstance(item, dict)
        and (item.get("matched_expected_chunk_count") or 0) > 0
        else 0.0
        for item in records
    ]
    precision = [
        (item.get("matched_expected_chunk_count") or 0) / top_k
        for item in records
        if isinstance(item, dict)
    ]
    expected = {
        "recall_at_k": sum(
            (item.get("matched_expected_chunk_count") or 0)
            / item["expected_chunk_count"]
            for item in records
            if isinstance(item, dict)
        )
        / len(records),
        "hit_rate_at_k": sum(hits) / len(hits),
        "precision_at_k": sum(precision) / len(precision),
    }
    declared = retrieval.get("retrieval")
    if not isinstance(declared, dict):
        raise ValueError("retrieval artifact is missing retrieval metrics")
    _validate_retrieval_aggregate(
        declared, record_count=len(records), missing_records=missing_records
    )
    for key, value in expected.items():
        declared_value = declared.get(key)
        if not isinstance(declared_value, (int, float)) or not math.isclose(
            float(declared_value), value, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise ValueError(f"retrieval metric is not reproducible: {key}")
    # The compact checked-in artifact records aggregate MRR but intentionally omits
    # per-row ranks. Validate the recorded value's safe numeric domain and preserve
    # it as an artifact-sourced metric rather than pretending to recompute it.
    recorded_mrr = declared.get("mrr")
    if (
        not isinstance(recorded_mrr, (int, float))
        or not math.isfinite(recorded_mrr)
        or not 0 <= recorded_mrr <= 1
    ):
        raise ValueError("retrieval artifact MRR is invalid")
    expected["mrr"] = recorded_mrr
    stage12 = _load_json(root / "docs/evaluation/stage12_baseline_v1.json")
    recorded = _validate_stage12_source(stage12)
    return {
        "retrieval_run_id": provenance.get("run_id"),
        "retrieval_artifact_sha256": _sha256(retrieval_path),
        "dataset_sha256": dataset_hash,
        "corpus_sha256": corpus_hash,
        "retrieval_metrics": expected,
        "stage12_success_rate": recorded.get("success_rate"),
        "stage12_artifact_sha256": _sha256(
            root / "docs/evaluation/stage12_baseline_v1.json"
        ),
        "policy_version": policy["policy_version"],
        "policy_sha256": policy_hash,
    }


def _validate_source_policy(dataset: dict[str, Any], corpus: dict[str, Any]) -> None:
    if dataset.get("source_policy") != "synthetic_or_public_only":
        raise ValueError("dataset source policy is not permitted")
    if (
        dataset.get("schema_version") != 1
        or dataset.get("dataset_id") != "ekaa-synthetic-golden"
        or dataset.get("dataset_version") != "1.0.0"
    ):
        raise ValueError("dataset identity or version is not approved")
    questions = dataset.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("dataset has no questions")
    if any(
        not isinstance(question, dict)
        or question.get("provenance") not in {"synthetic", "public"}
        for question in questions
    ):
        raise ValueError("dataset contains a non-synthetic/public question")
    if (
        corpus.get("schema_version") != 1
        or corpus.get("corpus_id") != "ekaa-synthetic-evaluation-corpus"
        or corpus.get("corpus_version") != "1.0.0"
        or corpus.get("source_policy") not in {"synthetic_only", "public_only"}
    ):
        raise ValueError("corpus source policy is not permitted")
    chunks = corpus.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError("corpus has no chunks")
    chunk_ids = {
        chunk.get("chunk_id")
        for chunk in chunks
        if isinstance(chunk, dict) and isinstance(chunk.get("chunk_id"), str)
    }
    if len(chunk_ids) != len(chunks):
        raise ValueError("corpus chunk IDs must be unique and valid")
    if any(
        not isinstance(chunk, dict)
        or not isinstance(chunk.get("chunk_id"), str)
        or not isinstance(chunk.get("content"), str)
        for chunk in chunks
    ):
        raise ValueError("corpus contains an invalid chunk")
    for question in questions:
        expected = question.get("expected_chunk_ids")
        if not isinstance(expected, list) or any(
            chunk_id not in chunk_ids for chunk_id in expected
        ):
            raise ValueError("dataset references a chunk missing from the corpus")


def _validate_policy(
    policy: dict[str, Any],
    *,
    dataset: dict[str, Any],
    corpus: dict[str, Any],
    corpus_path: Path,
) -> None:
    policy_corpus = policy.get("synthetic_corpus_sha256")
    if (
        policy.get("schema_version") != 1
        or policy.get("status") != "active"
        or not isinstance(policy.get("policy_version"), str)
        or policy.get("dataset_id") != dataset.get("dataset_id")
        or policy.get("dataset_version") != dataset.get("dataset_version")
        or policy_corpus != _sha256(corpus_path)
    ):
        raise ValueError("policy identity or corpus binding is invalid")
    if not isinstance(policy.get("thresholds"), dict):
        raise ValueError("policy thresholds are invalid")
    if corpus.get("source_policy") not in {"synthetic_only", "public_only"}:
        raise ValueError("policy corpus source is not permitted")


def _validate_metric_records(
    records: list[Any], *, expected_ids: list[object], top_k: int
) -> int:
    if not records or len(expected_ids) != len(records):
        raise ValueError("retrieval artifact record count is invalid")
    missing_records = 0
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("retrieval record is invalid")
        expected = record.get("expected_chunk_count")
        retrieved = record.get("retrieved_chunk_count")
        matched = record.get("matched_expected_chunk_count")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            raise ValueError("retrieval record count or denominator is invalid")
        if retrieved is None:
            if matched is not None:
                raise ValueError("missing retrieval record has a matched count")
            missing_records += 1
        elif (
            not isinstance(retrieved, int)
            or isinstance(retrieved, bool)
            or retrieved < 0
            or retrieved > top_k
            or not isinstance(matched, int)
            or isinstance(matched, bool)
            or matched < 0
            or matched > expected
            or matched > retrieved
        ):
            raise ValueError("retrieval record count or denominator is invalid")
        for field in ("latency_ms",):
            value = record.get(field)
            if value is not None and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("retrieval record numeric input is invalid")
        token_values = [
            record.get(field)
            for field in ("input_tokens", "output_tokens", "total_tokens")
        ]
        if (
            any(
                value is not None
                and (not isinstance(value, int) or isinstance(value, bool) or value < 0)
                for value in token_values
            )
            or (token_values[0] is None) != (token_values[1] is None)
            or (token_values[0] is None) != (token_values[2] is None)
        ):
            raise ValueError("retrieval record token input is invalid")
        if (
            token_values[0] is not None
            and token_values[2] != token_values[0] + token_values[1]
        ):
            raise ValueError("retrieval record token total is invalid")
        claims = record.get("claim_count")
        unsupported = record.get("unsupported_claim_count")
        if (claims is None) != (unsupported is None) or (
            claims is not None
            and (
                not isinstance(claims, int)
                or isinstance(claims, bool)
                or claims < 0
                or not isinstance(unsupported, int)
                or isinstance(unsupported, bool)
                or unsupported < 0
                or unsupported > claims
            )
        ):
            raise ValueError("retrieval record claim input is invalid")
    return missing_records


def _validate_retrieval_aggregate(
    declared: dict[str, Any], *, record_count: int, missing_records: int
) -> None:
    if record_count < 1:
        raise ValueError("retrieval aggregate denominator is invalid")
    for key in ("evaluated_questions", "missing_results"):
        value = declared.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("retrieval aggregate count is invalid")
    if declared["evaluated_questions"] != record_count:
        raise ValueError("retrieval aggregate coverage is invalid")
    if declared["missing_results"] != missing_records:
        raise ValueError("retrieval aggregate missing count is invalid")
    for key in ("recall_at_k", "hit_rate_at_k", "precision_at_k", "mrr"):
        value = declared.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value < 0
            or value > 1
        ):
            raise ValueError("retrieval aggregate metric is invalid")


def _validate_stage12_source(stage12: dict[str, Any]) -> dict[str, Any]:
    if (
        stage12.get("suite_id") != "stage12-single-agent-baseline-v1"
        or stage12.get("version") != "1.0.0"
        or stage12.get("scope") != "local synthetic deterministic contract suite"
        or not isinstance(stage12.get("tasks"), list)
        or len(stage12["tasks"]) != 3
    ):
        raise ValueError("Stage 12 baseline identity is invalid")
    task_ids = {task.get("id") for task in stage12["tasks"] if isinstance(task, dict)}
    if task_ids != {"no-tool-answer", "authorized-read-tool", "write-proposal-stops"}:
        raise ValueError("Stage 12 task identity is invalid")
    expected_tasks = {
        "no-tool-answer": ("completed", 1),
        "authorized-read-tool": ("completed", 2),
        "write-proposal-stops": ("awaiting_approval", 1),
    }
    for task in stage12["tasks"]:
        if (
            not isinstance(task, dict)
            or set(task) != {"id", "expected_terminal_status", "expected_steps"}
            or task["expected_terminal_status"] != expected_tasks[task["id"]][0]
            or task["expected_steps"] != expected_tasks[task["id"]][1]
        ):
            raise ValueError("Stage 12 task contract is invalid")
    if stage12.get("model") != "deterministic test policies":
        raise ValueError("Stage 12 model identity is invalid")
    recorded = stage12.get("recorded_results")
    if not isinstance(recorded, dict) or set(recorded) != {
        "successful_tasks",
        "failed_tasks",
        "success_rate",
        "run_date",
        "runner",
    }:
        raise ValueError("Stage 12 results are invalid")
    successful = recorded.get("successful_tasks")
    failed = recorded.get("failed_tasks")
    success_rate = recorded.get("success_rate")
    if (
        not isinstance(successful, int)
        or isinstance(successful, bool)
        or successful < 0
        or not isinstance(failed, int)
        or isinstance(failed, bool)
        or failed < 0
        or successful + failed < 1
        or not isinstance(success_rate, (int, float))
        or isinstance(success_rate, bool)
        or not math.isfinite(success_rate)
        or not 0 <= success_rate <= 1
        or not math.isclose(success_rate, successful / (successful + failed))
    ):
        raise ValueError("Stage 12 result domain is invalid")
    return recorded


def validate(root: Path) -> dict[str, object]:
    for relative in _REQUIRED_FILES:
        if not (root / relative).is_file():
            raise ValueError(f"required portfolio file is missing: {relative}")
    manifest_sha = _validate_manifest(root)
    link_count = _validate_links(root)
    tracked_count = _validate_secret_contract(root)
    metrics = _validate_metric_sources(root)
    return {
        "status": "ok",
        "manifest_sha256": manifest_sha,
        "local_links_checked": link_count,
        "tracked_files_checked": tracked_count,
        "metric_sources": metrics,
    }


def demo(root: Path, output_dir: Path) -> dict[str, object]:
    result = validate(root)
    raw_output = _reject_symlink_components(output_dir)
    output_dir = raw_output.resolve()
    if output_dir == root or output_dir.is_symlink():
        raise ValueError(
            "demo output must be a disposable directory, not repository root"
        )
    if root in output_dir.parents:
        relative = output_dir.relative_to(root)
        if not relative.parts or relative.parts[0] != ".stage20-demo-output":
            raise ValueError("repository demo output must use .stage20-demo-output")
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema_version": 1,
        "demo": "stage20-safe-synthetic-replay",
        "status": "ok",
        "manifest_sha256": result["manifest_sha256"],
        "metric_sources": result["metric_sources"],
        "external_calls": False,
        "synthetic_only": True,
    }
    summary_path = output_dir / "summary.json"
    if summary_path.is_symlink():
        raise ValueError("demo summary cannot be a symlink")
    if summary_path.exists() and summary_path.stat().st_nlink > 1:
        raise ValueError("demo summary cannot be a hardlink")
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "demo", "audit-metrics"))
    parser.add_argument(
        "--root", help="EKAA repository root (defaults to this checkout)"
    )
    parser.add_argument("--output-dir", default=".stage20-demo-output")
    args = parser.parse_args(argv)
    try:
        root = _root(args.root)
        if args.command == "demo":
            result = demo(root, Path(args.output_dir))
        elif args.command == "audit-metrics":
            result = _validate_metric_sources(root)
        else:
            result = validate(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"stage20 validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
