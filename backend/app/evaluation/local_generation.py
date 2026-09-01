"""Explicit local-Ollama generation target over synthetic-only Stage 8 fixtures."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass

import httpx
from pydantic import SecretStr

from backend.app.core.config import Settings
from backend.app.embeddings.models import RetrievedChunk
from backend.app.evaluation.artifacts import validate_safe_identifier
from backend.app.evaluation.corpus import (
    SyntheticCorpus,
    ranked_synthetic_chunk_ids,
    synthetic_terms,
    validate_approved_local_ollama_corpus,
)
from backend.app.evaluation.models import (
    EvaluationObservation,
    GoldenQuestion,
    RunProvenance,
)
from backend.app.evaluation.review_packet import (
    ReviewCitationMaterial,
    ReviewClaimMaterial,
    ReviewEvidence,
    ReviewPacketItem,
)
from backend.app.llm.errors import ProviderOutputError
from backend.app.llm.models import ChatPrompt, ChatResult
from backend.app.llm.provider import LLMProvider, OpenAICompatibleProvider
from backend.app.rag.citations import CitationOutputParser, CitationValidator
from backend.app.rag.context import ContextBuilder
from backend.app.rag.errors import RagError, RagOutputError
from backend.app.rag.prompt_builder import RagPromptBuilder
from backend.app.rag.service import INSUFFICIENT_EVIDENCE_MODEL, RagService

_LOCAL_OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
_LOCAL_OLLAMA_API_KEY_MARKER = "ollama-local"
_DIRECT_EVIDENCE_PROMPT_PROFILE = "selected_ids_json_v1"
_LOCAL_OLLAMA_READINESS_PROFILE = "generic_completion_v1"
_LOCAL_OLLAMA_READINESS_PROMPT = ChatPrompt(
    system=(
        "You are a local model readiness check. Return a short JSON object that "
        "confirms readiness."
    ),
    user="Return the readiness confirmation now.",
)


def _local_ollama_readiness_prompt_sha256() -> str:
    """Fingerprint the static, content-free readiness prompt without retaining it."""

    serialized = json.dumps(
        {
            "system": _LOCAL_OLLAMA_READINESS_PROMPT.system,
            "user": _LOCAL_OLLAMA_READINESS_PROMPT.user,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _direct_evidence_instruction_sha256() -> str:
    """Fingerprint the effective static instruction without retaining its text."""

    instruction = RagPromptBuilder._DIRECT_EVIDENCE_USER_INSTRUCTION
    return hashlib.sha256(instruction.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _LocalOllamaEvaluationProfile:
    """Fixed controls for the explicit synthetic-only local evaluation target."""

    reasoning_effort: str = "none"
    response_format_type: str = "json_object"
    max_tokens: int = 256
    provider_timeout_seconds: float = 20.0
    rag_max_retries: int = 0


_LOCAL_OLLAMA_EVALUATION_PROFILE = _LocalOllamaEvaluationProfile()


@dataclass(frozen=True)
class LocalOllamaReadinessRecord:
    """Content-free outcome for one full-run local provider readiness call."""

    event: str
    outcome: str
    duration_ms: float
    model: str
    profile: str
    correlation: str

    def render(self) -> str:
        """Render only the six approved, content-free readiness fields."""

        return (
            f"event={self.event} outcome={self.outcome} "
            f"duration_ms={self.duration_ms:.3f} model={self.model} "
            f"profile={self.profile} correlation={self.correlation}"
        )


async def _complete_local_ollama_readiness(
    *, provider: LLMProvider, model_identity: str, run_id: str
) -> LocalOllamaReadinessRecord:
    """Run the static readiness completion and retain only its safe outcome."""

    started_at = time.perf_counter()
    outcome = "failed"
    try:
        result = await provider.complete(_LOCAL_OLLAMA_READINESS_PROMPT)
        if result.finish_reason == "stop" and result.model == model_identity:
            outcome = "ready"
    except Exception:
        # Do not expose a provider message, payload, traceback, or usage.
        outcome = "failed"
    duration_ms = (time.perf_counter() - started_at) * 1000
    if not math.isfinite(duration_ms) or duration_ms < 0:
        duration_ms = 0.0
    return LocalOllamaReadinessRecord(
        event="stage8_local_readiness",
        outcome=outcome,
        duration_ms=duration_ms,
        model=model_identity,
        profile=_LOCAL_OLLAMA_READINESS_PROFILE,
        correlation=run_id,
    )


class LocalOllamaReadinessPreflight:
    """Readiness-only local provider boundary with no evaluation dependencies."""

    def __init__(
        self, *, provider: LLMProvider, model_identity: str, run_id: str
    ) -> None:
        self._provider = provider
        self._model_identity = model_identity
        self._run_id = run_id
        self._attempted = False

    async def prepare(self) -> LocalOllamaReadinessRecord:
        """Make exactly one generic readiness call for an explicit preflight."""

        if self._attempted:
            raise ValueError("local readiness may be attempted only once")
        self._attempted = True
        return await _complete_local_ollama_readiness(
            provider=self._provider,
            model_identity=self._model_identity,
            run_id=self._run_id,
        )


class _LocalOllamaEvaluationProvider(OpenAICompatibleProvider):
    """Isolated adapter for the fixed, synthetic-only local Ollama endpoint."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        _require_local_ollama_configuration(settings)
        super().__init__(
            settings.model_copy(
                update={
                    "llm_api_key": SecretStr(_LOCAL_OLLAMA_API_KEY_MARKER),
                    "llm_timeout_seconds": (
                        _LOCAL_OLLAMA_EVALUATION_PROFILE.provider_timeout_seconds
                    ),
                }
            ),
            transport=transport,
        )

    async def complete(self, prompt: ChatPrompt) -> ChatResult:
        result = await super().complete(prompt)
        if result.finish_reason != "stop":
            raise ProviderOutputError(
                "local provider did not stop normally", usage=result.usage
            )
        return result

    def _request_body_extension(self, prompt: ChatPrompt) -> Mapping[str, object]:
        del prompt
        return {
            "reasoning_effort": _LOCAL_OLLAMA_EVALUATION_PROFILE.reasoning_effort,
            "response_format": {
                "type": _LOCAL_OLLAMA_EVALUATION_PROFILE.response_format_type
            },
            "max_tokens": _LOCAL_OLLAMA_EVALUATION_PROFILE.max_tokens,
        }

    def _client_options(self) -> Mapping[str, object]:
        """Prevent proxy environment variables from altering local-only egress."""

        return {"trust_env": False}


