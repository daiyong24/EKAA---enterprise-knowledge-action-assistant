from __future__ import annotations

import asyncio
from collections.abc import Sequence
from statistics import median
from time import perf_counter

import pytest

from backend.app.embeddings.models import RetrievedChunk
from backend.app.reranking.models import RerankBatch, RerankInput, RerankScore
from backend.app.reranking.provider import DeterministicOverlapReranker
from backend.app.reranking.service import RerankingService


def _chunk(chunk_id: str, content: str, *, score: float = 0.9) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=f"document-{chunk_id}",
        document_name=f"{chunk_id}.txt",
        chunk_id=chunk_id,
        chunk_index=0,
        content=content,
        score=score,
        page_number=None,
        section=None,
    )


class _Provider:
    model = "test-reranker"

    def __init__(self, outcome: RerankBatch | Exception, *, delay: float = 0) -> None:
        self.outcome = outcome
        self.delay = delay
        self.calls: list[tuple[str, tuple[RerankInput, ...]]] = []

    async def rerank(
        self, *, query: str, candidates: Sequence[RerankInput]
    ) -> RerankBatch:
        self.calls.append((query, tuple(candidates)))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _batch(*scores: tuple[str, float]) -> RerankBatch:
    return RerankBatch(
        model="test-reranker",
        scores=tuple(
            RerankScore(chunk_id=chunk_id, score=score) for chunk_id, score in scores
        ),
    )


@pytest.mark.anyio
async def test_reranking_orders_by_normalized_score_then_original_rank() -> None:
    provider = _Provider(_batch(("b", 0.9), ("a", 0.9), ("c", 0.4)))
    service = RerankingService(provider=provider, timeout_seconds=0.1)  # type: ignore[arg-type]
    candidates = [
        _chunk("a", "first"),
        _chunk("b", "second"),
        _chunk("c", "third"),
    ]

    result = await service.rerank(query="question", candidates=candidates)

    assert [item.chunk_id for item in result] == ["a", "b", "c"]
    assert [item.rerank_score for item in result] == [0.9, 0.9, 0.4]
    assert [item.score for item in result] == [0.9, 0.9, 0.9]
    assert provider.calls[0][1] == (
        RerankInput(chunk_id="a", content="first"),
        RerankInput(chunk_id="b", content="second"),
        RerankInput(chunk_id="c", content="third"),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "batch",
    [
        RerankBatch(model="wrong-model", scores=(RerankScore("a", 0.9),)),
        _batch(("invented", 0.9)),
        RerankBatch(
            model="test-reranker",
            scores=(RerankScore("a", 0.9), RerankScore("a", 0.8)),
        ),
        _batch(("a", 1.1)),
        _batch(("a", -0.1)),
        _batch(("a", True)),
        _batch(("a", float("inf"))),
        _batch(("a", float("-inf"))),
        _batch(("a", float("nan"))),
    ],
)
async def test_invalid_provider_output_falls_back_to_original_stable_order(
    batch: RerankBatch,
) -> None:
    service = RerankingService(
        provider=_Provider(batch),
        timeout_seconds=0.1,  # type: ignore[arg-type]
    )
    candidates = [_chunk("a", "a"), _chunk("b", "b")]

    result = await service.rerank(query="question", candidates=candidates)

    assert result == candidates


@pytest.mark.anyio
async def test_provider_failure_and_timeout_fall_back_without_raising() -> None:
    candidates = [_chunk("a", "a"), _chunk("b", "b")]
    failure = RerankingService(
        provider=_Provider(RuntimeError("private provider detail")),  # type: ignore[arg-type]
        timeout_seconds=0.1,
    )
    timeout = RerankingService(
        provider=_Provider(_batch(("a", 1.0), ("b", 0.0)), delay=0.05),  # type: ignore[arg-type]
        timeout_seconds=0.001,
    )

    assert await failure.rerank(query="question", candidates=candidates) == candidates
    assert await timeout.rerank(query="question", candidates=candidates) == candidates


@pytest.mark.anyio
async def test_deterministic_provider_score_mapping_is_reproducible() -> None:
    provider = DeterministicOverlapReranker()
    candidates = (
        RerankInput("full", "Recovery target for incident INC-1042 is four hours."),
        RerankInput("partial", "The target is documented."),
        RerankInput("none", "Unrelated public deployment evidence."),
    )

    first = await provider.rerank(
        query="INC-1042 recovery target", candidates=candidates
    )
    second = await provider.rerank(
        query="INC-1042 recovery target", candidates=candidates
    )

    assert first == second
    assert [(item.chunk_id, item.score) for item in first.scores] == [
        ("full", 1.0),
        ("partial", 1 / 3),
        ("none", 0.0),
    ]


@pytest.mark.anyio
async def test_declared_synthetic_fixture_has_top_one_gain_at_bounded_local_cost() -> (
    None
):
    # This is a focused, fully deterministic fixture comparison, not a broad RAG
    # evaluation or a production-quality claim.  The expected chunk is deliberately
    # second in each baseline hybrid ordering but covers all query terms.
    fixtures = (
        (
            "INC-1042 recovery target",
            [
                _chunk("noise-a", "INC-1042 status"),
                _chunk("answer-a", "INC-1042 recovery target is four hours."),
            ],
            "answer-a",
        ),
        (
            "KB_7 rotation interval",
            [
                _chunk("noise-b", "KB_7 overview"),
                _chunk("answer-b", "KB_7 rotation interval is ninety days."),
            ],
            "answer-b",
        ),
        (
            "VPN certificate renewal",
            [
                _chunk("noise-c", "VPN certificate inventory"),
                _chunk(
                    "answer-c", "VPN certificate renewal requires security approval."
                ),
            ],
            "answer-c",
        ),
        (
            "backup recovery target",
            [
                _chunk("noise-d", "backup status"),
                _chunk("answer-d", "backup recovery target is four hours."),
            ],
            "answer-d",
        ),
    )
    service = RerankingService(
        provider=DeterministicOverlapReranker(), timeout_seconds=0.1
    )
    baseline_hits = sum(
        candidates[0].chunk_id == expected for _, candidates, expected in fixtures
    )

    for query, candidates, _ in fixtures:
        await service.rerank(query=query, candidates=candidates)
    timings: list[float] = []
    reranked_hits = 0
    for query, candidates, expected in fixtures:
        started = perf_counter()
        result = await service.rerank(query=query, candidates=candidates)
        timings.append(perf_counter() - started)
        reranked_hits += result[0].chunk_id == expected

    assert baseline_hits == 0
    assert reranked_hits == len(fixtures)
    assert max(timings) < 0.1
    assert median(timings) > 0
