"""Stage 8 formula, schema, replay, calibration, and regression-gate coverage."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

import backend.app.evaluation.local_generation as local_generation
from backend.app.core.config import Settings
from backend.app.embeddings.models import RetrievedChunk
from backend.app.evaluation import cli
from backend.app.evaluation.artifacts import (
    apply_assessment_bundle,
    load_generation_source_artifact,
    write_artifacts,
)
from backend.app.evaluation.corpus import (
    APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    SyntheticCorpus,
    SyntheticCorpusRetrievalTarget,
    load_synthetic_corpus,
    validate_dataset_coverage,
)
from backend.app.evaluation.dataset import (
    DatasetValidationError,
    load_assessments,
    load_dataset,
)
from backend.app.evaluation.local_generation import (
    _LOCAL_OLLAMA_EVALUATION_PROFILE,
    LocalOllamaReadinessRecord,
    _LocalOllamaEvaluationProvider,
    build_local_ollama_readiness_preflight,
    build_synthetic_ollama_generation_target,
)
from backend.app.evaluation.metrics import (
    human_review_rate,
    percentile_nearest_rank,
    retrieval_metrics,
    unsupported_claim_rate,
)
from backend.app.evaluation.models import (
    EvaluationObservation,
    GoldenDataset,
    GoldenQuestion,
    HumanAssessment,
    RunProvenance,
)
from backend.app.evaluation.regression import (
    RegressionPolicy,
    RegressionThresholds,
    compare_to_baseline,
    load_regression_policy,
)
from backend.app.evaluation.review_packet import (
    ReviewCitationMaterial,
    ReviewClaimMaterial,
    ReviewEvidence,
    ReviewPacketItem,
    write_reviewer_packet,
)
from backend.app.evaluation.runner import (
    EvaluationRunner,
    RagGenerationEvaluationTarget,
    StaticFixtureTarget,
    observation_from_fixture,
)
from backend.app.llm.errors import (
    ProviderOutputError,
    ProviderRetryableError,
    ProviderTimeoutError,
)
from backend.app.llm.models import ChatPrompt, ChatResult, TokenUsage
from backend.app.llm.provider import OpenAICompatibleProvider
from backend.app.rag.models import Citation, CitationClaim, RagAnswerResult

TEST_POLICY = RegressionPolicy(
    policy_version="test-policy",
    dataset_id="test-golden",
    dataset_version="1.0.0",
    thresholds=RegressionThresholds(),
    sha256="test-policy-sha",
)


def test_reviewer_packet_is_local_markdown_and_rejects_invalid_source_hash(
    tmp_path: Path,
) -> None:
    packet_path = tmp_path / "review.md"
    provenance = RunProvenance(
        target_id="synthetic-corpus-ollama-generation",
        target_version="1.2.0",
        model_identity="qwen3:8b",
        configuration_sha256="a" * 64,
        run_id="review-source-1",
        calibration_id=None,
        policy_version="policy-v1",
        policy_sha256="b" * 64,
    )
    item = ReviewPacketItem(
        question_id="G001",
        question="Synthetic question?",
        answer="Synthetic answer.",
        failure_kind=None,
        evidence=(ReviewEvidence("synthetic-001", "Synthetic evidence."),),
        claims=(
            ReviewClaimMaterial(
                text="Synthetic claim.",
                status="supported",
                citation_ids=("c1",),
                unsupported_reason=None,
            ),
        ),
        citations=(ReviewCitationMaterial("c1", "synthetic-001", "Excerpt."),),
    )

    write_reviewer_packet(
        path=packet_path,
        reviewer_id="reviewer_a",
        source_artifact_sha256="c" * 64,
        provenance=provenance,
        items=(item,),
    )

    rendered = packet_path.read_text(encoding="utf-8")
    assert "Local Synthetic Reviewer Packet" in rendered
    assert "Source artifact SHA-256: `" + "c" * 64 + "`" in rendered
    assert "Synthetic question?" in rendered
    assert "Correctness: [ ] True  [ ] False" in rendered

    rejected_path = tmp_path / "rejected.md"
    with pytest.raises(ValueError, match="SHA-256"):
        write_reviewer_packet(
            path=rejected_path,
            reviewer_id="reviewer_a",
            source_artifact_sha256="invalid",
            provenance=provenance,
            items=(item,),
        )
    assert not rejected_path.exists()


def _dataset(path: Path) -> GoldenDataset:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "test-golden",
                "dataset_version": "1.0.0",
                "source_policy": "synthetic_or_public_only",
                "questions": [
                    {
                        "question_id": "q-1",
                        "question": "Synthetic question one?",
                        "expected_chunk_ids": ["chunk-1", "chunk-2"],
                        "provenance": "synthetic",
                        "review_status": "reviewed",
                        "review_note": "Reviewed expected evidence.",
                    },
                    {
                        "question_id": "q-2",
                        "question": "Synthetic question two?",
                        "expected_chunk_ids": ["chunk-3"],
                        "provenance": "synthetic",
                        "review_status": "reviewed",
                        "review_note": "Reviewed expected evidence.",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return load_dataset(path)


def _observation(
    question_id: str,
    chunks: tuple[str, ...] | None,
    *,
    claims: int | None = 2,
    unsupported: int | None = 0,
) -> EvaluationObservation:
    return EvaluationObservation(
        question_id=question_id,
        retrieved_chunk_ids=chunks,
        model="local-test-model",
        latency_ms=None,
        usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        claim_count=claims,
        unsupported_claim_count=unsupported,
        failure_kind=None,
    )


def _eligible_generation_source(
    tmp_path: Path, *, run_id: str = "source-run-a", target_version: str = "1.2.0"
) -> tuple[Path, GoldenDataset, RegressionPolicy]:
    """Create a content-free, full local-generation shaped source without a provider."""

    dataset_path = Path("docs/evaluation/golden_dataset_v1.json")
    policy_path = Path("docs/evaluation/baseline_policy_v1.json")
    dataset = load_dataset(dataset_path)
    policy = load_regression_policy(policy_path)
    target = StaticFixtureTarget(
        {
            question.question_id: _observation(
                question.question_id, question.expected_chunk_ids
            )
            for question in dataset.questions
        },
        RunProvenance(
            target_id="synthetic-corpus-ollama-generation",
            target_version=target_version,
            model_identity="model-test",
            configuration_sha256="a" * 64,
            run_id=run_id,
            calibration_id=None,
            policy_version=policy.policy_version,
            policy_sha256=policy.sha256,
        ),
    )
    summary = asyncio.run(
        EvaluationRunner(target=target, clock=lambda: 0.0).run(
            dataset=dataset,
            dataset_path=dataset_path,
            policy=policy,
            top_k=5,
            corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        )
    )
    source_path = tmp_path / f"{run_id}.json"
    write_artifacts(
        summary=summary, json_path=source_path, csv_path=tmp_path / f"{run_id}.csv"
    )
    return source_path, dataset, policy


def _assessment_payload(
    *, source_path: Path, dataset: GoldenDataset, policy: RegressionPolicy
) -> dict[str, object]:
    source = load_generation_source_artifact(
        source_path,
        dataset=dataset,
        dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
        policy=policy,
    )
    provenance = source.summary.provenance
    return {
        "schema_version": 2,
        "calibration_set_id": "calibration-v2",
        "source": {
            "source_artifact_sha256": source.sha256,
            "source_run_id": provenance.run_id,
            "dataset_id": source.summary.dataset_id,
            "dataset_version": source.summary.dataset_version,
            "dataset_sha256": source.summary.dataset_sha256,
            "corpus_sha256": source.summary.corpus_sha256,
            "top_k": source.summary.top_k,
            "target_id": provenance.target_id,
            "target_version": provenance.target_version,
            "model_identity": provenance.model_identity,
            "configuration_sha256": provenance.configuration_sha256,
            "policy_version": provenance.policy_version,
            "policy_sha256": provenance.policy_sha256,
        },
        "assessments": [
            {
                "question_id": question.question_id,
                "correctness": True,
                "faithfulness": True,
                "citation_accuracy": True,
                "reviewer_id": "reviewer-a",
            }
            for question in dataset.questions
        ],
    }


def _hardlink_or_skip(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hard links are unavailable for this test: {exc}")


def _symlink_or_skip(source: Path, destination: Path) -> None:
    try:
        destination.symlink_to(source)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links are unavailable for this test: {exc}")


def test_dataset_schema_rejects_unreviewed_or_non_synthetic_public_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset_id": "bad",
                "dataset_version": "1.0.0",
                "source_policy": "enterprise",
                "questions": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DatasetValidationError, match="source policy"):
        load_dataset(path)


def test_versioned_golden_dataset_has_initial_fifty_reviewed_questions() -> None:
    dataset = load_dataset(Path("docs/evaluation/golden_dataset_v1.json"))
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))

    assert dataset.dataset_version == "1.0.0"
    assert len(dataset.questions) == 50
    assert all(question.review_status == "reviewed" for question in dataset.questions)
    assert all(question.provenance == "synthetic" for question in dataset.questions)
    validate_dataset_coverage(dataset, corpus)
    policy = load_regression_policy(Path("docs/evaluation/baseline_policy_v1.json"))
    assert corpus.sha256 == APPROVED_LOCAL_OLLAMA_CORPUS_SHA256
    assert policy.synthetic_corpus_sha256 == APPROVED_LOCAL_OLLAMA_CORPUS_SHA256
    assert all(
        question.question not in chunk.content
        for question in dataset.questions
        for chunk in corpus.chunks
    )


def test_synthetic_corpus_target_returns_actual_ranked_evidence_not_citations() -> None:
    dataset_path = Path("docs/evaluation/golden_dataset_v1.json")
    corpus_path = Path("docs/evaluation/synthetic_corpus_v1.json")
    dataset = load_dataset(dataset_path)
    corpus = load_synthetic_corpus(corpus_path)
    policy = load_regression_policy(Path("docs/evaluation/baseline_policy_v1.json"))
    target = SyntheticCorpusRetrievalTarget(
        corpus=corpus,
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        run_id="synthetic-target-test",
    )
    summary = asyncio.run(
        EvaluationRunner(target=target, clock=lambda: 0.0).run(
            dataset=dataset,
            dataset_path=dataset_path,
            policy=policy,
            top_k=5,
            corpus_sha256=corpus.sha256,
        )
    )

    assert summary.retrieval.hit_rate_at_k == 1.0
    assert summary.retrieval.mrr == 1.0
    assert summary.unsupported_claim_rate is None
    assert summary.citation_accuracy is None
    assert summary.corpus_sha256 == corpus.sha256


def test_synthetic_ollama_generation_target_uses_rag_boundary_with_fake_provider() -> (
    None
):
    class FakeProvider:
        def __init__(self) -> None:
            self.prompts: list[ChatPrompt] = []

        async def complete(self, prompt: ChatPrompt) -> ChatResult:
            self.prompts.append(prompt)
            return ChatResult(
                answer=json.dumps(
                    {
                        "claims": [
                            {
                                "text": "The synthetic recovery target is four hours.",
                                "citation_chunk_ids": ["synthetic-recovery-001"],
                            }
                        ]
                    }
                ),
                model="qwen3:4b",
                usage=TokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
                finish_reason="stop",
            )

    corpus_path = Path("docs/evaluation/synthetic_corpus_v1.json")
    dataset_path = Path("docs/evaluation/golden_dataset_v1.json")
    corpus = load_synthetic_corpus(corpus_path)
    dataset = load_dataset(dataset_path)
    policy = load_regression_policy(Path("docs/evaluation/baseline_policy_v1.json"))
    provider = FakeProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version=policy.policy_version,
        policy_sha256=policy.sha256,
        run_id="fake-ollama-generation-test",
        top_k=5,
        policy_corpus_sha256=policy.synthetic_corpus_sha256,
        provider=provider,
    )

    async def run_after_readiness() -> object:
        readiness = await target.prepare_for_full_run()
        assert readiness.outcome == "ready"
        return await EvaluationRunner(target=target, clock=lambda: 0.0).run(
            dataset=GoldenDataset(
                schema_version=dataset.schema_version,
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.dataset_version,
                source_policy=dataset.source_policy,
                questions=(dataset.questions[0],),
            ),
            dataset_path=dataset_path,
            policy=policy,
            top_k=5,
            corpus_sha256=corpus.sha256,
        )

    summary = asyncio.run(run_after_readiness())

    assert summary.provenance.target_id == "synthetic-corpus-ollama-generation"
    assert summary.provenance.target_version == "1.4.0"
    assert summary.provenance.model_identity == "qwen3:4b"
    assert summary.records[0].retrieved_chunk_count == 5
    assert summary.records[0].matched_expected_chunk_count == 1
    assert summary.unsupported_claim_rate == 0.0
    assert len(provider.prompts) == 2
    assert provider.prompts[0] == local_generation._LOCAL_OLLAMA_READINESS_PROMPT
    assert "BEGIN UNTRUSTED RETRIEVED SOURCE" in provider.prompts[1].user
    assert "EVALUATION-ONLY DIRECT-EVIDENCE PROFILE" in provider.prompts[1].user
    assert '"synthetic-recovery-001"' in provider.prompts[1].user


@pytest.mark.anyio
async def test_local_ollama_evaluation_provider_appends_exact_fixed_controls() -> None:
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="local-ollama-marker",
        llm_model="qwen3:4b",
        llm_timeout_seconds=15,
        _env_file=None,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "model": "qwen3:4b",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "question"},
            ],
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "max_tokens": 256,
        }
        assert request.headers["authorization"] == "Bearer ollama-local"
        return httpx.Response(
            200,
            json={
                "model": "qwen3:4b",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"claims": []}'}}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    provider = _LocalOllamaEvaluationProvider(
        settings, transport=httpx.MockTransport(handler)
    )
    result = await provider.complete(ChatPrompt(system="system", user="question"))

    assert result.model == "qwen3:4b"
    assert (
        provider._timeout_seconds
        == _LOCAL_OLLAMA_EVALUATION_PROFILE.provider_timeout_seconds
    )
    assert provider._timeout_seconds == 20.0


@pytest.mark.anyio
async def test_local_ollama_provider_rejects_valid_json_when_not_stopped() -> None:
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="configured-real-key-must-not-leave-process",
        llm_model="qwen3:4b",
        _env_file=None,
    )
    provider = _LocalOllamaEvaluationProvider(
        settings,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "model": "qwen3:4b",
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"claims": []}'},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 256,
                        "total_tokens": 258,
                    },
                },
            )
        ),
    )

    with pytest.raises(ProviderOutputError) as error:
        await provider.complete(ChatPrompt(system="system", user="question"))

    assert error.value.usage == TokenUsage(
        input_tokens=2, output_tokens=256, total_tokens=258
    )


@pytest.mark.anyio
async def test_local_provider_isolates_proxies_without_changing_generic_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_options: list[dict[str, object]] = []

    class CapturingClient:
        def __init__(self, **kwargs: object) -> None:
            captured_options.append(kwargs)

        async def __aenter__(self) -> CapturingClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, *args: object, **kwargs: object) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "model": "provider-model",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "response"},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", CapturingClient)
    generic = OpenAICompatibleProvider(
        Settings(
            llm_enabled=True,
            llm_api_key="configured-generic-key",
            llm_model="generic-model",
            _env_file=None,
        )
    )
    local = _LocalOllamaEvaluationProvider(
        Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="configured-local-key",
            llm_model="qwen3:4b",
            _env_file=None,
        )
    )

    await generic.complete(ChatPrompt(system="system", user="generic"))
    await local.complete(ChatPrompt(system="system", user="local"))

    assert "trust_env" not in captured_options[0]
    assert captured_options[0]["headers"] == {
        "Authorization": "Bearer configured-generic-key"
    }
    assert captured_options[1]["trust_env"] is False
    assert captured_options[1]["headers"] == {"Authorization": "Bearer ollama-local"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reasoning_effort", "minimal"),
        ("response_format_type", "text"),
        ("max_tokens", 128),
        ("provider_timeout_seconds", 9.0),
        ("rag_max_retries", 1),
    ],
)
def test_local_ollama_provenance_hashes_each_effective_profile_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="local-ollama-marker",
        llm_model="qwen3:4b",
        _env_file=None,
    )
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=settings,
        policy_version="policy",
        policy_sha256="sha",
        run_id="profile-hash-test",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    )
    monkeypatch.setattr(
        local_generation,
        "_LOCAL_OLLAMA_EVALUATION_PROFILE",
        replace(_LOCAL_OLLAMA_EVALUATION_PROFILE, **{field: value}),
    )
    changed_target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=settings,
        policy_version="policy",
        policy_sha256="sha",
        run_id="profile-hash-test",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    )

    assert target.provenance.target_version == "1.4.0"
    assert (
        target.provenance.configuration_sha256
        != changed_target.provenance.configuration_sha256
    )


def test_local_ollama_target_wires_and_fingerprints_direct_evidence_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="local-ollama-marker",
        llm_model="qwen3:4b",
        _env_file=None,
    )
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=settings,
        policy_version="policy",
        policy_sha256="sha",
        run_id="direct-evidence-profile-test",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    )
    monkeypatch.setattr(
        local_generation.RagPromptBuilder,
        "_DIRECT_EVIDENCE_USER_INSTRUCTION",
        "Changed direct-evidence instruction.",
    )
    changed_target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=settings,
        policy_version="policy",
        policy_sha256="sha",
        run_id="direct-evidence-profile-test",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    )

    assert target.provenance.target_version == "1.4.0"
    assert (
        target.provenance.configuration_sha256
        != changed_target.provenance.configuration_sha256
    )


@pytest.mark.anyio
async def test_full_local_target_runs_one_generic_readiness_call_on_same_provider() -> (
    None
):
    class ReadinessProvider:
        def __init__(self) -> None:
            self.prompts: list[ChatPrompt] = []

        async def complete(self, prompt: ChatPrompt) -> ChatResult:
            self.prompts.append(prompt)
            return ChatResult(
                answer='{"status":"ready"}',
                model="qwen3:4b",
                usage=TokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                finish_reason="stop",
            )

    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    provider = ReadinessProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="readiness-full-run",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        provider=provider,
    )

    record = await target.prepare_for_full_run()

    assert record == LocalOllamaReadinessRecord(
        event="stage8_local_readiness",
        outcome="ready",
        duration_ms=record.duration_ms,
        model="qwen3:4b",
        profile="generic_completion_v1",
        correlation="readiness-full-run",
    )
    assert record.duration_ms >= 0
    assert len(provider.prompts) == 1
    assert provider.prompts[0] == local_generation._LOCAL_OLLAMA_READINESS_PROMPT
    assert "synthetic-" not in provider.prompts[0].system
    assert "synthetic-" not in provider.prompts[0].user
    assert "G001" not in provider.prompts[0].system
    assert "G001" not in provider.prompts[0].user
    assert set(field.split("=", 1)[0] for field in record.render().split()) == {
        "event",
        "outcome",
        "duration_ms",
        "model",
        "profile",
        "correlation",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result_or_error",
    [
        ChatResult(
            answer="secret-readiness-answer-must-not-be-retained",
            model="other-model",
            usage=TokenUsage(input_tokens=9, output_tokens=4, total_tokens=13),
            finish_reason="stop",
        ),
        ProviderTimeoutError("provider-message-must-not-be-exposed"),
    ],
)
async def test_full_local_target_fails_readiness_safely(
    result_or_error: ChatResult | Exception,
) -> None:
    class FailingReadinessProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            if isinstance(result_or_error, Exception):
                raise result_or_error
            return result_or_error

    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    provider = FailingReadinessProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="safe-readiness-failure",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        provider=provider,
    )

    record = await target.prepare_for_full_run()

    assert provider.calls == 1
    assert record.outcome == "failed"
    assert "secret-readiness-answer-must-not-be-retained" not in record.render()
    assert "provider-message-must-not-be-exposed" not in record.render()


@pytest.mark.anyio
async def test_full_local_target_blocks_evaluation_before_readiness_side_effects() -> (
    None
):
    class TrackingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            raise AssertionError("provider must not run before readiness")

    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    provider = TrackingProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="readiness-required",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        provider=provider,
    )
    retrieval_calls: list[str] = []

    def search_must_not_run(*, query: str, top_k: int) -> list[RetrievedChunk]:
        retrieval_calls.append(f"{query}:{top_k}")
        pytest.fail("retrieval must not run before readiness")

    target._retrieval_service.search = search_must_not_run

    with pytest.raises(ValueError, match="successful readiness"):
        await target.evaluate(
            GoldenQuestion(
                "G001",
                "What is the synthetic recovery target?",
                ("synthetic-recovery-001",),
                "synthetic",
                "reviewed",
                "Four hours.",
            )
        )

    assert provider.calls == 0
    assert retrieval_calls == []


@pytest.mark.anyio
async def test_full_local_target_rejects_duplicate_readiness_without_second_call() -> (
    None
):
    class ReadyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            return ChatResult(
                answer='{"status":"ready"}',
                model="qwen3:4b",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                finish_reason="stop",
            )

    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    provider = ReadyProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="readiness-once",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        provider=provider,
    )

    assert (await target.prepare_for_full_run()).outcome == "ready"
    with pytest.raises(ValueError, match="only once"):
        await target.prepare_for_full_run()

    assert provider.calls == 1


@pytest.mark.anyio
async def test_full_local_target_rejects_duplicate_after_failed_readiness() -> None:
    class FailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            raise ProviderTimeoutError("do-not-repeat-readiness")

    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    provider = FailingProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="failed-readiness-once",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        provider=provider,
    )

    assert (await target.prepare_for_full_run()).outcome == "failed"
    with pytest.raises(ValueError, match="only once"):
        await target.prepare_for_full_run()

    assert provider.calls == 1


def test_single_question_smoke_excludes_readiness_and_uses_historical_target() -> None:
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="historical-smoke",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        readiness_required=False,
    )

    assert target.provenance.target_version == "1.3.2"
    with pytest.raises(ValueError, match="full-dataset"):
        asyncio.run(target.prepare_for_full_run())


def test_readiness_preflight_factory_constructs_only_the_local_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReadyProvider:
        async def complete(self, _: ChatPrompt) -> ChatResult:
            return ChatResult(
                answer='{"ready":true}',
                model="qwen3:4b",
                usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                finish_reason="stop",
            )

    def evaluation_component_must_not_be_constructed(*_: object, **__: object) -> None:
        pytest.fail("readiness preflight must not construct an evaluation component")

    monkeypatch.setattr(
        local_generation,
        "_LocalOllamaEvaluationProvider",
        lambda _: ReadyProvider(),
    )
    monkeypatch.setattr(
        local_generation,
        "SyntheticCorpusRagRetrievalService",
        evaluation_component_must_not_be_constructed,
    )
    monkeypatch.setattr(
        local_generation, "RagService", evaluation_component_must_not_be_constructed
    )
    monkeypatch.setattr(
        local_generation, "ContextBuilder", evaluation_component_must_not_be_constructed
    )
    monkeypatch.setattr(
        local_generation,
        "CitationOutputParser",
        evaluation_component_must_not_be_constructed,
    )
    monkeypatch.setattr(
        local_generation,
        "CitationValidator",
        evaluation_component_must_not_be_constructed,
    )

    preflight = build_local_ollama_readiness_preflight(
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        run_id="preflight-isolated",
        top_k=5,
    )
    record = asyncio.run(preflight.prepare())

    assert record.outcome == "ready"
    assert record.model == "qwen3:4b"
    assert record.correlation == "preflight-isolated"


def test_readiness_preflight_factory_fails_safely_and_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider:
        async def complete(self, _: ChatPrompt) -> ChatResult:
            raise ProviderTimeoutError("secret provider failure")

    monkeypatch.setattr(
        local_generation,
        "_LocalOllamaEvaluationProvider",
        lambda _: FailingProvider(),
    )
    preflight = build_local_ollama_readiness_preflight(
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        run_id="preflight-safe-failure",
        top_k=5,
    )

    record = asyncio.run(preflight.prepare())

    assert record.outcome == "failed"
    assert "secret provider failure" not in record.render()
    with pytest.raises(ValueError, match="only once"):
        asyncio.run(preflight.prepare())


@pytest.mark.parametrize(
    ("settings_overrides", "run_id", "top_k", "message"),
    (
        ({"llm_model": "model with spaces"}, "preflight-valid", 5, "model_identity"),
        (
            {"llm_base_url": "http://localhost:11434/v1"},
            "preflight-valid",
            5,
            "127.0.0.1",
        ),
        ({}, "run id with spaces", 5, "run_id"),
        ({}, "preflight-valid", 4, "--top-k"),
    ),
)
def test_readiness_preflight_factory_validates_its_only_inputs_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    settings_overrides: dict[str, object],
    run_id: str,
    top_k: int,
    message: str,
) -> None:
    def provider_must_not_be_constructed(_: Settings) -> _LocalOllamaEvaluationProvider:
        pytest.fail("invalid readiness preflight input must not construct a provider")

    monkeypatch.setattr(
        local_generation,
        "_LocalOllamaEvaluationProvider",
        provider_must_not_be_constructed,
    )
    settings_values: dict[str, object] = {
        "llm_enabled": True,
        "llm_base_url": "http://127.0.0.1:11434/v1",
        "llm_api_key": "local-ollama-marker",
        "llm_model": "qwen3:4b",
    }
    settings_values.update(settings_overrides)
    settings = Settings(_env_file=None, **settings_values)

    with pytest.raises(ValueError, match=message):
        build_local_ollama_readiness_preflight(
            settings=settings,
            run_id=run_id,
            top_k=top_k,
        )


def test_full_local_readiness_protocol_changes_provenance_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="local-ollama-marker",
        llm_model="qwen3:4b",
        _env_file=None,
    )
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=settings,
        policy_version="policy",
        policy_sha256="sha",
        run_id="readiness-fingerprint",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    )
    monkeypatch.setattr(
        local_generation,
        "_LOCAL_OLLAMA_READINESS_PROMPT",
        ChatPrompt(system="different static system", user="different static user"),
    )
    changed_target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=settings,
        policy_version="policy",
        policy_sha256="sha",
        run_id="readiness-fingerprint",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
    )

    assert target.provenance.target_version == "1.4.0"
    assert (
        target.provenance.configuration_sha256
        != changed_target.provenance.configuration_sha256
    )


def test_local_ollama_profile_records_malformed_and_cap_truncated_output_safely() -> (
    None
):
    class MalformedProvider:
        def __init__(self, answer: str) -> None:
            self.answer = answer
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(
                    answer='{"status":"ready"}',
                    model="qwen3:4b",
                    usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    finish_reason="stop",
                )
            return ChatResult(
                answer=self.answer,
                model="qwen3:4b",
                usage=TokenUsage(input_tokens=1, output_tokens=256, total_tokens=257),
            )

    corpus_path = Path("docs/evaluation/synthetic_corpus_v1.json")
    dataset_path = Path("docs/evaluation/golden_dataset_v1.json")
    corpus = load_synthetic_corpus(corpus_path)
    dataset = load_dataset(dataset_path)
    policy = load_regression_policy(Path("docs/evaluation/baseline_policy_v1.json"))
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="local-ollama-marker",
        llm_model="qwen3:4b",
        _env_file=None,
    )
    for answer in ('{"claims":', '{"claims": [{"text": "truncated"'):
        provider = MalformedProvider(answer)
        target = build_synthetic_ollama_generation_target(
            corpus=corpus,
            settings=settings,
            policy_version=policy.policy_version,
            policy_sha256=policy.sha256,
            run_id="malformed-local-output-test",
            top_k=5,
            policy_corpus_sha256=policy.synthetic_corpus_sha256,
            provider=provider,
        )
        readiness = asyncio.run(target.prepare_for_full_run())
        assert readiness.outcome == "ready"
        summary = asyncio.run(
            EvaluationRunner(target=target, clock=lambda: 0.0).run(
                dataset=GoldenDataset(
                    schema_version=dataset.schema_version,
                    dataset_id=dataset.dataset_id,
                    dataset_version=dataset.dataset_version,
                    source_policy=dataset.source_policy,
                    questions=(dataset.questions[0],),
                ),
                dataset_path=dataset_path,
                policy=policy,
                top_k=5,
                corpus_sha256=corpus.sha256,
            )
        )
        assert summary.failures == 1
        assert summary.records[0].failure_kind == "RagOutputError"
        assert summary.records[0].input_tokens == 1
        assert summary.records[0].output_tokens == 256
        assert summary.records[0].total_tokens == 257
        assert summary.records[0].retrieved_chunk_count == 5
        assert summary.records[0].matched_expected_chunk_count == 1
        assert summary.records[0].claim_count is None
        assert summary.records[0].unsupported_claim_count is None
        assert provider.calls == 2


def test_local_ollama_profile_makes_one_attempt_for_timeout_or_retryable_error() -> (
    None
):
    class UnavailableProvider:
        def __init__(self, error: Exception) -> None:
            self.error = error
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(
                    answer='{"status":"ready"}',
                    model="qwen3:4b",
                    usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    finish_reason="stop",
                )
            raise self.error

    corpus_path = Path("docs/evaluation/synthetic_corpus_v1.json")
    dataset_path = Path("docs/evaluation/golden_dataset_v1.json")
    corpus = load_synthetic_corpus(corpus_path)
    dataset = load_dataset(dataset_path)
    policy = load_regression_policy(Path("docs/evaluation/baseline_policy_v1.json"))
    settings = Settings(
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434/v1",
        llm_api_key="local-ollama-marker",
        llm_model="qwen3:4b",
        llm_max_retries=2,
        _env_file=None,
    )
    for error in (
        ProviderTimeoutError("timed out"),
        ProviderRetryableError("temporary failure"),
    ):
        provider = UnavailableProvider(error)
        target = build_synthetic_ollama_generation_target(
            corpus=corpus,
            settings=settings,
            policy_version=policy.policy_version,
            policy_sha256=policy.sha256,
            run_id="unavailable-local-provider-test",
            top_k=5,
            policy_corpus_sha256=policy.synthetic_corpus_sha256,
            provider=provider,
        )
        readiness = asyncio.run(target.prepare_for_full_run())
        assert readiness.outcome == "ready"
        summary = asyncio.run(
            EvaluationRunner(target=target, clock=lambda: 0.0).run(
                dataset=GoldenDataset(
                    schema_version=dataset.schema_version,
                    dataset_id=dataset.dataset_id,
                    dataset_version=dataset.dataset_version,
                    source_policy=dataset.source_policy,
                    questions=(dataset.questions[0],),
                ),
                dataset_path=dataset_path,
                policy=policy,
                top_k=5,
                corpus_sha256=corpus.sha256,
            )
        )
        assert provider.calls == 2
        assert summary.failures == 1
        assert summary.records[0].failure_kind == "RagUnavailableError"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://example.invalid/v1",
        "http://localhost:11434/v1",
        "http://[::1]:11434/v1",
        "http://127.0.0.1:11434/v1/other",
        "http://127.0.0.1:11434/v1?proxy=1",
    ],
)
def test_synthetic_ollama_target_rejects_noncanonical_configuration(
    base_url: str,
) -> None:
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    with pytest.raises(ValueError, match="local Ollama"):
        build_synthetic_ollama_generation_target(
            corpus=corpus,
            settings=Settings(
                llm_enabled=True,
                llm_base_url=base_url,
                llm_api_key="not-used",
                llm_model="qwen3:4b",
                _env_file=None,
            ),
            policy_version="policy",
            policy_sha256="sha",
            run_id="run",
            top_k=5,
            policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        )


def test_synthetic_ollama_target_withholds_provider_call_without_evidence() -> None:
    class FailingProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, _: ChatPrompt) -> ChatResult:
            self.calls += 1
            if self.calls == 1:
                return ChatResult(
                    answer='{"status":"ready"}',
                    model="qwen3:4b",
                    usage=TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    finish_reason="stop",
                )
            raise AssertionError("provider must not be called without evidence")

    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    provider = FailingProvider()
    target = build_synthetic_ollama_generation_target(
        corpus=corpus,
        settings=Settings(
            llm_enabled=True,
            llm_base_url="http://127.0.0.1:11434/v1",
            llm_api_key="local-ollama-marker",
            llm_model="qwen3:4b",
            _env_file=None,
        ),
        policy_version="policy",
        policy_sha256="sha",
        run_id="no-evidence-test",
        top_k=5,
        policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        provider=provider,
    )

    async def evaluate_after_readiness() -> EvaluationObservation:
        readiness = await target.prepare_for_full_run()
        assert readiness.outcome == "ready"
        return await target.evaluate(
            GoldenQuestion(
                "no-evidence",
                "zzzz-not-in-the-synthetic-corpus",
                ("synthetic-recovery-001",),
                "synthetic",
                "reviewed",
                "No matching synthetic evidence.",
            )
        )

    observation = asyncio.run(evaluate_after_readiness())

    assert observation.model is None
    assert observation.usage == TokenUsage(
        input_tokens=0, output_tokens=0, total_tokens=0
    )
    assert observation.claim_count == 0
    assert provider.calls == 1


def test_synthetic_ollama_target_rejects_altered_approved_corpus_before_provider() -> (
    None
):
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    with pytest.raises(ValueError, match="approved SHA-256"):
        build_synthetic_ollama_generation_target(
            corpus=replace(corpus, sha256="0" * 64),
            settings=Settings(
                llm_enabled=True,
                llm_base_url="http://127.0.0.1:11434/v1",
                llm_api_key="local-ollama-marker",
                llm_model="qwen3:4b",
                _env_file=None,
            ),
            policy_version="policy",
            policy_sha256="sha",
            run_id="altered-corpus-test",
            top_k=5,
            policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        )


def test_retrieval_metric_formulas_handle_duplicates_missing_and_empty_results() -> (
    None
):
    observations = (
        _observation("q-1", ("chunk-1", "chunk-1", "noise")),
        _observation("q-2", None),
    )

    result = retrieval_metrics(
        observations=observations,
        expected_chunk_ids=(("chunk-1", "chunk-2"), ("chunk-3",)),
        top_k=2,
    )

    assert result.evaluated_questions == 2
    assert result.missing_results == 1
    assert result.recall_at_k == 0.25
    assert result.hit_rate_at_k == 0.5
    assert result.precision_at_k == 0.25
    assert result.mrr == 0.5
    assert percentile_nearest_rank([3.0, 1.0, 2.0, 99.0], 50) == 2.0
    assert percentile_nearest_rank([], 95) is None


def test_runner_replay_is_stable_and_unreviewed_human_metrics_are_unavailable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.json"
    dataset = _dataset(path)
    fixture = {
        "q-1": _observation("q-1", ("noise", "chunk-2")),
        "q-2": _observation("q-2", ("chunk-3",), claims=0, unsupported=0),
    }
    ticks = iter((0.0, 0.002, 0.003, 0.007, 0.0, 0.002, 0.003, 0.007))
    runner = EvaluationRunner(
        target=StaticFixtureTarget(fixture), clock=lambda: next(ticks)
    )

    first = asyncio.run(
        runner.run(dataset=dataset, dataset_path=path, policy=TEST_POLICY, top_k=2)
    )
    second = asyncio.run(
        EvaluationRunner(
            target=StaticFixtureTarget(fixture),
            clock=lambda: next(ticks),
        ).run(dataset=dataset, dataset_path=path, policy=TEST_POLICY, top_k=2)
    )

    assert first == second
    assert first.retrieval.recall_at_k == 0.75
    assert first.unsupported_claim_rate == 0.0
    assert first.correctness is None
    assert first.faithfulness is None
    assert first.citation_accuracy is None
    assert first.estimated_cost_usd is None
    assert (
        first.cost_unavailable_reason
        == "no versioned provider pricing schedule supplied"
    )


def test_human_calibration_and_regression_thresholds_are_explicit(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.json"
    dataset = _dataset(path)
    fixture = {
        "q-1": _observation("q-1", ("chunk-1", "chunk-2")),
        "q-2": _observation("q-2", ("chunk-3",)),
    }
    reviews = (
        HumanAssessment("q-1", True, True, True, "reviewer-a"),
        HumanAssessment("q-2", False, None, True, "reviewer-a"),
    )
    baseline = asyncio.run(
        EvaluationRunner(target=StaticFixtureTarget(fixture), clock=lambda: 0.0).run(
            dataset=dataset,
            dataset_path=path,
            policy=TEST_POLICY,
            top_k=2,
        )
    )
    candidate = asyncio.run(
        EvaluationRunner(
            target=StaticFixtureTarget(
                {"q-1": _observation("q-1", ("noise",)), "q-2": fixture["q-2"]}
            ),
            clock=lambda: 0.0,
        ).run(
            dataset=dataset,
            dataset_path=path,
            policy=TEST_POLICY,
            top_k=2,
        )
    )

    findings = compare_to_baseline(
        baseline=baseline,
        candidate=candidate,
        policy=RegressionPolicy(
            policy_version="test-policy",
            dataset_id="test-golden",
            dataset_version="1.0.0",
            thresholds=RegressionThresholds(max_retrieval_metric_drop=0.02),
            sha256="test-policy-sha",
        ),
    )

    assert human_review_rate(reviews, "correctness") == 0.5
    assert human_review_rate(reviews, "faithfulness") is None
    assert human_review_rate(reviews, "citation_accuracy") == 1.0
    assert (
        next(item for item in findings if item.metric == "recall_at_k").status == "fail"
    )
    assert (
        next(item for item in findings if item.metric == "faithfulness").status
        == "not_evaluated"
    )
    with pytest.raises(ValueError, match="multipliers"):
        RegressionThresholds(max_total_tokens_multiplier=0.9)


def test_artifacts_exclude_question_and_document_content(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    dataset = _dataset(path)
    summary = asyncio.run(
        EvaluationRunner(
            target=StaticFixtureTarget(
                {
                    "q-1": _observation("q-1", ("chunk-1",)),
                    "q-2": _observation("q-2", ("chunk-3",)),
                }
            ),
            clock=lambda: 0.0,
        ).run(dataset=dataset, dataset_path=path, policy=TEST_POLICY, top_k=2)
    )
    json_path = tmp_path / "artifact.json"
    csv_path = tmp_path / "artifact.csv"

    write_artifacts(summary=summary, json_path=json_path, csv_path=csv_path)

    artifact = json_path.read_text(encoding="utf-8") + csv_path.read_text(
        encoding="utf-8"
    )
    assert "Synthetic question" not in artifact
    assert "chunk-1" not in artifact
    assert "q-1" in artifact
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"][
        "evaluation_scope"
    ] == ("full_dataset")


def test_generation_source_artifact_accepts_safe_run_and_model_identifiers(
    tmp_path: Path,
) -> None:
    run_id = "reviewed-run_1.2:local"
    source_path, dataset, policy = _eligible_generation_source(tmp_path, run_id=run_id)

    source = load_generation_source_artifact(
        source_path,
        dataset=dataset,
        dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
        policy=policy,
    )

    assert source.summary.provenance.run_id == run_id
    assert source.summary.provenance.model_identity == "model-test"


@pytest.mark.parametrize("run_id", ("content-like run id", "a" * 129))
def test_cli_rejects_invalid_live_run_id_before_settings_target_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_id: str
) -> None:
    def settings_must_not_be_constructed() -> Settings:
        pytest.fail("invalid run ID must not construct Settings")

    def target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("invalid run ID must not construct a target")

    output_json = tmp_path / "result.json"
    output_csv = tmp_path / "result.csv"
    monkeypatch.setattr(cli, "Settings", settings_must_not_be_constructed)
    monkeypatch.setattr(
        cli,
        "build_synthetic_ollama_generation_target",
        target_must_not_be_constructed,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--run-id",
            run_id,
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2
    assert not output_json.exists()
    assert not output_csv.exists()


@pytest.mark.parametrize("model_identity", ("content-like model", "m" * 129))
def test_generation_target_rejects_invalid_model_identity_before_provider_construction(
    monkeypatch: pytest.MonkeyPatch, model_identity: str
) -> None:
    corpus = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))

    def configuration_must_not_be_checked(_: Settings) -> None:
        pytest.fail("invalid model identity must not construct a provider")

    monkeypatch.setattr(
        local_generation,
        "_require_local_ollama_configuration",
        configuration_must_not_be_checked,
    )

    with pytest.raises(
        ValueError, match="model_identity must be a bounded safe identifier"
    ):
        build_synthetic_ollama_generation_target(
            corpus=corpus,
            settings=Settings(llm_model=model_identity),
            policy_version="policy-v1",
            policy_sha256="a" * 64,
            run_id="reviewed-run",
            top_k=5,
            policy_corpus_sha256=APPROVED_LOCAL_OLLAMA_CORPUS_SHA256,
        )


def test_offline_scoring_binds_full_review_to_exact_raw_generation_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    assessment_path = tmp_path / "assessment-v2.json"
    assessment_path.write_text(
        json.dumps(
            _assessment_payload(source_path=source_path, dataset=dataset, policy=policy)
        ),
        encoding="utf-8",
    )
    output_json = tmp_path / "scored.json"
    output_csv = tmp_path / "scored.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--policy",
            "docs/evaluation/baseline_policy_v1.json",
            "--assessments",
            str(assessment_path),
            "--assessment-source-artifact",
            str(source_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    assert cli.main() == 0
    scored_artifact = json.loads(output_json.read_text(encoding="utf-8"))
    assert scored_artifact["artifact_kind"] == "stage8_evaluation_result"
    scored = scored_artifact["summary"]
    assert scored["correctness"] == 1.0
    assert scored["faithfulness"] == 1.0
    assert scored["citation_accuracy"] == 1.0
    assert (
        scored["provenance"]["assessment_artifact_sha256"]
        == hashlib.sha256(assessment_path.read_bytes()).hexdigest()
    )
    assert (
        scored["provenance"]["assessment_source_artifact_sha256"]
        == hashlib.sha256(source_path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "target_version", ["1.2.0", "1.3.0", "1.3.1", "1.3.2", "1.4.0"]
)
def test_strict_eligible_generation_source_writes_loads_and_scores_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_version: str
) -> None:
    source_path, dataset, policy = _eligible_generation_source(
        tmp_path, target_version=target_version
    )
    source_artifact = json.loads(source_path.read_text(encoding="utf-8"))
    assert source_artifact["artifact_kind"] == "stage8_generation_source"
    source = load_generation_source_artifact(
        source_path,
        dataset=dataset,
        dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
        policy=policy,
    )
    assert source.summary.provenance.target_version == target_version
    assessment_path = tmp_path / "assessment-v2.json"
    assessment_path.write_text(
        json.dumps(
            _assessment_payload(source_path=source_path, dataset=dataset, policy=policy)
        ),
        encoding="utf-8",
    )
    output_json = tmp_path / "scored.json"
    output_csv = tmp_path / "scored.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--assessments",
            str(assessment_path),
            "--assessment-source-artifact",
            str(source_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    assert cli.main() == 0
    assert (
        json.loads(output_json.read_text(encoding="utf-8"))["summary"]["provenance"][
            "target_version"
        ]
        == target_version
    )


def test_source_writer_and_loader_reject_unreviewed_generation_target_version(
    tmp_path: Path,
) -> None:
    source_path, dataset, policy = _eligible_generation_source(
        tmp_path, target_version="1.4.1"
    )
    written = json.loads(source_path.read_text(encoding="utf-8"))

    assert written["artifact_kind"] == "stage8_evaluation_result"
    written["artifact_kind"] = "stage8_generation_source"
    source_path.write_text(json.dumps(written), encoding="utf-8")
    with pytest.raises(ValueError, match="eligible"):
        load_generation_source_artifact(
            source_path,
            dataset=dataset,
            dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
            policy=policy,
        )


@pytest.mark.parametrize("field", ["source_artifact_sha256", "source_run_id"])
def test_offline_scoring_rejects_stale_run_or_hash_binding_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    payload = _assessment_payload(
        source_path=source_path, dataset=dataset, policy=policy
    )
    source = payload["source"]
    assert isinstance(source, dict)
    source[field] = "b" * 64 if field.endswith("sha256") else "other-run"
    assessment_path = tmp_path / f"stale-{field}.json"
    assessment_path.write_text(json.dumps(payload), encoding="utf-8")
    output_json = tmp_path / "untouched.json"
    output_csv = tmp_path / "untouched.csv"
    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: pytest.fail("offline scoring must not construct settings/provider"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--assessments",
            str(assessment_path),
            "--assessment-source-artifact",
            str(source_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()
    assert exit_code.value.code == 2
    assert not output_json.exists()
    assert not output_csv.exists()


def test_source_reader_rejects_forged_aggregate_and_legacy_content(
    tmp_path: Path,
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    payload["summary"]["total_tokens"] += 1
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="aggregates"):
        load_generation_source_artifact(
            forged,
            dataset=dataset,
            dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
            policy=policy,
        )
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(payload["summary"]), encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected"):
        load_generation_source_artifact(
            legacy,
            dataset=dataset,
            dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
            policy=policy,
        )


def test_offline_scoring_rejects_path_collision_before_any_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps(
            _assessment_payload(source_path=source_path, dataset=dataset, policy=policy)
        ),
        encoding="utf-8",
    )
    original_source = source_path.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--assessments",
            str(assessment_path),
            "--assessment-source-artifact",
            str(source_path),
            "--output-json",
            str(source_path),
            "--output-csv",
            str(tmp_path / "other.csv"),
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()
    assert exit_code.value.code == 2
    assert source_path.read_bytes() == original_source


@pytest.mark.parametrize("input_name", ["dataset", "policy", "assessments", "source"])
def test_offline_scoring_rejects_hardlinked_input_output_before_any_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, input_name: str
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps(
            _assessment_payload(source_path=source_path, dataset=dataset, policy=policy)
        ),
        encoding="utf-8",
    )
    dataset_input = tmp_path / "golden-dataset.json"
    dataset_input.write_bytes(
        Path("docs/evaluation/golden_dataset_v1.json").read_bytes()
    )
    policy_input = tmp_path / "baseline-policy.json"
    policy_input.write_bytes(
        Path("docs/evaluation/baseline_policy_v1.json").read_bytes()
    )
    inputs = {
        "dataset": dataset_input,
        "policy": policy_input,
        "assessments": assessment_path,
        "source": source_path,
    }
    original_input = inputs[input_name]
    original_bytes = original_input.read_bytes()
    output_json = tmp_path / f"hardlinked-{input_name}.json"
    _hardlink_or_skip(original_input, output_json)
    output_csv = tmp_path / "result.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            str(inputs["dataset"]),
            "--policy",
            str(inputs["policy"]),
            "--assessments",
            str(inputs["assessments"]),
            "--assessment-source-artifact",
            str(inputs["source"]),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2
    assert original_input.read_bytes() == original_bytes
    assert not output_csv.exists()


def test_offline_scoring_rejects_hardlinked_json_and_csv_outputs_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    assessment_path = tmp_path / "assessment.json"
    assessment_path.write_text(
        json.dumps(
            _assessment_payload(source_path=source_path, dataset=dataset, policy=policy)
        ),
        encoding="utf-8",
    )
    sentinel = tmp_path / "output-sentinel"
    sentinel.write_bytes(b"existing-output-must-remain-unchanged")
    output_json = tmp_path / "result.json"
    output_csv = tmp_path / "result.csv"
    _hardlink_or_skip(sentinel, output_json)
    _hardlink_or_skip(sentinel, output_csv)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--assessments",
            str(assessment_path),
            "--assessment-source-artifact",
            str(source_path),
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2
    assert sentinel.read_bytes() == b"existing-output-must-remain-unchanged"


@pytest.mark.parametrize(
    "target_name",
    ("fixture", "synthetic-corpus", "synthetic-ollama-corpus"),
)
@pytest.mark.parametrize("collision_kind", ("equal", "symlink", "hardlink"))
def test_live_replay_rejects_target_input_output_collisions_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_name: str,
    collision_kind: str,
) -> None:
    """Every live target remains intact when an output aliases its input file."""

    if target_name == "fixture":
        target_input = tmp_path / "fixture.json"
        target_input.write_bytes(b"fixture-input-must-remain-unchanged")
        target_arguments = ("--fixture", str(target_input))
    elif target_name == "synthetic-corpus":
        target_input = tmp_path / "synthetic-corpus.json"
        target_input.write_bytes(b"corpus-input-must-remain-unchanged")
        target_arguments = ("--synthetic-corpus", str(target_input))
    else:
        target_input = Path("docs/evaluation/synthetic_corpus_v1.json").resolve()
        target_arguments = (
            "--synthetic-ollama-corpus",
            str(target_input),
            "--allow-local-ollama",
        )

        def settings_must_not_be_constructed() -> Settings:
            pytest.fail("invalid Ollama path collision must not construct Settings")

        def target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
            pytest.fail("invalid Ollama path collision must not construct a target")

        monkeypatch.setattr(cli, "Settings", settings_must_not_be_constructed)
        monkeypatch.setattr(
            cli,
            "build_synthetic_ollama_generation_target",
            target_must_not_be_constructed,
        )

    original_bytes = target_input.read_bytes()
    output_directory = (
        target_input.parent if target_name == "synthetic-ollama-corpus" else tmp_path
    )
    output_json = output_directory / (
        f"{tmp_path.name}-{target_name}-{collision_kind}.json"
    )
    if collision_kind == "equal":
        output_json = target_input
    elif collision_kind == "symlink":
        _symlink_or_skip(target_input, output_json)
    else:
        _hardlink_or_skip(target_input, output_json)
    output_csv = output_directory / (
        f"{tmp_path.name}-{target_name}-{collision_kind}.csv"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            *target_arguments,
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--run-id",
            f"{target_name}-{collision_kind}-collision",
        ],
    )

    try:
        with pytest.raises(SystemExit) as exit_code:
            cli.main()

        assert exit_code.value.code == 2
        assert target_input.read_bytes() == original_bytes
        assert not output_csv.exists()
    finally:
        if output_json != target_input:
            output_json.unlink(missing_ok=True)
        output_csv.unlink(missing_ok=True)


def test_artifact_writer_rejects_hardlinked_outputs_without_overwrite(
    tmp_path: Path,
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    summary = load_generation_source_artifact(
        source_path,
        dataset=dataset,
        dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
        policy=policy,
    ).summary
    sentinel = tmp_path / "writer-sentinel"
    sentinel.write_bytes(b"artifact-writer-must-not-overwrite")
    output_json = tmp_path / "writer-result.json"
    output_csv = tmp_path / "writer-result.csv"
    _hardlink_or_skip(sentinel, output_json)
    _hardlink_or_skip(sentinel, output_csv)

    with pytest.raises(ValueError, match="distinct"):
        write_artifacts(summary=summary, json_path=output_json, csv_path=output_csv)

    assert sentinel.read_bytes() == b"artifact-writer-must-not-overwrite"


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("dataset_id", "other-dataset"),
        ("dataset_version", "9.9.9"),
        ("dataset_sha256", "b" * 64),
        ("corpus_sha256", "b" * 64),
        ("top_k", 6),
        ("target_id", "other-target"),
        ("target_version", "9.9.9"),
        ("model_identity", "other-model"),
        ("configuration_sha256", "b" * 64),
        ("policy_version", "other-policy"),
        ("policy_sha256", "b" * 64),
    ],
)
def test_assessment_binding_rejects_every_non_run_identity_mismatch(
    tmp_path: Path, field: str, replacement: object
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    payload = _assessment_payload(
        source_path=source_path, dataset=dataset, policy=policy
    )
    binding = payload["source"]
    assert isinstance(binding, dict)
    binding[field] = replacement
    assessment_path = tmp_path / f"mismatch-{field}.json"
    assessment_path.write_text(json.dumps(payload), encoding="utf-8")
    source = load_generation_source_artifact(
        source_path,
        dataset=dataset,
        dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
        policy=policy,
    )

    with pytest.raises(ValueError, match="binding"):
        apply_assessment_bundle(
            source=source, bundle=load_assessments(assessment_path), dataset=dataset
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("artifact_kind", "stage8_evaluation_result"), "kind"),
        (("summary", "evaluation_scope", "single_question_smoke"), "full-dataset"),
        (
            ("summary", "provenance", "target_id", "static-metric-plumbing-fixture"),
            "eligible",
        ),
        (("summary", "records", 0, "latency_ms", True), "finite"),
    ],
)
def test_source_reader_rejects_scope_target_and_malformed_content_free_values(
    tmp_path: Path, mutation: tuple[object, ...], message: str
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    cursor: object = payload
    path = mutation[:-1]
    for key in path[:-1]:
        assert isinstance(cursor, (dict, list))
        cursor = cursor[key]  # type: ignore[index]
    assert isinstance(cursor, dict)
    cursor[path[-1]] = mutation[-1]
    invalid_path = tmp_path / f"invalid-{message}.json"
    invalid_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_generation_source_artifact(
            invalid_path,
            dataset=dataset,
            dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
            policy=policy,
        )


def test_partial_metric_verdict_is_null_and_regression_rejects_incomplete_state(
    tmp_path: Path,
) -> None:
    source_path, dataset, policy = _eligible_generation_source(tmp_path)
    payload = _assessment_payload(
        source_path=source_path, dataset=dataset, policy=policy
    )
    assessments = payload["assessments"]
    assert isinstance(assessments, list)
    assessments[0]["faithfulness"] = None
    assessment_path = tmp_path / "partial.json"
    assessment_path.write_text(json.dumps(payload), encoding="utf-8")
    source = load_generation_source_artifact(
        source_path,
        dataset=dataset,
        dataset_path=Path("docs/evaluation/golden_dataset_v1.json"),
        policy=policy,
    )
    scored = apply_assessment_bundle(
        source=source, bundle=load_assessments(assessment_path), dataset=dataset
    )
    assert scored.correctness == 1.0
    assert scored.faithfulness is None
    assert scored.provenance.assessment_artifact_sha256 is not None
    with pytest.raises(ValueError, match="complete pair"):
        compare_to_baseline(
            baseline=replace(
                scored,
                provenance=replace(scored.provenance, assessment_artifact_sha256=None),
            ),
            candidate=scored,
            policy=policy,
        )


def test_assessment_schema_cost_and_observation_validation(tmp_path: Path) -> None:
    assessment_path = tmp_path / "assessments.json"
    assessment_path.write_text(
        json.dumps({"schema_version": 1, "assessments": []}), encoding="utf-8"
    )
    with pytest.raises(DatasetValidationError):
        load_assessments(assessment_path)
    fixture_value = {
        "question_id": "q-1",
        "retrieved_chunk_ids": ["chunk-1"],
        "model": "model-a",
        "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
        "claim_count": 1,
        "unsupported_claim_count": 0,
        "failure_kind": None,
    }
    observation = observation_from_fixture(fixture_value)
    dataset_path = tmp_path / "dataset.json"
    dataset = _dataset(dataset_path)
    summary = asyncio.run(
        EvaluationRunner(
            target=StaticFixtureTarget(
                {"q-1": observation, "q-2": _observation("q-2", ("chunk-3",))}
            ),
            clock=lambda: 0.0,
        ).run(
            dataset=dataset,
            dataset_path=dataset_path,
            policy=TEST_POLICY,
            top_k=2,
            cost_per_1k_tokens_usd={
                "model-a": (1.0, 2.0),
                "local-test-model": (1.0, 2.0),
            },
        )
    )

    assert summary.estimated_cost_usd is not None
    assert summary.cost_unavailable_reason is None
    with pytest.raises(ValueError, match="claim counts"):
        observation_from_fixture({**fixture_value, "unsupported_claim_count": 2})

    assessment_path.write_text("{}", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="unexpected"):
        load_assessments(assessment_path)


def test_runner_records_missing_fixture_and_question_id_mismatch(
    tmp_path: Path,
) -> None:
    path = tmp_path / "dataset.json"
    dataset = _dataset(path)
    missing = asyncio.run(
        EvaluationRunner(
            target=StaticFixtureTarget({"q-1": _observation("q-1", ("chunk-1",))}),
            clock=lambda: 0.0,
        ).run(dataset=dataset, dataset_path=path, policy=TEST_POLICY, top_k=1)
    )

    class MismatchedTarget:
        provenance = RunProvenance(
            "static-metric-plumbing-fixture",
            "1.0.0",
            None,
            "0" * 64,
            "mismatch-test",
            None,
            "test-policy",
            "test-policy-sha",
        )

        async def evaluate(self, _: object) -> EvaluationObservation:
            return _observation("different", ("chunk-1",))

    mismatched = asyncio.run(
        EvaluationRunner(target=MismatchedTarget(), clock=lambda: 0.0).run(
            dataset=dataset, dataset_path=path, policy=TEST_POLICY, top_k=1
        )
    )

    assert missing.retrieval.missing_results == 1
    assert missing.failures == 1
    assert mismatched.failures == 2
    assert {record.failure_kind for record in mismatched.records} == {
        "question_id_mismatch"
    }


def test_fixture_only_cli_writes_artifacts_without_provider_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset_path = Path("docs/evaluation/golden_dataset_v1.json")
    dataset = load_dataset(dataset_path)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "observations": [
                    {
                        "question_id": question.question_id,
                        "retrieved_chunk_ids": list(question.expected_chunk_ids),
                        "model": "local-fixture",
                        "usage": {
                            "input_tokens": 1,
                            "output_tokens": 1,
                            "total_tokens": 2,
                        },
                        "claim_count": 0,
                        "unsupported_claim_count": 0,
                        "failure_kind": None,
                    }
                    for question in dataset.questions
                ],
            }
        ),
        encoding="utf-8",
    )
    json_path = tmp_path / "result.json"
    csv_path = tmp_path / "records.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            str(dataset_path),
            "--fixture",
            str(fixture_path),
            "--output-json",
            str(json_path),
            "--output-csv",
            str(csv_path),
            "--run-id",
            "fixture-cli-test",
        ],
    )

    assert cli.main() == 0
    assert '"recall_at_k": 1.0' in json_path.read_text(encoding="utf-8")
    assert len(csv_path.read_text(encoding="utf-8").splitlines()) == 51


def test_rag_generation_adapter_never_uses_citations_as_retrieval_ids() -> None:
    class FakeRagService:
        async def answer(self, _: str) -> RagAnswerResult:
            return RagAnswerResult(
                answer="Synthetic answer.",
                model="fake-rag",
                usage=TokenUsage(input_tokens=2, output_tokens=1, total_tokens=3),
                claims=(
                    CitationClaim(
                        "Synthetic answer.", "supported", ("citation-1",), None
                    ),
                    CitationClaim("Rejected.", "unsupported", (), "missing_citation"),
                ),
                citations=(
                    Citation(
                        "citation-1",
                        "doc-id",
                        "synthetic.md",
                        "chunk-safe",
                        0,
                        "Synthetic excerpt.",
                        None,
                        None,
                    ),
                ),
            )

    question = GoldenQuestion(
        "q-1", "Synthetic question?", ("chunk-safe",), "synthetic", "reviewed", "note"
    )
    observation = asyncio.run(
        RagGenerationEvaluationTarget(FakeRagService()).evaluate(question)
    )

    assert observation.retrieved_chunk_ids is None
    assert observation.unsupported_claim_count == 1
    assert observation.claim_count == 2


def test_metric_and_fixture_validation_reject_invalid_input() -> None:
    observation = _observation("q-1", ("chunk-1",))
    with pytest.raises(ValueError, match="positive"):
        retrieval_metrics(
            observations=(observation,), expected_chunk_ids=(("chunk-1",),), top_k=0
        )
    with pytest.raises(ValueError, match="align"):
        retrieval_metrics(observations=(observation,), expected_chunk_ids=(), top_k=1)
    with pytest.raises(ValueError, match="unknown"):
        human_review_rate((), "not-a-metric")
    assert (
        unsupported_claim_rate(
            [_observation("q-1", ("chunk-1",), claims=0, unsupported=0)]
        )
        is None
    )
    with pytest.raises(ValueError, match="percentile"):
        percentile_nearest_rank([1.0], 0)
    with pytest.raises(ValueError, match="unexpected"):
        observation_from_fixture({})


def test_cli_reports_invalid_fixture_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture_path = tmp_path / "invalid.json"
    fixture_path.write_text(
        '{"schema_version": 2, "observations": []}', encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--fixture",
            str(fixture_path),
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "invalid-fixture-test",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_cli_requires_explicit_local_ollama_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "missing-ollama-opt-in",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


@pytest.mark.parametrize(
    "disallowed_arguments",
    (
        ("--output-json", "result.json", "--output-csv", "result.csv"),
        ("--question-id", "G001"),
        ("--fixture", "fixture.json"),
        ("--review-packet", "review.md", "--reviewer-id", "reviewer_a"),
    ),
)
def test_local_ollama_preflight_rejects_outputs_and_non_full_modes_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    disallowed_arguments: tuple[str, ...],
) -> None:
    def settings_must_not_be_constructed() -> Settings:
        pytest.fail("invalid preflight arguments must not construct Settings")

    def target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("invalid preflight arguments must not construct a target")

    monkeypatch.setattr(cli, "Settings", settings_must_not_be_constructed)
    monkeypatch.setattr(
        cli,
        "build_local_ollama_readiness_preflight",
        target_must_not_be_constructed,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--local-ollama-preflight",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--run-id",
            "preflight-invalid-mode",
            *disallowed_arguments,
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_local_ollama_preflight_requires_explicit_opt_in_and_run_id_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def settings_must_not_be_constructed() -> Settings:
        pytest.fail("invalid preflight must not construct Settings")

    monkeypatch.setattr(cli, "Settings", settings_must_not_be_constructed)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--local-ollama-preflight",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_local_ollama_preflight_rejects_an_altered_approved_corpus_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))

    def target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("unapproved preflight corpus must not construct a target")

    monkeypatch.setattr(
        cli,
        "load_synthetic_corpus",
        lambda _: replace(approved, sha256="f" * 64),
    )
    monkeypatch.setattr(
        cli,
        "build_local_ollama_readiness_preflight",
        target_must_not_be_constructed,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--local-ollama-preflight",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--run-id",
            "preflight-altered-corpus",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_non_preflight_cli_still_requires_both_artifact_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--fixture",
            "fixture.json",
            "--run-id",
            "missing-artifact-paths",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_local_ollama_preflight_validates_inputs_then_runs_only_one_readiness_call(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    original_load_dataset = cli.load_dataset
    original_load_policy = cli.load_regression_policy
    original_load_corpus = cli.load_synthetic_corpus
    original_validate_coverage = cli.validate_dataset_coverage
    original_validate_corpus = cli.validate_approved_local_ollama_corpus

    class ReadyTarget:
        async def prepare(self) -> LocalOllamaReadinessRecord:
            events.append("readiness")
            return LocalOllamaReadinessRecord(
                event="stage8_local_readiness",
                outcome="ready",
                duration_ms=1.0,
                model="fake-local-model",
                profile="generic_completion_v1",
                correlation="preflight-ready",
            )

    def load_dataset(path: Path) -> GoldenDataset:
        events.append("dataset")
        return original_load_dataset(path)

    def load_policy(path: Path) -> RegressionPolicy:
        events.append("policy")
        return original_load_policy(path)

    def load_corpus(path: Path) -> SyntheticCorpus:
        events.append("corpus")
        return original_load_corpus(path)

    def validate_coverage(dataset: GoldenDataset, corpus: SyntheticCorpus) -> None:
        events.append("coverage")
        original_validate_coverage(dataset, corpus)

    def validate_corpus(
        corpus: SyntheticCorpus, *, policy_corpus_sha256: str | None
    ) -> None:
        events.append("approved-corpus")
        original_validate_corpus(corpus, policy_corpus_sha256=policy_corpus_sha256)

    def settings() -> object:
        events.append("settings")
        return object()

    def build_preflight(**kwargs: object) -> ReadyTarget:
        assert set(kwargs) == {"settings", "run_id", "top_k"}
        events.append("preflight")
        return ReadyTarget()

    def runner_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("preflight must not construct an evaluation runner")

    def full_target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("preflight must not construct the full generation target")

    def artifacts_must_not_be_written(**_: object) -> None:
        pytest.fail("preflight must not write artifacts")

    def packet_must_not_be_written(**_: object) -> None:
        pytest.fail("preflight must not write a reviewer packet")

    monkeypatch.setattr(cli, "load_dataset", load_dataset)
    monkeypatch.setattr(cli, "load_regression_policy", load_policy)
    monkeypatch.setattr(cli, "load_synthetic_corpus", load_corpus)
    monkeypatch.setattr(cli, "validate_dataset_coverage", validate_coverage)
    monkeypatch.setattr(cli, "validate_approved_local_ollama_corpus", validate_corpus)
    monkeypatch.setattr(cli, "Settings", settings)
    monkeypatch.setattr(cli, "build_local_ollama_readiness_preflight", build_preflight)
    monkeypatch.setattr(
        cli,
        "build_synthetic_ollama_generation_target",
        full_target_must_not_be_constructed,
    )
    monkeypatch.setattr(cli, "EvaluationRunner", runner_must_not_be_constructed)
    monkeypatch.setattr(cli, "write_artifacts", artifacts_must_not_be_written)
    monkeypatch.setattr(cli, "write_reviewer_packet", packet_must_not_be_written)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--local-ollama-preflight",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--run-id",
            "preflight-ready",
        ],
    )

    assert cli.main() == 0
    assert events == [
        "dataset",
        "policy",
        "corpus",
        "coverage",
        "approved-corpus",
        "settings",
        "preflight",
        "readiness",
    ]
    captured = capsys.readouterr()
    assert captured.out.strip().split() == [
        "event=stage8_local_readiness",
        "outcome=ready",
        "duration_ms=1.000",
        "model=fake-local-model",
        "profile=generic_completion_v1",
        "correlation=preflight-ready",
    ]
    assert captured.err == ""


def test_local_ollama_preflight_fails_closed_without_runner_or_artifacts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FailedTarget:
        async def prepare(self) -> LocalOllamaReadinessRecord:
            return LocalOllamaReadinessRecord(
                event="stage8_local_readiness",
                outcome="failed",
                duration_ms=1.0,
                model="fake-local-model",
                profile="generic_completion_v1",
                correlation="preflight-failed",
            )

    def runner_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("failed preflight must not construct an evaluation runner")

    def full_target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("failed preflight must not construct the full generation target")

    def artifacts_must_not_be_written(**_: object) -> None:
        pytest.fail("failed preflight must not write artifacts")

    def packet_must_not_be_written(**_: object) -> None:
        pytest.fail("failed preflight must not write a reviewer packet")

    monkeypatch.setattr(
        cli, "build_local_ollama_readiness_preflight", lambda **_: FailedTarget()
    )
    monkeypatch.setattr(cli, "Settings", object)
    monkeypatch.setattr(cli, "EvaluationRunner", runner_must_not_be_constructed)
    monkeypatch.setattr(
        cli,
        "build_synthetic_ollama_generation_target",
        full_target_must_not_be_constructed,
    )
    monkeypatch.setattr(cli, "write_artifacts", artifacts_must_not_be_written)
    monkeypatch.setattr(cli, "write_reviewer_packet", packet_must_not_be_written)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--local-ollama-preflight",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--run-id",
            "preflight-failed",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    captured = capsys.readouterr()
    assert exit_code.value.code == 2
    assert captured.out.strip().split() == [
        "event=stage8_local_readiness",
        "outcome=failed",
        "duration_ms=1.000",
        "model=fake-local-model",
        "profile=generic_completion_v1",
        "correlation=preflight-failed",
    ]
    assert "local readiness check failed" in captured.err
    assert "traceback" not in captured.err.lower()


@pytest.mark.parametrize(
    ("target_arguments", "question_id", "message"),
    [
        (("--fixture", "unused.json"), "G001", "requires"),
        (
            (
                "--synthetic-ollama-corpus",
                "docs/evaluation/synthetic_corpus_v1.json",
                "--allow-local-ollama",
            ),
            "",
            "must not be empty",
        ),
    ],
)
def test_cli_rejects_invalid_question_id_smoke_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    target_arguments: tuple[str, ...],
    question_id: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            *target_arguments,
            "--question-id",
            question_id,
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "invalid-question-id-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "result.json").exists()


def test_cli_smoke_rejects_assessments_before_target_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("smoke target must not be constructed")

    monkeypatch.setattr(
        cli,
        "build_synthetic_ollama_generation_target",
        target_must_not_be_constructed,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--question-id",
            "G001",
            "--assessments",
            str(tmp_path / "assessments.json"),
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "smoke-assessment-rejected",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_cli_unknown_smoke_question_rejects_before_target_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def target_must_not_be_constructed(**_: object) -> StaticFixtureTarget:
        pytest.fail("unknown smoke question must not construct a target")

    monkeypatch.setattr(
        cli,
        "build_synthetic_ollama_generation_target",
        target_must_not_be_constructed,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--question-id",
            "unknown-question",
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "unknown-question-smoke",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_smoke_run_validates_full_corpus_then_serializes_one_question_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    original_coverage = cli.validate_dataset_coverage
    original_approved_corpus = cli.validate_approved_local_ollama_corpus

    def record_coverage(dataset: GoldenDataset, corpus: SyntheticCorpus) -> None:
        events.append(f"coverage:{len(dataset.questions)}")
        original_coverage(dataset, corpus)

    def record_approved_corpus(
        corpus: SyntheticCorpus, *, policy_corpus_sha256: str | None
    ) -> None:
        events.append("approved-corpus")
        original_approved_corpus(corpus, policy_corpus_sha256=policy_corpus_sha256)

    def build_fake_target(**kwargs: object) -> StaticFixtureTarget:
        events.append("target")
        selected_corpus = kwargs["corpus"]
        assert isinstance(selected_corpus, SyntheticCorpus)
        policy_version = kwargs["policy_version"]
        policy_sha256 = kwargs["policy_sha256"]
        run_id = kwargs["run_id"]
        assert isinstance(policy_version, str)
        assert isinstance(policy_sha256, str)
        assert isinstance(run_id, str)
        return StaticFixtureTarget(
            {"G001": _observation("G001", ("synthetic-recovery-001",))},
            RunProvenance(
                target_id="fake-local-smoke",
                target_version="1",
                model_identity="fake-local-model",
                configuration_sha256="f" * 64,
                run_id=run_id,
                calibration_id=None,
                policy_version=policy_version,
                policy_sha256=policy_sha256,
            ),
        )

    monkeypatch.setattr(cli, "validate_dataset_coverage", record_coverage)
    monkeypatch.setattr(
        cli, "validate_approved_local_ollama_corpus", record_approved_corpus
    )
    monkeypatch.setattr(
        cli, "build_synthetic_ollama_generation_target", build_fake_target
    )
    monkeypatch.setattr(cli, "Settings", object)
    summary = asyncio.run(
        cli._run(
            argparse.Namespace(
                dataset=Path("docs/evaluation/golden_dataset_v1.json"),
                fixture=None,
                synthetic_corpus=None,
                synthetic_ollama_corpus=Path(
                    "docs/evaluation/synthetic_corpus_v1.json"
                ),
                allow_local_ollama=True,
                policy=Path("docs/evaluation/baseline_policy_v1.json"),
                run_id="single-question-smoke-test",
                output_json=tmp_path / "result.json",
                output_csv=tmp_path / "result.csv",
                top_k=5,
                assessments=None,
                question_id="G001",
            )
        )
    )

    assert events == ["coverage:50", "approved-corpus", "target"]
    assert summary.evaluation_scope == "single_question_smoke"
    assert summary.retrieval.evaluated_questions == 1
    assert tuple(record.question_id for record in summary.records) == ("G001",)


def test_full_local_run_performs_readiness_before_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    expected_summary = object()

    class ReadyTarget:
        async def prepare_for_full_run(self) -> LocalOllamaReadinessRecord:
            events.append("readiness")
            return LocalOllamaReadinessRecord(
                event="stage8_local_readiness",
                outcome="ready",
                duration_ms=1.0,
                model="fake-local-model",
                profile="generic_completion_v1",
                correlation="full-readiness-run",
            )

    class RecordingRunner:
        def __init__(self, *, target: object) -> None:
            assert isinstance(target, ReadyTarget)
            events.append("runner")

        async def run(self, **_: object) -> object:
            return expected_summary

    def build_target(**kwargs: object) -> ReadyTarget:
        assert kwargs["readiness_required"] is True
        events.append("target")
        return ReadyTarget()

    monkeypatch.setattr(cli, "build_synthetic_ollama_generation_target", build_target)
    monkeypatch.setattr(cli, "EvaluationRunner", RecordingRunner)
    monkeypatch.setattr(cli, "Settings", object)

    result = asyncio.run(
        cli._run(
            argparse.Namespace(
                dataset=Path("docs/evaluation/golden_dataset_v1.json"),
                fixture=None,
                synthetic_corpus=None,
                synthetic_ollama_corpus=Path(
                    "docs/evaluation/synthetic_corpus_v1.json"
                ),
                allow_local_ollama=True,
                policy=Path("docs/evaluation/baseline_policy_v1.json"),
                run_id="full-readiness-run",
                output_json=tmp_path / "result.json",
                output_csv=tmp_path / "result.csv",
                top_k=5,
                assessments=None,
                assessment_source_artifact=None,
                question_id=None,
                review_packet=None,
                reviewer_id=None,
            )
        )
    )

    assert result is expected_summary
    assert events == ["target", "readiness", "runner"]
    assert capsys.readouterr().out.strip().split() == [
        "event=stage8_local_readiness",
        "outcome=ready",
        "duration_ms=1.000",
        "model=fake-local-model",
        "profile=generic_completion_v1",
        "correlation=full-readiness-run",
    ]


def test_full_local_readiness_failure_prevents_runner_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FailedTarget:
        async def prepare_for_full_run(self) -> LocalOllamaReadinessRecord:
            return LocalOllamaReadinessRecord(
                event="stage8_local_readiness",
                outcome="failed",
                duration_ms=1.0,
                model="fake-local-model",
                profile="generic_completion_v1",
                correlation="failed-readiness-run",
            )

    monkeypatch.setattr(
        cli, "build_synthetic_ollama_generation_target", lambda **_: FailedTarget()
    )
    monkeypatch.setattr(cli, "Settings", object)

    class RunnerMustNotRun:
        def __init__(self, **_: object) -> None:
            pytest.fail("runner must not execute after failed readiness")

    monkeypatch.setattr(cli, "EvaluationRunner", RunnerMustNotRun)

    with pytest.raises(cli._LocalOllamaReadinessError):
        asyncio.run(
            cli._run(
                argparse.Namespace(
                    dataset=Path("docs/evaluation/golden_dataset_v1.json"),
                    fixture=None,
                    synthetic_corpus=None,
                    synthetic_ollama_corpus=Path(
                        "docs/evaluation/synthetic_corpus_v1.json"
                    ),
                    allow_local_ollama=True,
                    policy=Path("docs/evaluation/baseline_policy_v1.json"),
                    run_id="failed-readiness-run",
                    output_json=tmp_path / "result.json",
                    output_csv=tmp_path / "result.csv",
                    top_k=5,
                    assessments=None,
                    assessment_source_artifact=None,
                    question_id=None,
                    review_packet=None,
                    reviewer_id=None,
                )
            )
        )

    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "result.csv").exists()
    assert capsys.readouterr().out.strip().split() == [
        "event=stage8_local_readiness",
        "outcome=failed",
        "duration_ms=1.000",
        "model=fake-local-model",
        "profile=generic_completion_v1",
        "correlation=failed-readiness-run",
    ]


def test_cli_reports_fixed_safe_error_after_readiness_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failed_readiness(_: argparse.Namespace) -> object:
        raise cli._LocalOllamaReadinessError()

    monkeypatch.setattr(cli, "_run", failed_readiness)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--fixture",
            str(tmp_path / "fixture.json"),
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "readiness-cli-failure",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2
    assert "local readiness check failed" in capsys.readouterr().err
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "result.csv").exists()


def test_cli_rejects_a_nonbaseline_ollama_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alternate_corpus = tmp_path / "alternate-synthetic.json"
    alternate_corpus.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-ollama-corpus",
            str(alternate_corpus),
            "--allow-local-ollama",
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "alternate-ollama-corpus",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_cli_rejects_altered_content_at_the_approved_ollama_corpus_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    approved = load_synthetic_corpus(Path("docs/evaluation/synthetic_corpus_v1.json"))
    monkeypatch.setattr(
        cli,
        "load_synthetic_corpus",
        lambda path: replace(approved, sha256="f" * 64),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-ollama-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--allow-local-ollama",
            "--output-json",
            str(tmp_path / "result.json"),
            "--output-csv",
            str(tmp_path / "result.csv"),
            "--run-id",
            "altered-content-at-approved-path",
        ],
    )

    with pytest.raises(SystemExit) as exit_code:
        cli.main()

    assert exit_code.value.code == 2


def test_fixture_ids_must_be_unique_and_exact_dataset_membership(
    tmp_path: Path,
) -> None:
    dataset = _dataset(tmp_path / "dataset.json")
    duplicate = (_observation("q-1", ("chunk-1",)),) * 2
    outside = (
        _observation("q-1", ("chunk-1",)),
        _observation("unknown", ("chunk-3",)),
    )

    with pytest.raises(ValueError, match="unique"):
        cli._validate_fixture_ids(dataset, duplicate)
    with pytest.raises(ValueError, match="exactly"):
        cli._validate_fixture_ids(dataset, outside)


def test_regression_rejects_provenance_and_policy_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "dataset.json"
    dataset = _dataset(path)
    summary = asyncio.run(
        EvaluationRunner(
            target=StaticFixtureTarget(
                {
                    "q-1": _observation("q-1", ("chunk-1",)),
                    "q-2": _observation("q-2", ("chunk-3",)),
                }
            ),
            clock=lambda: 0.0,
        ).run(dataset=dataset, dataset_path=path, policy=TEST_POLICY, top_k=1)
    )
    different_policy = RegressionPolicy(
        policy_version="different-policy",
        dataset_id="test-golden",
        dataset_version="1.0.0",
        thresholds=RegressionThresholds(),
        sha256="different-policy-sha",
    )

    with pytest.raises(ValueError, match="policy"):
        compare_to_baseline(
            baseline=summary, candidate=summary, policy=different_policy
        )
    same_version_different_sha = RegressionPolicy(
        policy_version="test-policy",
        dataset_id="test-golden",
        dataset_version="1.0.0",
        thresholds=RegressionThresholds(max_retrieval_metric_drop=0.5),
        sha256="same-version-different-sha",
    )
    with pytest.raises(ValueError, match="policy"):
        compare_to_baseline(
            baseline=summary,
            candidate=summary,
            policy=same_version_different_sha,
        )
    with pytest.raises(ValueError, match="provenance"):
        compare_to_baseline(
            baseline=summary,
            candidate=replace(
                summary,
                provenance=replace(summary.provenance, target_version="different"),
            ),
            policy=TEST_POLICY,
        )
    with pytest.raises(ValueError, match="smoke"):
        compare_to_baseline(
            baseline=replace(summary, evaluation_scope="single_question_smoke"),
            candidate=summary,
            policy=TEST_POLICY,
        )


def test_corpus_and_policy_loaders_fail_closed(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="could not load"):
        load_synthetic_corpus(malformed)
    with pytest.raises(ValueError, match="could not load"):
        load_regression_policy(malformed)

    dataset = _dataset(tmp_path / "dataset.json")
    empty_corpus = SyntheticCorpus("test", "1", (), "0" * 64)
    with pytest.raises(ValueError, match="does not contain"):
        validate_dataset_coverage(dataset, empty_corpus)


def test_cli_runs_traceable_synthetic_corpus_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_json = tmp_path / "synthetic.json"
    output_csv = tmp_path / "synthetic.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "stage8",
            "--dataset",
            "docs/evaluation/golden_dataset_v1.json",
            "--synthetic-corpus",
            "docs/evaluation/synthetic_corpus_v1.json",
            "--output-json",
            str(output_json),
            "--output-csv",
            str(output_csv),
            "--run-id",
            "test-corpus-cli",
        ],
    )

    assert cli.main() == 0
    artifact = json.loads(output_json.read_text(encoding="utf-8"))
    assert artifact["summary"]["retrieval"]["recall_at_k"] == 1.0
    assert (
        artifact["summary"]["provenance"]["target_id"] == "synthetic-corpus-retrieval"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": 2,
            "corpus_id": "x",
            "corpus_version": "1",
            "source_policy": "synthetic_only",
            "chunks": [],
        },
        {
            "schema_version": 1,
            "corpus_id": "",
            "corpus_version": "1",
            "source_policy": "synthetic_only",
            "chunks": [],
        },
        {
            "schema_version": 1,
            "corpus_id": "x",
            "corpus_version": "",
            "source_policy": "synthetic_only",
            "chunks": [],
        },
        {
            "schema_version": 1,
            "corpus_id": "x",
            "corpus_version": "1",
            "source_policy": "synthetic_only",
            "chunks": [],
        },
        {
            "schema_version": 1,
            "corpus_id": "x",
            "corpus_version": "1",
            "source_policy": "synthetic_only",
            "chunks": [{"chunk_id": "x"}],
        },
    ],
)
def test_synthetic_corpus_schema_rejects_incomplete_data(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_synthetic_corpus(path)


def test_observation_fixture_rejects_invalid_usage_and_identity() -> None:
    valid = {
        "question_id": "q-1",
        "retrieved_chunk_ids": ["chunk-1"],
        "model": "model-a",
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "claim_count": 1,
        "unsupported_claim_count": 0,
        "failure_kind": None,
    }
    with pytest.raises(ValueError, match="question_id"):
        observation_from_fixture({**valid, "question_id": ""})
    with pytest.raises(ValueError, match="retrieved"):
        observation_from_fixture({**valid, "retrieved_chunk_ids": [""]})
    with pytest.raises(ValueError, match="usage"):
        observation_from_fixture({**valid, "usage": {"input_tokens": -1}})
    with pytest.raises(ValueError, match="model"):
        observation_from_fixture({**valid, "model": 1})
