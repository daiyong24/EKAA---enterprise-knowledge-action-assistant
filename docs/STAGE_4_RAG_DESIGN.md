# Stage 4 Basic RAG Design

Stage 4 introduces the bounded flow below for synthetic or explicitly public content
only:

```text
untrusted question -> query embedding -> pgvector Top-K retrieval
-> evidence threshold -> bounded context -> provider-neutral generation
-> answer + server-derived source metadata
```

`POST /api/rag/answer` is deliberately separate from Stage 1's restricted
`/api/chat` sandbox. The route performs transport and safe-error mapping only;
`RagService` orchestrates retrieval, evidence decisions, bounded context, and the
existing provider-neutral `LLMProvider` boundary.

## Evidence and source integrity

RAG retrieves `EKAA_RAG_RETRIEVAL_TOP_K` chunks and keeps only scores at or above
`EKAA_RAG_MIN_EVIDENCE_SCORE`. `ContextBuilder` then accepts whole chunks only,
within all three deterministic limits:

- maximum source count;
- maximum context characters; and
- maximum estimated context tokens (`ceil(characters / 4)`).

The token estimate is a conservative, provider-neutral safety budget; it is not a
claim of exact tokenizer accounting. A chunk is never silently partially copied. If
the configured minimum number of qualified, fitting sources is not met, EKAA returns
exactly `Insufficient information is available in the knowledge base.` with zero
provider usage and no sources. The LLM is not called in this path, preventing a
fallback to model memory.

Each candidate is evaluated as the complete final context string, including the
two newline separators between sources. The stored character and token counts are
calculated from that exact final string, so the returned context cannot undercount
multi-source separators against either configured budget.

Source metadata in the response is created only from chunks selected into the exact
context: document ID/name, chunk ID/index, and available page/section. It does not
use or parse model-provided source IDs. Stage 4 asks the provider not to emit citation
labels and rejects obvious model-owned `[source: ...]`, `[citation: ...]`, or
line-based `Source:`/`Citation:` labels before responding. Stage 5 will add
claim-level citation syntax, validation, excerpts, and unsupported-claim handling.
Stage 4 therefore does not claim that individual model sentences have been validated
for entailment.

Before exposing a provider answer, Stage 4 additionally applies a conservative,
provider-independent lexical guard: every meaningful answer term must appear in the
exact selected context (common function words are ignored). An answer that adds an
absent duration, person, system name, or other term is replaced with the deterministic
insufficient-information response and no sources. The rejected provider text is not
returned. If a provider call occurred before this guard, its normalized usage remains
in the response for honest accounting. This catches demonstrably unsupported lexical
additions but is not a proof of entailment: it can reject valid paraphrases and can
not fully detect negation, misleading composition, or every hallucination. Those
claim-level grounding limits remain Stage 5 work.

## Prompt-injection and privacy boundaries

The system prompt instructs the provider to answer only from supplied evidence and
to treat both the question and the retrieved content as untrusted data. Each selected
chunk is surrounded by explicit `BEGIN/END UNTRUSTED RETRIEVED SOURCE` delimiters;
instructions appearing inside a document cannot alter EKAA policy, reveal secrets,
invoke tools, or override the insufficient-evidence behavior.

No question, chunk content, provider payload, secret, database detail, or internal
exception is put in public errors or application logs. A configured external LLM
receives only the question and bounded selected context required for this request.
Provider retention, data-use, DPA, region, and cost decisions remain unknown until a
real provider is explicitly approved. Do not enable it with enterprise or protected
content before Stage 9 implements fail-closed authorization before retrieval.

## Activation and failure behavior

RAG is disabled by default. `EKAA_RAG_ENABLED=true` is valid only when both
`EKAA_EMBEDDING_ENABLED=true` with a PostgreSQL + pgvector URL and the existing LLM
provider configuration are enabled. Generation uses Stage 1's bounded retry settings
for timeout/temporary provider errors. The API returns stable safe errors for disabled
RAG, retrieval failure, unavailable provider, provider rejection, or invalid provider
output. No paid or live provider call is made in tests.

No hybrid retrieval, reranking, authentication, protected documents, tools, agents,
memory, streaming, claim-level citations, or frontend is implemented in this stage.
