# Stage 6 Hybrid Retrieval Design

## Scope

Stage 6 combines Stage 3 semantic retrieval and PostgreSQL lexical retrieval for
synthetic/public content only. It adds neither reranking nor generation, and keeps
the Stage 4/5 RAG and citation contracts intact.

```text
untrusted query
  -> semantic candidates (pgvector)
  -> lexical candidates (PostgreSQL simple FTS + exact identifier candidates)
  -> deterministic Reciprocal Rank Fusion
  -> bounded final Top-K chunks
  -> existing context / citation validation when RAG calls the same seam
```

Both repository statements include the same optional `allowed_document_ids` shape.
It is not authorization: Stage 9 must supply authorized IDs before either branch
executes. Until then the system uses only synthetic/public content.

## Lexical retrieval and exact identifiers

The Stage 6 Alembic migration creates a PostgreSQL GIN expression index over
`to_tsvector('simple', document_chunks.content)`. Queries use
`websearch_to_tsquery('simple', query)` and `ts_rank_cd` for lexical candidates.
The `simple` configuration is intentional for technical identifiers rather than
language stemming.

Identifier-like query tokens are detected locally, normalized to lowercase, capped
at five, and passed as parameterized PostgreSQL regular-expression values. Their
literal text is regex-escaped and surrounded by technical-ID boundaries: letters,
digits, hyphens, and underscores are identifier characters. Thus `KB_7` matches
literal `KB_7` but not `KBX7`, and `INC-1042` does not match `XINC-1042` or
`INC-10420`. For example, `INC-1042` can be returned as an exact lexical candidate
even if full-text tokenization separates the hyphen. Exact matches get lexical score
`1.0`; they remain candidates rather than being treated as trusted facts or
bypassing future authorization.

SQLite remains an isolated relational-test backend and is not described as a
PostgreSQL full-text implementation.

## Fusion and deterministic behavior

Candidate branches are independently bounded by
`EKAA_HYBRID_SEMANTIC_CANDIDATE_COUNT` and
`EKAA_HYBRID_LEXICAL_CANDIDATE_COUNT`. They merge by immutable chunk ID and use
Reciprocal Rank Fusion (RRF):

```text
rrf(chunk) = Σ 1 / (EKAA_HYBRID_RRF_RANK_CONSTANT + branch_rank)
```

Ranks start at 1. A chunk found by both branches gets two contributions. This avoids
pretending cosine similarity and PostgreSQL lexical rank are comparable numerical
scales. Equal fused scores sort by `chunk_id` ascending.

RRF determines order only. The `RetrievedChunk.score` consumed by the existing RAG
evidence threshold is deliberately the maximum branch confidence for that chunk
(semantic cosine score or lexical candidate score); the internal `fusion_score`
retains the RRF value. RRF contributions are never numerically added to evidence
confidence, but this maximum policy does deliberately compare branch confidences to
preserve the Stage 4 threshold contract. A rank-constant-dependent RRF value around
`0.016` must not cause otherwise qualifying evidence to be rejected, and
low-confidence evidence still does not qualify merely because it ranks highly.

This is threshold-compatible plumbing, not score calibration: cosine similarity and
`ts_rank_cd` have different meanings. Exact-ID lexical candidates receive `1.0` and
can qualify under the existing threshold. A non-exact lexical-only candidate uses its
raw `ts_rank_cd`; if it is below `EKAA_RAG_MIN_EVIDENCE_SCORE`, RAG safely refuses
instead of treating relative lexical rank as sufficient evidence. Future evaluation
must calibrate this policy on reviewed data before production use.

The selected candidate metadata always comes from the server retrieval boundary.
No provider output can add a source, citation, or identifier match.

## API and diagnostics

`POST /api/retrieval/search` stays read-only and returns sources only by default.
When its explicit `include_diagnostics` flag is true and hybrid retrieval is enabled,
it returns only four capped integer counts: semantic candidates, lexical candidates,
merged candidates, and exact-identifier candidates. It does not echo query text,
document content, raw branch scores, or future authorization decisions. RAG does not
publish these diagnostics.

Hybrid mode is off by default and requires enabled PostgreSQL/pgvector retrieval.
It is configured by bounded candidate-count and RRF settings; it introduces no
provider, external call, or public maintenance endpoint.

## Evidence, safety, and limitations

Retrieved text and queries remain untrusted data. RAG still performs evidence
threshold/context filtering before generation, and Stage 5 still validates claims
and server-generates citations only from final selected chunks. A hybrid candidate is
not evidence by itself.

No reranking, keyword synonym expansion, production-scale tuning, authentication,
protected documents, frontend, tools, agents, or memory is included. The deterministic
embedding provider and lexical fixtures are test mechanisms, not a production quality
claim. The opt-in disposable PostgreSQL verification is documented in the Stage 6
report; its local benchmark result is a regression check, not a production claim.

## Opt-in PostgreSQL benchmark guard

`tests/integration/test_pgvector_postgresql.py` contains an opt-in disposable-schema
benchmark at 512 synthetic/public chunks. It runs the actual semantic service and
hybrid service with the same mixed query after 3 warm-ups, takes the median of 9 `perf_counter`
measurements, and runs `EXPLAIN (ANALYZE, BUFFERS)` on the actual lexical repository
statement with no exact-ID branch to verify the GIN FTS index is usable. The plan is
executed with SQLAlchemy/DBAPI-bound parameters rather than literal SQL compilation,
which preserves PostgreSQL `REGCONFIG` typing and never interpolates query text.
Because a
512-row fixture may correctly favour a sequential scan under a developer machine's
cost settings, that plan transaction disables only sequential scans to test index
compatibility; timed service calls retain normal planner settings.

It fails when the hybrid median exceeds `max(0.5 seconds, 5 × semantic median)`.
That intentionally loose guard detects severe local regressions only; it is neither a
production SLO nor a claim about another machine. Against an explicitly disposable
pgvector database, run:

```powershell
$env:EKAA_TEST_POSTGRES_URL = "postgresql+psycopg://.../disposable_ekaa_test"
.venv\Scripts\python.exe -m pytest tests/integration/test_pgvector_postgresql.py -q -s
```

Record the printed `stage6_hybrid_latency` line and `stage6_fts_plan` output in the
Stage 6 report before changing acceptance from pending to passed.