class SyntheticCorpusRagRetrievalService:
    """Map the versioned synthetic corpus to the existing RAG retrieval seam.

    This is an evaluation-only, in-memory adapter. It never opens a database and
    cannot be constructed from arbitrary documents.
    """

    def __init__(self, corpus: SyntheticCorpus) -> None:
        self._corpus = corpus
        self._chunk_terms = {
            chunk.chunk_id: synthetic_terms(chunk.content) for chunk in corpus.chunks
        }
        self._chunk_by_id = {chunk.chunk_id: chunk for chunk in corpus.chunks}
        self._chunk_index = {
            chunk.chunk_id: index for index, chunk in enumerate(corpus.chunks)
        }

    def search(self, *, query: str, top_k: int) -> list[RetrievedChunk]:
        if top_k < 1:
            raise ValueError("synthetic retrieval top_k must be positive")
        query_terms = synthetic_terms(query)
        denominator = len(query_terms)
        ranked_ids = ranked_synthetic_chunk_ids(query, self._chunk_terms)[:top_k]
        return [
            RetrievedChunk(
                document_id=self._corpus.corpus_id,
                document_name=(
                    f"{self._corpus.corpus_id}@{self._corpus.corpus_version}"
                ),
                chunk_id=chunk_id,
                chunk_index=self._chunk_index[chunk_id],
                content=self._chunk_by_id[chunk_id].content,
                score=(
                    len(query_terms & self._chunk_terms[chunk_id]) / denominator
                    if denominator
                    else 0.0
                ),
                page_number=None,
                section=None,
            )
            for chunk_id in ranked_ids
        ]


