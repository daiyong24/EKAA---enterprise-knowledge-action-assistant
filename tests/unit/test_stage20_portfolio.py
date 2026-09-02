"""Contract tests for the self-contained Stage 20 portfolio package."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.stage20_portfolio import (
    _validate_metric_records,
    _validate_policy,
    _validate_retrieval_aggregate,
    _validate_source_policy,
    _validate_stage12_source,
    demo,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]


def test_portfolio_validation_checks_manifest_links_and_metrics() -> None:
    result = validate(ROOT)

    assert result["status"] == "ok"
    assert result["local_links_checked"] > 0
    sources = result["metric_sources"]
    assert isinstance(sources, dict)
    assert sources["retrieval_run_id"] == "reviewed-local-synthetic-retrieval-v1"


def test_demo_replays_safe_synthetic_fixture(tmp_path: Path) -> None:
    result = demo(ROOT, tmp_path / "demo")

    output = tmp_path / "demo" / "summary.json"
    assert output.is_file()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == result
    assert written["external_calls"] is False
    assert written["synthetic_only"] is True


def test_demo_rejects_repository_root_as_output(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="disposable directory"):
        demo(ROOT, ROOT)


def test_source_policy_rejects_confidential_labels() -> None:
    dataset = {
        "source_policy": "confidential",
        "questions": [{"provenance": "synthetic"}],
    }
    corpus = {"source_policy": "synthetic_only", "chunks": []}
    with pytest.raises(ValueError, match="dataset source policy"):
        _validate_source_policy(dataset, corpus)


def test_source_policy_rejects_dataset_chunk_missing_from_corpus() -> None:
    dataset = json.loads(
        (ROOT / "docs/evaluation/golden_dataset_v1.json").read_text(encoding="utf-8")
    )
    corpus = json.loads(
        (ROOT / "docs/evaluation/synthetic_corpus_v1.json").read_text(encoding="utf-8")
    )
    dataset["questions"][0]["expected_chunk_ids"] = ["missing-chunk"]
    with pytest.raises(ValueError, match="missing from the corpus"):
        _validate_source_policy(dataset, corpus)


def test_policy_binding_rejects_wrong_corpus_hash() -> None:
    dataset = json.loads(
        (ROOT / "docs/evaluation/golden_dataset_v1.json").read_text(encoding="utf-8")
    )
    corpus = json.loads(
        (ROOT / "docs/evaluation/synthetic_corpus_v1.json").read_text(encoding="utf-8")
    )
    policy = json.loads(
        (ROOT / "docs/evaluation/baseline_policy_v1.json").read_text(encoding="utf-8")
    )
    policy["synthetic_corpus_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="policy identity"):
        _validate_policy(
            policy,
            dataset=dataset,
            corpus=corpus,
            corpus_path=ROOT / "docs/evaluation/synthetic_corpus_v1.json",
        )

    dataset["source_policy"] = "synthetic_or_public_only"
    dataset["questions"][0]["provenance"] = "confidential"
    with pytest.raises(ValueError, match="non-synthetic/public"):
        _validate_source_policy(dataset, corpus)


def test_metric_input_contract_rejects_invalid_counts_and_aggregates() -> None:
    with pytest.raises(ValueError, match="count or denominator"):
        _validate_metric_records(
            [
                {
                    "question_id": "G001",
                    "expected_chunk_count": 0,
                    "retrieved_chunk_count": 5,
                    "matched_expected_chunk_count": 1,
                }
            ],
            expected_ids=["G001"],
            top_k=5,
        )
    with pytest.raises(ValueError, match="aggregate metric"):
        _validate_retrieval_aggregate(
            {
                "evaluated_questions": 1,
                "missing_results": 0,
                "recall_at_k": float("nan"),
                "hit_rate_at_k": 1.0,
                "precision_at_k": 0.2,
                "mrr": 1.0,
            },
            record_count=1,
            missing_records=0,
        )


def test_stage12_source_requires_known_suite_and_finite_success_rate() -> None:
    with pytest.raises(ValueError, match="identity"):
        _validate_stage12_source({"suite_id": "unreviewed"})
    source = json.loads(
        (ROOT / "docs/evaluation/stage12_baseline_v1.json").read_text(encoding="utf-8")
    )
    source["recorded_results"]["success_rate"] = float("nan")
    with pytest.raises(ValueError, match="result domain"):
        _validate_stage12_source(source)


def test_demo_rejects_existing_symlink_component(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links unavailable in this environment")
    with pytest.raises(ValueError, match="cannot contain symlinks"):
        demo(ROOT, link / ".stage20-demo-output")


def test_demo_rejects_summary_symlink(tmp_path: Path) -> None:
    output = tmp_path / ".stage20-demo-output"
    output.mkdir()
    target = tmp_path / "target.json"
    target.write_text("do not overwrite", encoding="utf-8")
    summary = output / "summary.json"
    try:
        summary.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links unavailable in this environment")
    with pytest.raises(ValueError, match="summary cannot be a symlink"):
        demo(ROOT, output)


def test_demo_rejects_summary_hardlink(tmp_path: Path) -> None:
    output = tmp_path / ".stage20-demo-output"
    output.mkdir()
    target = tmp_path / "target.json"
    target.write_text("do not overwrite", encoding="utf-8")
    summary = output / "summary.json"
    try:
        summary.hardlink_to(target)
    except OSError:
        pytest.skip("hard links unavailable in this environment")
    if summary.stat().st_nlink < 2:
        pytest.skip("hard link count unavailable in this environment")
    with pytest.raises(ValueError, match="summary cannot be a hardlink"):
        demo(ROOT, output)
