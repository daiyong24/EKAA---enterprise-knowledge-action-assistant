# Stage 7 Reranking Design

## Scope

Stage 7 improves the order used for final RAG context selection over synthetic or
explicitly public content only. It preserves Stage 6 hybrid candidate retrieval and
all Stage 4/5 evidence and citation controls.

```text
untrusted question
  -> bounded hybrid candidates
  -> existing retrieval evidence threshold
  -> bounded reranker candidate set
  -> reranked final Top-K context selection
  -> existing prompt, claim validation, server-built citations
```

The public read-only retrieval API remains a Stage 6 candidate inspection endpoint;
it is not reranked. Reranking is deliberately applied inside the RAG application
service because its sole purpose is selecting the final bounded generation context.

## Provider boundary and score mapping

`RerankerProvider` is an async, provider-neutral protocol receiving only a query and
the already-retrieved candidate IDs/content. It returns exactly one normalized score
in `[0, 1]` for every supplied candidate, under a declared model name. A response
with a wrong model, unknown/missing/duplicate IDs, non-finite score, or out-of-range
score is rejected.

The included `DeterministicOverlapReranker` is the sole Stage 7 provider. It is local
and sends no data to a third party. Its score is exactly:

```text
unique normalized query terms also in candidate content / unique normalized query terms
```

Terms are lowercase ASCII alphanumeric sequences with optional internal `-` or `_`.
The score is used only for ordering. Equal scores preserve the prior deterministic
hybrid rank, then use `chunk_id` as a final stability key. Crucially, rerank score
never overwrites `RetrievedChunk.score`: that existing score remains the Stage 4
evidence-confidence value and is still checked before reranking.

This provider is a deterministic test/demo baseline, not a semantic reranker,
production relevance model, calibration, or evidence-entailment mechanism. A future
approved hosted/local model must implement this protocol, be tested with deterministic
mocks, and receive a separate privacy/cost review before real enterprise data.

## Bounds and safe fallback

`EKAA_RERANKING_CANDIDATE_COUNT` bounds the number retrieved before final context
selection and is constrained to the retrieval maximum and at least the RAG final
Top-K. `EKAA_RERANKING_TIMEOUT_SECONDS` is bounded to 10 seconds. Reranking requires
hybrid retrieval and is disabled by default. When disabled, RAG retains the exact
pre-Stage-7 retrieval Top-K request size and behavior.

The reranker receives only chunks that have already passed
`EKAA_RAG_MIN_EVIDENCE_SCORE`; it cannot promote a low-confidence lexical or
semantic candidate into evidence. After ordering, RAG still takes final Top-K and
`ContextBuilder` independently enforces maximum sources, exact context characters,
and conservative context tokens. Whole chunks only are selected.

Scores must be actual numeric `int`/`float` values in `[0, 1]`; booleans are rejected
even though Python treats them as integer subclasses. This prevents malformed
provider data from accidentally becoming ordering input.

An async deadline uses `asyncio.wait_for`. Timeout, provider exception, or malformed
provider output logs only a reason/type and returns the original stable hybrid order.
The request does not fail, no new source is introduced, and no private provider
detail is logged or returned. This prevents reranking availability from weakening
BR-001/2/3/7/8/9. Context, source provenance, and claim citations remain entirely
server-derived from final selected chunks.

## Focused comparison

`tests/unit/test_reranking.py::test_declared_synthetic_fixture_has_top_one_gain_at_bounded_local_cost`
measures a declared four-query synthetic/public fixture. In every fixture the hybrid
baseline's first chunk is a partial/noise candidate and the expected supporting
chunk is second. The deterministic reranker improves top-1 expected-chunk hit rate
from **0/4 (0%)** to **4/4 (100%)**. After one warm-up per fixture, it times the four
actual reranking service calls and enforces every call below **100 ms** on the local
test process. Run the exact focused check with:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_reranking.py -q
```

The exact Top-1 fixture gain is reproducible; wall-clock timing is deliberately
machine- and run-specific and is not recorded as a portable measurement. The
deterministic local provider has no external provider fee.

This is an exact automated fixture result, not a claim about broad user queries,
production latency, provider-model quality, or citation correctness. The 100-ms
check is a local regression guard, not a latency target or SLO. Its small,
purpose-built set is intentionally only enough to decide that this local baseline is
enabled for Stage 7 development; Stage 8 must establish a reviewed versioned
evaluation dataset and broader quality/cost gates.

## Deliberate exclusions

No hybrid expansion, semantic judge, model fine-tuning, broad evaluation platform,
authentication/authorization, protected data, tools, agents, chat memory, frontend,
or production provider call is introduced. Stage 9 authorization remains required
before protected content may enter any retrieval or reranking path.
