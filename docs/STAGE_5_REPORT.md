# Stage 5 Report

Goal: Make factual claims in Stage 4 RAG responses individually verifiable without
adding hybrid retrieval, reranking, authorization, or automated-judge scope.

Current branch: main.

Repository status: Stage 5 implementation is uncommitted. No push, external provider
call, production action, Docker action, database connection, or protected-data
operation was performed.

Files created: Citation parser/validator module, focused citation tests, this design,
and this report.

Files modified: RAG response models/service/prompt/API schema, context internal
evidence tracking, safe settings/environment example, README, roadmap, and stage
marker.

Implementation: Stage 5 replaces the free-text Stage 4 provider answer contract with
a strict JSON list of factual claim proposals and selected chunk IDs. The provider
IDs are only untrusted proposals. An explicit `{"claims":[]}` is valid when selected
evidence supports no factual claim; it produces the deterministic insufficient-
information answer with zero claims/citations while retaining honest provider usage.
`CitationValidator` validates non-empty proposals solely against exact final-context
chunks, requires a bounded supporting source sentence, generates public citation IDs
server-side, and copies provenance/excerpts from retrieval data. Malformed output has
the existing safe invalid-generation response. Unsupported claims receive an explicit
status/reason and cause the top-level answer to become the deterministic insufficient-
information response.

Architecture decisions: Citation parsing and validation are application-layer,
provider-neutral code; the route only maps response models and safe errors. The
internal context keeps selected chunks only for request-local validation; it does not
add persistence or return raw context. Excerpt truncation is rejected rather than
silently altering evidence.

Tests: Citation JSON shape, citation existence, selected-source metadata accuracy,
sentence-level entailment-oriented support/contradiction fixtures, adversarial
reordered subject/object and repeated-token claims, direct negative-evidence versus
positive-claim fixtures for `not`, `no`, `never`, `without`, `didn't`, and `can't`,
invented IDs, missing page/section, excerpt boundary rejection, API response schema,
empty provider claim proposal, no-evidence, provider failure, disabled configuration,
and existing regressions.

Test results: PASS locally — `109 passed, 1 skipped`, 90% overall coverage from
`python -m pytest --cov=backend --cov-report=term-missing`; `ruff check .`,
`ruff format --check .`, `mypy backend`, and `git diff --check` passed. The opt-in
PostgreSQL + pgvector integration test remains skipped without an explicit disposable
`EKAA_TEST_POSTGRES_URL`; Stage 5 changes no vector SQL or migration and all Stage 5
tests use in-process fakes.

Manual verification: In-process ASGI tests verified the full Stage 5 response
contract, safe provider failures, disabled RAG, deterministic no-evidence refusal,
and an invented provider chunk ID that is flagged without being exposed as a citation.

Known limitations: The validator is lexical and sentence-local, not semantic
entailment. It requires the ordered meaningful claim-token sequence, including token
multiplicity, to occur in one source sentence. It rejects a positive claim when that
sentence has `not`, `no`, `never`, `without`, or a normalized negative contraction,
but treats all sentence-level markers as relevant and cannot determine grammatical
scope, double negatives, synonymy, subject/object semantics, or complex multi-sentence
reasoning. Citation quality baseline is the deterministic fixture suite, not a
production quality metric. No citation UX frontend or semantic/LLM judge exists.

Security review: Citation IDs and metadata are server-derived from request-selected
retrieval evidence. Model-invented IDs are not exposed. Synthetic/public-only data,
bounded provider context, safe errors, and untrusted-content isolation remain in
force.

Regression review: Stage 1 chat remains separate. Stage 2 ingestion and Stage 3
embeddings/retrieval persistence are unchanged. Stage 4 no-evidence and safe provider
failure behavior remains unchanged in intent; the response contract now uses claims
and citations rather than top-level selected sources.

Acceptance criteria: PASS locally — tested claims have traceable citations copied
from exact request-selected chunks, while unsupported claims are explicit and are not
returned in the top-level factual answer. Independent review remains required before
Stage completion.

Next stage: Stage 6 — Hybrid Retrieval, only after explicit user authorization.

No next-stage implementation performed.
