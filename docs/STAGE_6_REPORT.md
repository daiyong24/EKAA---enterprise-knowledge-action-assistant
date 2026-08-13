# Stage 6 Report

Goal: Combine semantic and PostgreSQL full-text retrieval for synthetic/public
content without adding reranking or changing the grounded RAG/citation contract.

Current branch: main.

Repository status: Stage 6 implementation is uncommitted. No push, production
provider call, or protected-data operation was performed. A disposable local
PostgreSQL 16 + pgvector Docker container was used only for the opt-in integration
tests and removed immediately afterwards.

Files created: Hybrid application service, lexical persistence boundary, PostgreSQL
full-text migration, focused hybrid tests, this design, and this report.

Files modified: retrieval models/service/API wiring, safe settings/environment
example, README, roadmap, and active-stage marker.

Implementation: PostgreSQL lexical candidates use `simple` full-text search and a
GIN expression index. Identifier-like tokens receive a bounded exact-match candidate
path through parameterized PostgreSQL regex with literal escaped text and technical-ID
boundaries: `INC-1042` cannot match `XINC-1042` or `INC-10420`, and `KB_7` cannot
match `KBX7`.
The application layer obtains independently bounded semantic and lexical candidates,
merges duplicate chunk IDs, and applies documented deterministic RRF. RRF is an
ordering value only: `RetrievedChunk.score` retains the maximum branch evidence
confidence for the pre-existing RAG threshold, while internal `fusion_score` retains
the RRF value. This deliberately compares branch confidence values for threshold
compatibility but never adds branch scores to RRF; it is not cross-model score
calibration. Exact lexical ID candidates use confidence 1.0, while lexical-only
non-exact candidates retain raw `ts_rank_cd` and can safely fail the existing RAG
threshold. The existing RAG service receives the hybrid service through the same
read-only retrieval protocol, so context and claim citations remain server-derived
from final selected chunks. Optional API diagnostics are capped counts only.

Architecture decisions: SQL remains in a replaceable repository boundary, rank
fusion is provider/database-score agnostic application logic, and HTTP remains a
thin response/error mapper. Both branch queries retain the future
`allowed_document_ids` predicate shape. Exact identifiers improve candidate recall
but do not bypass the future authorization boundary.

Tests: Natural-language/mixed identifier fixture, semantic/lexical duplicate merge,
deterministic ties, empty semantic and lexical branches, exact-ID normalization and
bounds, future scope-filter forwarding, API safe-count diagnostics, literal technical
identifier boundaries, PostgreSQL SQL shape, configuration bounds, SQLite migration
compatibility, and opt-in real PostgreSQL 512-chunk lexical-index plan/identifier
fixtures. The opt-in benchmark warms actual semantic and hybrid services three times
with the same mixed query, records median `perf_counter` timing across nine repetitions, runs `EXPLAIN ANALYZE`
against the lexical repository statement through bound DBAPI parameters (not literal
SQL compilation), and guards hybrid latency at
`max(0.5 seconds, 5 × semantic median)` on the same local machine. Hybrid-to-RAG API
tests verify default-threshold qualifying evidence reaches the provider and produces
server-validated citations, while low-confidence evidence still refuses without a
provider call. Existing RAG/citation regressions run in the full suite.

Test results: PASS — `128 passed`, 92% overall coverage; Ruff, formatting, strict
mypy, and `git diff --check` passed. This includes the four opt-in PostgreSQL +
pgvector tests against an isolated local PostgreSQL 16 container. SQLite is not
substituted for full-text or vector behavior.

Manual verification: The disposable PostgreSQL 16 + pgvector run applied EKAA's
migrations through `20260814_03`; all four opt-in tests passed. Its 512-chunk
benchmark used the actual service paths and recorded a semantic median of 10.014 ms
and hybrid median of 15.514 ms, below the 500 ms local guard. `EXPLAIN ANALYZE`
used `ix_document_chunks_content_fts_simple` with a Bitmap Index Scan; execution
time was 0.112 ms. The container and temporary schemas were removed after testing.

Known limitations: The reproducible benchmark guard is deliberately a loose local
regression tripwire, not a production target; its median varies by developer machine.
Candidate quality is demonstrated only by deterministic synthetic fixtures. No
reranker or production tuning exists.

Security review: Only synthetic/public content remains permitted. Inputs and
retrieved text remain untrusted. Diagnostics contain capped counts only. No provider,
protected-data, auth, or external action is introduced.

Regression review: Stage 1 chat remains separate. Stage 2 ingestion and Stage 3
embedding persistence are unchanged except for a PostgreSQL lexical index. Stage 4
RAG evidence gating and Stage 5 claim/citation validation continue to operate after
hybrid final selection.

Acceptance criteria: PASS — the deterministic mixed fixture matches semantic-only
while adding identifier evidence; the real disposable PostgreSQL + pgvector run
verified the GIN plan and the 512-chunk hybrid median (15.514 ms) remained within the
local guard.

Next stage: Stage 7 — Reranking, only after Stage 6 acceptance passes and explicit
user authorization.

No next-stage implementation performed.