class SyntheticCorpusOllamaGenerationTarget:
    """Measure local generation through ``RagService`` without exposing content.

    Retrieval IDs are captured from the deterministic retrieval seam itself, not
    from provider-proposed citations. The provider model must exactly match the
    configured model identity before the run can claim that provenance. An
    evidence-policy refusal intentionally uses EKAA's deterministic policy model
    identity instead, while retaining the configured provider provenance.
    """

    def __init__(
        self,
        *,
        service: RagService,
        retrieval_service: SyntheticCorpusRagRetrievalService,
        retrieval_top_k: int,
        provenance: RunProvenance,
        model_identity: str,
        provider: LLMProvider,
        readiness_required: bool,
        capture_review_material: bool = False,
    ) -> None:
        self._service = service
        self._retrieval_service = retrieval_service
        self._retrieval_top_k = retrieval_top_k
        self.provenance = provenance
        self._model_identity = model_identity
        self._provider = provider
        self._readiness_required = readiness_required
        self._readiness_attempted = False
        self._readiness_ready = not readiness_required
        self._capture_review_material = capture_review_material
        self._review_items: list[ReviewPacketItem] = []

    @property
    def review_items(self) -> tuple[ReviewPacketItem, ...]:
        """Return local-only material captured during this exact replay."""

        return tuple(self._review_items)

    async def prepare_for_full_run(self) -> LocalOllamaReadinessRecord:
        """Make exactly one generic provider call before a full local replay.

        The prompt deliberately contains no dataset, corpus, question, evidence,
        credential, endpoint, or dynamic value. Its answer and usage are discarded.
        Ordinary provider and validation failures are represented only by the safe
        record below so the caller can fail closed before the runner or artifacts.
        """

        if not self._readiness_required:
            raise ValueError("local readiness is only available for full-dataset runs")
        if self._readiness_attempted:
            raise ValueError("local readiness may be attempted only once")
        self._readiness_attempted = True
        readiness = await _complete_local_ollama_readiness(
            provider=self._provider,
            model_identity=self._model_identity,
            run_id=self.provenance.run_id,
        )
        if readiness.outcome == "ready":
            self._readiness_ready = True
        return readiness

    async def evaluate(self, question: GoldenQuestion) -> EvaluationObservation:
        if self._readiness_required and not self._readiness_ready:
            raise ValueError(
                "local full-dataset generation requires successful readiness"
            )
        retrieved = self._retrieval_service.search(
            query=question.question, top_k=self._retrieval_top_k
        )
        try:
            result = await self._service.answer(question.question)
        except RagError as exc:
            self._capture_review_item(
                question=question,
                retrieved=retrieved,
                answer=None,
                failure_kind=type(exc).__name__,
                claims=(),
                citations=(),
            )
            usage = exc.usage if isinstance(exc, RagOutputError) else None
            return EvaluationObservation(
                question_id=question.question_id,
                retrieved_chunk_ids=tuple(chunk.chunk_id for chunk in retrieved),
                model=(self.provenance.model_identity if usage is not None else None),
                latency_ms=None,
                usage=usage,
                claim_count=None,
                unsupported_claim_count=None,
                failure_kind=type(exc).__name__,
            )
        if result.model not in {
            self.provenance.model_identity,
            INSUFFICIENT_EVIDENCE_MODEL,
        }:
            raise ValueError(
                "provider model does not match configured evaluation model"
            )
        observed_model = (
            self.provenance.model_identity
            if result.model == self.provenance.model_identity
            or result.usage.total_tokens > 0
            else None
        )
        self._capture_review_item(
            question=question,
            retrieved=retrieved,
            answer=result.answer,
            failure_kind=None,
            claims=tuple(
                ReviewClaimMaterial(
                    text=claim.text,
                    status=claim.status,
                    citation_ids=claim.citation_ids,
                    unsupported_reason=claim.unsupported_reason,
                )
                for claim in result.claims
            ),
            citations=tuple(
                ReviewCitationMaterial(
                    citation_id=citation.citation_id,
                    chunk_id=citation.chunk_id,
                    excerpt=citation.excerpt,
                )
                for citation in result.citations
            ),
        )
        return EvaluationObservation(
            question_id=question.question_id,
            retrieved_chunk_ids=tuple(chunk.chunk_id for chunk in retrieved),
            model=observed_model,
            latency_ms=None,
            usage=result.usage,
            claim_count=len(result.claims),
            unsupported_claim_count=sum(
                claim.status == "unsupported" for claim in result.claims
            ),
            failure_kind=None,
        )

    def _capture_review_item(
        self,
        *,
        question: GoldenQuestion,
        retrieved: list[RetrievedChunk],
        answer: str | None,
        failure_kind: str | None,
        claims: tuple[ReviewClaimMaterial, ...],
        citations: tuple[ReviewCitationMaterial, ...],
    ) -> None:
        if not self._capture_review_material:
            return
        self._review_items.append(
            ReviewPacketItem(
                question_id=question.question_id,
                question=question.question,
                answer=answer,
                failure_kind=failure_kind,
                evidence=tuple(
                    ReviewEvidence(chunk_id=chunk.chunk_id, content=chunk.content)
                    for chunk in retrieved
                ),
                claims=claims,
                citations=citations,
            )
        )


