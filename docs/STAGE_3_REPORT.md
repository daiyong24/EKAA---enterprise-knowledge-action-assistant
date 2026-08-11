# Stage 3 Report

Goal: Add reproducible semantic Top-K retrieval over synthetic/public normalized
chunks without generation or protected content.

Current branch: main.

Repository status: Stage 3 implementation worktree changes are uncommitted; no
commit, push, pull, or external provider action was performed. A disposable local
PostgreSQL + pgvector Docker test database was created solely for the integration
test and removed immediately afterwards.

Files created: `backend/app/embeddings/`, retrieval route, Stage 3 Alembic migration,
Stage 3 design/report, and focused unit/integration tests.

Files modified: stage markers, settings, application wiring, API router, database
models, dependency manifest, environment example, and README.

Implementation: Provider-neutral batch embeddings, deterministic local provider,
versioned `chunk_embeddings` with pgvector storage/model/dimension metadata, atomic
batch persistence, and `POST /api/retrieval/search` semantic Top-K contract with
stable ordering and source provenance. The API is disabled unless PostgreSQL pgvector
is explicitly configured.

Architecture decisions: PostgreSQL + pgvector is the only deployed vector path;
SQLite is test-only relational convenience. Embedding versions coexist for controlled
model changes and rollback. Authorization is not implemented, but the retrieval
query accepts a future document-ID scope in its primary query shape.

Tests: deterministic provider, provider model/dimension mismatch, partial batch
failure/no partial write, deterministic score/tie ordering, no results, provenance,
configuration guard, API disabled contract, pgvector-query shape, and isolated
Alembic schema migration.

Test results: PASS — 65 passed, including the opt-in PostgreSQL + pgvector
end-to-end test, with 90% overall coverage; Ruff, formatting, strict mypy,
`git diff --check`, and the Stage 3 pre-commit hooks all passed. The isolated
Alembic migration test also passed.

Manual verification: no live provider was contacted. The opt-in integration test
ran against an isolated local PostgreSQL 16 + pgvector Docker container using a
random test credential and schema; it applied EKAA's Alembic migration, seeded
synthetic EKAA models, and verified the application embedding, persistence, and
retrieval path before the container was removed. SQLite remains limited to isolated
relational migration mechanics and is not substituted for vector search.

Known limitations: The local provider is not a quality model; no approximate index,
background job, authorization, real enterprise content, RAG generation, citation,
hybrid search, or reranking exists.

Security review: No embedding provider secrets or payload content are logged or
returned in errors. The endpoint only becomes available with PostgreSQL configured;
Stage 9 authorization remains required before protected data.

Regression review: Stage 1 LLM policy and Stage 2 ingestion behavior remain
unchanged; Stage 3 does not expose free-text answers or execute writes outside its
own database transaction.

Acceptance criteria: PASS — relevant synthetic chunks were retrieved reproducibly
through the real PostgreSQL + pgvector application path, and the model
migration/re-embedding strategy is documented.

Next stage: Stage 4 — Basic RAG, only after explicit user authorization.

No next-stage implementation performed.
