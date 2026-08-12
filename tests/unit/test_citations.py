import pytest

from backend.app.embeddings.models import RetrievedChunk
from backend.app.rag.citations import (
    CitationOutputError,
    CitationOutputParser,
    CitationValidator,
    ProposedClaim,
)
from backend.app.rag.context import ContextBuilder


def _chunk(
    *,
    chunk_id: str = "chunk-1",
    content: str = "The synthetic recovery target is four hours. Review monthly.",
    page_number: int | None = 7,
    section: str | None = "Recovery",
) -> RetrievedChunk:
    return RetrievedChunk(
        document_id="document-1",
        document_name="synthetic-runbook.md",
        chunk_id=chunk_id,
        chunk_index=4,
        content=content,
        score=0.9,
        page_number=page_number,
        section=section,
    )


def _context(*chunks: RetrievedChunk):
    return ContextBuilder(
        max_characters=10_000, max_estimated_tokens=10_000, max_sources=5
    ).build(list(chunks))


def test_citation_parser_rejects_non_json_or_extra_model_fields() -> None:
    parser = CitationOutputParser(max_claims=2, max_claim_characters=100)

    with pytest.raises(CitationOutputError):
        parser.parse("not JSON")
    with pytest.raises(CitationOutputError):
        parser.parse(
            '{"claims":[{"text":"A claim","citation_chunk_ids":["chunk-1"],'
            '"source":"invented"}]}'
        )


def test_citation_parser_allows_an_explicit_no_claim_proposal() -> None:
    parser = CitationOutputParser(max_claims=2, max_claim_characters=100)

    assert parser.parse('{"claims":[]}') == ()


def test_validator_copies_exact_selected_metadata_and_sentence_excerpt() -> None:
    claims, citations = CitationValidator(max_excerpt_characters=100).validate(
        (
            ProposedClaim(
                text="The recovery target is four hours.",
                citation_chunk_ids=("chunk-1",),
            ),
        ),
        context=_context(_chunk()),
    )

    assert claims[0].status == "supported"
    assert claims[0].citation_ids == ("citation-1",)
    assert citations[0].excerpt == "The synthetic recovery target is four hours."
    assert citations[0].document_id == "document-1"
    assert citations[0].document_name == "synthetic-runbook.md"
    assert citations[0].chunk_id == "chunk-1"
    assert citations[0].chunk_index == 4
    assert citations[0].page_number == 7
    assert citations[0].section == "Recovery"


def test_validator_flags_missing_or_invented_ids_and_no_page_without_leaking_them() -> (
    None
):
    validator = CitationValidator(max_excerpt_characters=100)
    claims, citations = validator.validate(
        (
            ProposedClaim(
                text="The recovery target is four hours.", citation_chunk_ids=()
            ),
            ProposedClaim(
                text="The recovery target is four hours.",
                citation_chunk_ids=("not-selected",),
            ),
            ProposedClaim(
                text="The recovery target is four hours.",
                citation_chunk_ids=("chunk-2",),
            ),
        ),
        context=_context(_chunk(chunk_id="chunk-2", page_number=None, section=None)),
    )

    assert [claim.unsupported_reason for claim in claims] == [
        "missing_citation",
        "citation_not_selected",
        None,
    ]
    assert citations[0].page_number is None
    assert citations[0].section is None


def test_validator_requires_a_cited_sentence_to_entail_orient_the_claim() -> None:
    claims, citations = CitationValidator(max_excerpt_characters=100).validate(
        (
            ProposedClaim(
                text="The recovery target is eight hours.",
                citation_chunk_ids=("chunk-1",),
            ),
        ),
        context=_context(_chunk()),
    )

    assert claims[0].status == "unsupported"
    assert claims[0].unsupported_reason == "citation_does_not_support_claim"
    assert citations == ()


@pytest.mark.parametrize(
    ("content", "claim"),
    [
        (
            "Alice approved the deployment plan.",
            "The deployment plan approved Alice.",
        ),
        (
            "The recovery target is four hours.",
            "The recovery recovery target is four hours.",
        ),
    ],
)
def test_validator_rejects_reordered_or_repeated_claim_tokens(
    content: str, claim: str
) -> None:
    claims, citations = CitationValidator(max_excerpt_characters=100).validate(
        (ProposedClaim(text=claim, citation_chunk_ids=("chunk-1",)),),
        context=_context(_chunk(content=content)),
    )

    assert claims[0].status == "unsupported"
    assert claims[0].unsupported_reason == "citation_does_not_support_claim"
    assert citations == ()


@pytest.mark.parametrize(
    ("content", "claim"),
    [
        (
            "Alice did not approve the deployment plan.",
            "Alice approved the deployment plan.",
        ),
        ("No deployment approval is permitted.", "Deployment approval is permitted."),
        (
            "Alice never approved the deployment plan.",
            "Alice approved the deployment plan.",
        ),
        ("The service runs without approval.", "The service runs approval."),
        (
            "Alice didn't approve the deployment plan.",
            "Alice approved the deployment plan.",
        ),
        ("The service can't process requests.", "The service process requests."),
    ],
)
def test_validator_rejects_positive_claims_against_negative_evidence(
    content: str, claim: str
) -> None:
    claims, citations = CitationValidator(max_excerpt_characters=100).validate(
        (ProposedClaim(text=claim, citation_chunk_ids=("chunk-1",)),),
        context=_context(_chunk(content=content)),
    )

    assert claims[0].status == "unsupported"
    assert claims[0].unsupported_reason == "citation_does_not_support_claim"
    assert citations == ()


def test_validator_allows_a_claim_that_preserves_source_negation() -> None:
    claims, citations = CitationValidator(max_excerpt_characters=100).validate(
        (
            ProposedClaim(
                text="Alice did not approve the deployment plan.",
                citation_chunk_ids=("chunk-1",),
            ),
        ),
        context=_context(_chunk(content="Alice did not approve the deployment plan.")),
    )

    assert claims[0].status == "supported"
    assert citations[0].excerpt == "Alice did not approve the deployment plan."


def test_validator_rejects_an_excerpt_that_would_need_truncation() -> None:
    content = (
        "The synthetic recovery target is four hours and this sentence is deliberately "
        "long."
    )
    claims, citations = CitationValidator(max_excerpt_characters=20).validate(
        (
            ProposedClaim(
                text="The recovery target is four hours.",
                citation_chunk_ids=("chunk-1",),
            ),
        ),
        context=_context(_chunk(content=content)),
    )

    assert claims[0].status == "unsupported"
    assert citations == ()
