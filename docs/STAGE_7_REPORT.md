# Stage 7 Report

Goal: Improve final RAG context selection through measured, bounded reranking while
preserving grounded retrieval, evidence thresholds, and server-built citations.

Current branch: main.

Repository status: Stage 7 implementation is uncommitted. No push, production
provider call, protected-data operation, database operation, or Docker operation was
performed.

Files created: Provider-neutral reranking models/protocol/service, focused reranking
tests, this design, and this report.

Files modified: RAG orchestration, retrieval metadata, settings/environment example,
application wiring, project README, roadmap, and active-stage marker.

Implementation: RAG retrieves a bounded hybrid candidate set, filters it by the
existing evidence threshold, and then optionally asks a reranker to order that set.
It still keeps only the configured final Top-K before `ContextBuilder` applies exact
source, character, and conservative-token budgets. The local deterministic provider
maps a score to query-token coverage and has no external call/cost. Score ties retain
the prior hybrid rank then `chunk_id`; rerank score does not replace evidence score.
The reranker is async and bounded by `asyncio.wait_for`; timeout, exception, or bad
output returns the original stable hybrid order. Sources and citations still come
only from final server-selected chunks.

Architecture decisions: Reranking is an application boundary inside `RagService`,
not an API-layer or persistence concern. The public retrieval endpoint remains a
candidate inspection endpoint and does not expose reranker scores. This avoids a
public contract expansion and preserves the exact Stage 4/5 source/citation seam.

Tests: Ordering and score mapping, tie stability, invalid model/IDs/scores,
provider failure and deadline fallback, deterministic mock reproducibility,
configuration guards, final-context source selection, citation integrity, and
below-threshold no-reranker/no-LLM regression behavior. Existing context/no-evidence
regressions also run. The declared focused four-query synthetic
fixture verifies 0/4 baseline Top-1 expected-chunk hits versus 4/4 after reranking
and a less-than-100-ms local service-call guard.

Test results: PASS — `141 passed`, `4 skipped`, and 91% overall coverage. The four
skips are the existing opt-in PostgreSQL/pgvector integration tests, which require
an explicit disposable `EKAA_TEST_POSTGRES_URL`; Stage 7 does not alter database or
vector behavior. Ruff, formatting, strict mypy, and `git diff --check` passed. The
focused reranking comparison has an exact deterministic Top-1 gain from `0/4` to
`4/4`. Its four post-warm-up calls are guarded below 100 ms in the local test
process; no fixed timing number is reported because wall-clock values are machine-
and run-specific rather than portable performance evidence.

Manual verification: Not required; no live provider, database, or Docker activity
is part of Stage 7's deterministic local baseline.

Known limitations: The deterministic overlap provider is lexical and fixture-driven;
it is not a production semantic reranker. The focused 4-query comparison is not a
broad evaluation, latency SLO, or evidence-entailment proof. Stage 8 must establish
a reviewed versioned evaluation corpus and wider quality/cost gates. A future
provider requires an explicit privacy/cost/data-use review before real enterprise
content, and Stage 9 authorization must precede protected retrieval/reranking.

Security review: Candidate text and question remain untrusted. Only already-selected
synthetic/public candidate content enters the reranker. It cannot introduce sources,
change evidence confidence, bypass context limits, create citations, or reveal
provider errors. No content/query/raw score is placed in the public API or logs.

Regression review: Existing RAG refusal, context bounds, citation validation, hybrid
confidence, and retrieval API contracts remain in force.

Acceptance criteria: PASS locally — the focused fixture gain is documented, the
deterministic provider adds no external latency/cost, and the full regression suite
passes. No broad quality or production-performance claim is made.

Next stage: Stage 8 — RAG Evaluation, only after explicit user authorization.

No next-stage implementation performed.
