"""Traceable synthetic corpus and deterministic server-owned retrieval target."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.evaluation.models import (
    EvaluationObservation,
    GoldenDataset,
    GoldenQuestion,
    RunProvenance,
)

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
APPROVED_LOCAL_OLLAMA_CORPUS_SHA256 = (
    "c2e4d529881a0b711c4f5c6cc42b2ef1f6ed14a71e56a0dfef5678100fce2fa6"
)


@dataclass(frozen=True)
class SyntheticCorpusChunk:
    chunk_id: str
    content: str


@dataclass(frozen=True)
class SyntheticCorpus:
    corpus_id: str
    corpus_version: str
    chunks: tuple[SyntheticCorpusChunk, ...]
    sha256: str


def load_synthetic_corpus(path: Path) -> SyntheticCorpus:
    """Load synthetic-only fixture evidence with no database/provider dependency."""

    try:
        raw_bytes = path.read_bytes()
        raw: Any = json.loads(raw_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("could not load synthetic evaluation corpus") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "corpus_id",
        "corpus_version",
        "source_policy",
        "chunks",
    }:
        raise ValueError("synthetic corpus has unexpected or missing fields")
    if raw["schema_version"] != 1 or raw["source_policy"] != "synthetic_only":
        raise ValueError("synthetic corpus schema or source policy is invalid")
    if not isinstance(raw["corpus_id"], str) or not raw["corpus_id"].strip():
        raise ValueError("synthetic corpus ID is invalid")
    if not isinstance(raw["corpus_version"], str) or not raw["corpus_version"].strip():
        raise ValueError("synthetic corpus version is invalid")
    if not isinstance(raw["chunks"], list) or not raw["chunks"]:
        raise ValueError("synthetic corpus chunks are invalid")
    chunks: list[SyntheticCorpusChunk] = []
    for value in raw["chunks"]:
        if not isinstance(value, dict) or set(value) != {"chunk_id", "content"}:
            raise ValueError("synthetic corpus chunk fields are invalid")
        if not all(
            isinstance(value[field], str) and value[field].strip()
            for field in ("chunk_id", "content")
        ):
            raise ValueError("synthetic corpus chunk values are invalid")
        chunks.append(SyntheticCorpusChunk(value["chunk_id"], value["content"]))
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise ValueError("synthetic corpus chunk IDs must be unique")
    return SyntheticCorpus(
        corpus_id=raw["corpus_id"],
        corpus_version=raw["corpus_version"],
        chunks=tuple(chunks),
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def validate_dataset_coverage(dataset: GoldenDataset, corpus: SyntheticCorpus) -> None:
    """Fail closed when expected evidence cannot be retrieved from the fixture."""

    chunk_ids = {chunk.chunk_id for chunk in corpus.chunks}
    missing = {
        chunk_id
        for question in dataset.questions
        for chunk_id in question.expected_chunk_ids
        if chunk_id not in chunk_ids
    }
    if missing:
        raise ValueError("synthetic corpus does not contain every expected chunk ID")


def validate_approved_local_ollama_corpus(
    corpus: SyntheticCorpus, *, policy_corpus_sha256: str | None
) -> None:
    """Fail closed unless code, policy, and loaded fixture agree byte-for-byte."""

    if policy_corpus_sha256 != APPROVED_LOCAL_OLLAMA_CORPUS_SHA256:
        raise ValueError("policy does not pin the approved local Ollama corpus")
    if corpus.sha256 != APPROVED_LOCAL_OLLAMA_CORPUS_SHA256:
        raise ValueError(
            "local Ollama corpus content does not match the approved SHA-256"
        )


class SyntheticCorpusRetrievalTarget:
    """Local lexical replay target that returns actual ranked corpus chunk IDs only."""

    def __init__(
        self,
        *,
        corpus: SyntheticCorpus,
        policy_version: str,
        policy_sha256: str,
        run_id: str,
        calibration_id: str | None = None,
    ) -> None:
        if not policy_version or not policy_sha256 or not run_id:
            raise ValueError("policy identity and run identity are required")
        self._corpus = corpus
        self._chunk_terms = {
            chunk.chunk_id: synthetic_terms(chunk.content) for chunk in corpus.chunks
        }
        configuration = json.dumps(
            {"ranking": "unique_query_token_overlap_desc_then_chunk_id"},
            sort_keys=True,
        ).encode()
        self.provenance = RunProvenance(
            target_id="synthetic-corpus-retrieval",
            target_version="1.0.0",
            model_identity=None,
            configuration_sha256=hashlib.sha256(configuration).hexdigest(),
            run_id=run_id,
            calibration_id=calibration_id,
            policy_version=policy_version,
            policy_sha256=policy_sha256,
        )

    async def evaluate(self, question: GoldenQuestion) -> EvaluationObservation:
        ranked = ranked_synthetic_chunk_ids(question.question, self._chunk_terms)
        return EvaluationObservation(
            question_id=question.question_id,
            retrieved_chunk_ids=tuple(ranked),
            model=None,
            latency_ms=None,
            usage=None,
            claim_count=None,
            unsupported_claim_count=None,
            failure_kind=None,
        )


def synthetic_terms(value: str) -> frozenset[str]:
    return frozenset(_TOKEN_PATTERN.findall(value.lower()))


def ranked_synthetic_chunk_ids(
    query: str, chunk_terms: dict[str, frozenset[str]]
) -> list[str]:
    query_terms = synthetic_terms(query)
    return sorted(
        chunk_terms,
        key=lambda chunk_id: (
            -len(query_terms & chunk_terms[chunk_id]),
            chunk_id,
        ),
    )