def build_synthetic_ollama_generation_target(
    *,
    corpus: SyntheticCorpus,
    settings: Settings,
    policy_version: str,
    policy_sha256: str,
    run_id: str,
    top_k: int,
    policy_corpus_sha256: str | None,
    readiness_required: bool = True,
    capture_review_material: bool = False,
    calibration_id: str | None = None,
    provider: LLMProvider | None = None,
) -> SyntheticCorpusOllamaGenerationTarget:
    """Create the explicit opt-in local-generation target from safe boundaries."""

    model_identity = settings.llm_model
    validate_safe_identifier(model_identity, "model_identity")
    if model_identity is None:
        raise ValueError("model_identity must be a bounded safe identifier")
    _require_local_ollama_configuration(settings)
    validate_approved_local_ollama_corpus(
        corpus, policy_corpus_sha256=policy_corpus_sha256
    )
    if top_k != settings.rag_retrieval_top_k:
        raise ValueError("--top-k must match EKAA_RAG_RETRIEVAL_TOP_K for generation")
    if not policy_version or not policy_sha256 or not run_id:
        raise ValueError("policy identity and run identity are required")

    retrieval_service = SyntheticCorpusRagRetrievalService(corpus)
    provenance = RunProvenance(
        target_id="synthetic-corpus-ollama-generation",
        target_version="1.4.0" if readiness_required else "1.3.2",
        model_identity=model_identity,
        configuration_sha256=_configuration_sha256(
            settings, corpus, top_k, readiness_required=readiness_required
        ),
        run_id=run_id,
        calibration_id=calibration_id,
        policy_version=policy_version,
        policy_sha256=policy_sha256,
    )
    local_provider = provider or _LocalOllamaEvaluationProvider(settings)
    service = RagService(
        retrieval_service=retrieval_service,
        provider=local_provider,
        prompt_builder=RagPromptBuilder(direct_evidence_profile=True),
        context_builder=ContextBuilder(
            max_characters=settings.rag_max_context_characters,
            max_estimated_tokens=settings.rag_max_context_tokens,
            max_sources=settings.rag_max_sources,
        ),
        citation_output_parser=CitationOutputParser(
            max_claims=settings.citation_max_claims,
            max_claim_characters=settings.citation_max_claim_characters,
        ),
        citation_validator=CitationValidator(
            max_excerpt_characters=settings.citation_max_excerpt_characters
        ),
        retrieval_top_k=top_k,
        min_evidence_score=settings.rag_min_evidence_score,
        min_evidence_sources=settings.rag_min_evidence_sources,
        max_retries=_LOCAL_OLLAMA_EVALUATION_PROFILE.rag_max_retries,
        retry_delay_seconds=settings.llm_retry_delay_seconds,
    )
    return SyntheticCorpusOllamaGenerationTarget(
        service=service,
        retrieval_service=retrieval_service,
        retrieval_top_k=top_k,
        provenance=provenance,
        model_identity=model_identity,
        provider=local_provider,
        readiness_required=readiness_required,
        capture_review_material=capture_review_material,
    )


