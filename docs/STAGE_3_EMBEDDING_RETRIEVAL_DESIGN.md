# Stage 3 Embedding and Vector Retrieval Design

Stage 3 adds this bounded flow for only synthetic or explicitly public content:

```text
completed document_chunks -> controlled embedding batch -> chunk_embeddings
query -> query embedding -> PostgreSQL pgvector Top-K -> traceable chunks
```

`EmbeddingProvider` is provider-neutral and batch-oriented. The shipped
`DeterministicHashEmbeddingProvider` is a local development/test implementation:
it computes normalized signed feature-hash vectors without a network call, API key,
or provider data transfer. It makes pipeline behavior and tests reproducible, but it
is not a claim of production retrieval quality. No live paid embedding call is made
by tests. A real provider requires a later explicit provider/privacy/cost review;
only the minimum normalized chunk text needed for embedding may be transmitted and
providers must not receive secrets or protected content.

The canonical deployed storage/query path is PostgreSQL with the `pgvector`
extension, created by migration `20260812_02`. The `chunk_embeddings` table stores
the pgvector value alongside `embedding_model`, `embedding_dimensions`, the source
chunk checksum, and timestamp. SQLite can execute isolated relational migration and
unit tests, but it is never described as a semantic/vector search backend and the
retrieval endpoint refuses to enable outside PostgreSQL.

Embeddings are versioned rather than overwritten. The uniqueness boundary is
`(chunk_id, embedding_model, embedding_dimensions)`. To change model or dimension:

1. Add the new provider configuration and run the controlled batch job for its new
   model/dimension pair; old vectors remain available for rollback.
2. Validate retrieval against declared synthetic fixtures before selecting the new
   configuration for queries.
3. Switch the query configuration atomically in deployment, then monitor and retain
   the old version until a documented rollback window closes.
4. Delete an obsolete embedding version only through a future retention-approved
   migration/operation; Stage 3 performs no destructive cleanup.

The local deterministic provider versions its tokenization as part of the embedding
model ID. `deterministic-hash-v1` preserves the original legacy tokenizer so vectors
already stored under that ID remain query-compatible. It is retained as the general
configuration default for existing databases. `deterministic-hash-v2` uses the
correct Unicode word-character tokenizer and is selected explicitly by the Stage 21
local demo. These are deterministic feature-hash utilities, not semantic embedding
models or production retrieval-quality claims. Never change an algorithm while
reusing its old model ID: query and stored vectors must use the same exact version.

For an existing v1 database, retain the v1 query setting while running the controlled
maintenance command with `EKAA_EMBEDDING_MODEL=deterministic-hash-v2` in that
maintenance process. Verify that every completed chunk has a v2 embedding with the
configured dimension before switching the API query setting to v2 and recreating the
API. The v1 and v2 rows can coexist; preserve v1 for rollback. Do not switch queries
to a model/dimension pair until its completed-chunk coverage is complete.

Each batch is validated before persistence for response count, model and dimension,
and vector length. A successful batch commits in one transaction. If a later batch
fails, earlier committed batches remain, the failed batch has no partial vector rows,
and the run reports its remaining work for a safe retry. Database errors are safe
failures and do not expose content, SQL, connection details, or provider payloads.
On PostgreSQL, insert uses the unique model/dimension/chunk constraint with `ON
CONFLICT DO NOTHING`, so a repeated or concurrent maintenance run cannot duplicate
vectors. A conflicting existing row is counted as already complete rather than
rewritten.

The stored source checksum is a freshness witness. Stage 2 chunks are immutable; if
a future stage permits chunk-content mutation, it must invalidate that chunk's
embedding versions and run an explicit re-embedding migration before retrieval. It
must not silently reuse a vector whose `content_checksum` differs from the chunk.

`POST /api/retrieval/search` is the intentionally thin Stage 3 API seam. It accepts
a bounded query and Top-K value and returns chunk content with document ID/name,
chunk ID/index, cosine similarity score, and available page/section metadata. The
score is `1 - cosine_distance`: higher is better; normalized vectors generally yield
a value in the expected `[-1, 1]` cosine-similarity range.
It does not call an LLM or create a RAG answer. Retrieved text remains untrusted
data. Until Stage 9, the system is restricted to synthetic/public data. The query
builder already accepts a future `allowed_document_ids` scope and applies it in the
same pgvector query; it is an authorization-ready shape, not an authorization
implementation. Stage 9 must supply a fail-closed scope before protected content is
introduced.

At Stage 3 demo scale the model/dimension-scoped pgvector query is correctness-first
and intentionally has no approximate-vector index. An index choice requires a fixed
model dimension, representative scale, and measured query plans; that optimization
is deferred rather than pretending a generic index safely supports arbitrary model
versions.

## Controlled embedding operation

Embedding is intentionally not triggered by upload and has no public write API. On a
provisioned, dedicated PostgreSQL + pgvector environment, set the explicit embedding
configuration and run:

```powershell
python -m backend.app.embeddings.cli embed-pending
```

The command exits `2` when embedding is disabled, `1` when a batch fails cleanly,
and `0` after all pending chunks are processed. It prints only aggregate counts, not
chunk text, filenames, checksums, or provider payloads. Run it again after a
transient failure; PostgreSQL conflict-safe persistence makes retries idempotent.

For a real integration run, set `EKAA_TEST_POSTGRES_URL` to a disposable test
database whose role may create temporary schemas and has pgvector installed. The
`test_pgvector_postgresql.py` test creates an isolated schema, runs EKAA Alembic
migrations through `20260812_02`, seeds EKAA `Document`/`DocumentChunk` models, and
invokes the real `EmbeddingService` and `SemanticRetrievalService`. It verifies real
pgvector similarity, model/dimension filters, score semantics, deterministic tie
ordering, provenance, and idempotent conflict behavior. It deliberately skips
without that explicit test DSN rather than substituting SQLite.

Dependency added:

- `pgvector`: SQLAlchemy type integration and PostgreSQL pgvector distance operator.
