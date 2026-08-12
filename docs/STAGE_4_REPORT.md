# Stage 4 Report

Goal: Produce evidence-bounded answers from semantic retrieval without entering
claim-level citation, hybrid retrieval, authorization, or agent scope.

Current branch: main.

Repository status: Stage 4 implementation is uncommitted; no commit, push, external
provider call, production action, or protected-data operation was performed.

Files created: `backend/app/rag/`, RAG route, focused Stage 4 unit/integration tests,
and this design/report documentation.

Files modified: application wiring, API router, safe settings, environment example,
README, roadmap, and stage marker.

Implementation: Added a disabled-by-default `POST /api/rag/answer` endpoint and
the `RagService` application layer. It invokes semantic retrieval before generation,
filters low-score evidence, builds a whole-chunk character/token/source-bounded
context, and returns a deterministic insufficient-information response without an
LLM call when evidence is absent or cannot fit. Provider answers are accompanied by
only server-derived source metadata from the selected retrieval chunks.
Before returning provider output, a conservative lexical guard rejects meaningful
terms absent from the exact selected context and returns the deterministic
insufficient-information response instead. It is an intentionally limited Stage 4
guard, not Stage 5 claim-level entailment or citation validation.

Architecture decisions: A dedicated `/api/rag/answer` endpoint preserves Stage 1's
non-retrieval chat sandbox. The application service retrieves before generation,
rejects insufficient evidence before a provider call, and attaches only retrieval-
derived sources selected into the bounded context. Stage 5 remains responsible for
claim-level citation validation.

Tests: Grounded-answer fixture, score/no-result refusal, exact multi-source context
character/token limits (including source separators), source-ID integrity,
injection-shaped retrieved content,
retryable/permanent/malformed provider failures, unsupported-output rejection,
API-level empty/below-threshold/context-capacity refusals, safe API failures,
disabled RAG, and configuration dependency/bounds tests.

Test results: PASS locally — `83 passed, 1 skipped`, 90% overall coverage from
`pytest --cov=backend --cov-report=term-missing`; `ruff check .`, `ruff format
--check .`, `mypy backend`, and `git diff --check` passed. The existing opt-in
PostgreSQL + pgvector Stage 3 integration test was skipped because this shell had no
`EKAA_TEST_POSTGRES_URL` and Docker Desktop's daemon was unavailable. No Stage 4
migration or retrieval SQL changed; the Stage 4 service/API tests use deterministic
retrieval/provider fakes and make no network or paid-provider calls.

Manual verification: In-process ASGI tests verified the complete Stage 4 HTTP
contract, including grounded response metadata, deterministic no-evidence refusal,
safe provider-unavailable error, disabled configuration, and no model-owned source
metadata.

Known limitations: The deterministic embedding provider is not a retrieval-quality
claim. The lexical output guard does not prove entailment and may reject valid
paraphrases; no claim-level grounding validator, citation UX, hybrid retrieval, reranking,
authorization, real enterprise data, streaming, tools, agents, or frontend exists.

Security review: The question and retrieved chunks are untrusted data; the provider
receives only bounded selected context. RAG is disabled by default and protected
content remains prohibited until Stage 9 authorization.

Regression review: Existing Stage 0–3 tests remained in the full suite. The Stage 1
`/api/chat` policy remains unchanged; Stage 4 is a separate endpoint. Stage 3
retrieval, embeddings, migrations, and schema were not changed.

Acceptance criteria: PASS locally — the tested RAG path sends only selected bounded
retrieval context to the provider and returns only source IDs copied by the server
from actual selected retrieval chunks. Independent code/design review remains
required before Stage completion.

Next stage: Stage 5 — Citation & Grounding, only after explicit user authorization.

No next-stage implementation performed.