def build_local_ollama_readiness_preflight(
    *, settings: Settings, run_id: str, top_k: int
) -> LocalOllamaReadinessPreflight:
    """Create the isolated local readiness boundary used before a fresh replay."""

    model_identity = settings.llm_model
    validate_safe_identifier(model_identity, "model_identity")
    if model_identity is None:
        raise ValueError("model_identity must be a bounded safe identifier")
    validate_safe_identifier(run_id, "run_id")
    _require_local_ollama_configuration(settings)
    if top_k != settings.rag_retrieval_top_k:
        raise ValueError("--top-k must match EKAA_RAG_RETRIEVAL_TOP_K for generation")
    return LocalOllamaReadinessPreflight(
        provider=_LocalOllamaEvaluationProvider(settings),
        model_identity=model_identity,
        run_id=run_id,
    )


def _require_local_ollama_configuration(settings: Settings) -> None:
    if not settings.llm_enabled or settings.llm_model is None:
        raise ValueError("local Ollama generation requires an enabled configured LLM")
    if settings.llm_base_url != _LOCAL_OLLAMA_BASE_URL:
        raise ValueError("local Ollama generation requires http://127.0.0.1:11434/v1")


def _configuration_sha256(
    settings: Settings,
    corpus: SyntheticCorpus,
    top_k: int,
    *,
    readiness_required: bool,
) -> str:
    """Fingerprint only non-secret, content-free configuration values."""

    configuration: Mapping[str, object] = {
        "provider": "openai_compatible_local_ollama",
        "endpoint_identity": _LOCAL_OLLAMA_BASE_URL,
        "model": settings.llm_model,
        "retrieval": "synthetic_lexical_overlap",
        "retrieval_top_k": top_k,
        "min_evidence_score": settings.rag_min_evidence_score,
        "min_evidence_sources": settings.rag_min_evidence_sources,
        "max_sources": settings.rag_max_sources,
        "max_context_characters": settings.rag_max_context_characters,
        "max_context_tokens": settings.rag_max_context_tokens,
        "citation_max_claims": settings.citation_max_claims,
        "citation_max_claim_characters": settings.citation_max_claim_characters,
        "citation_max_excerpt_characters": settings.citation_max_excerpt_characters,
        "local_evaluation_profile": {
            "reasoning_effort": _LOCAL_OLLAMA_EVALUATION_PROFILE.reasoning_effort,
            "response_format_type": (
                _LOCAL_OLLAMA_EVALUATION_PROFILE.response_format_type
            ),
            "max_tokens": _LOCAL_OLLAMA_EVALUATION_PROFILE.max_tokens,
            "effective_provider_timeout_seconds": (
                _LOCAL_OLLAMA_EVALUATION_PROFILE.provider_timeout_seconds
            ),
            "effective_rag_max_retries": (
                _LOCAL_OLLAMA_EVALUATION_PROFILE.rag_max_retries
            ),
            "api_key_mode": "fixed_non_secret_marker",
            "proxy_isolation": "trust_env_false",
            "finish_reason_policy": "stop_only",
        },
        "prompt_profile": {
            "identifier": _DIRECT_EVIDENCE_PROMPT_PROFILE,
            "instruction_sha256": _direct_evidence_instruction_sha256(),
        },
        "full_dataset_readiness": (
            {
                "profile": _LOCAL_OLLAMA_READINESS_PROFILE,
                "static_prompt_sha256": _local_ollama_readiness_prompt_sha256(),
                "scope": "full_dataset_only",
                "provider_instance_policy": "same_instance_as_generation",
                "success_policy": "finish_reason_stop_and_configured_model",
                "result_data_policy": "discard_answer_and_usage",
                "attempt_policy": "exactly_one_call_zero_retry",
                "record_schema": (
                    "event,outcome,duration_ms,model,profile,correlation"
                ),
            }
            if readiness_required
            else {"scope": "not_run_for_single_question_smoke"}
        ),
        "corpus_id": corpus.corpus_id,
        "corpus_version": corpus.corpus_version,
    }
    serialized = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()
