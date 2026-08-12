# Stage 5 Citation & Grounding Design

Stage 5 turns the Stage 4 evidence-bounded answer into an individually verifiable
claim contract for synthetic or explicitly public content only.

```text
retrieval-selected chunks -> bounded context -> provider claim proposals
-> strict JSON parsing -> selected-source / excerpt validation
-> server-built citations + supported claims, or an explicit unsupported result
```

## Public response contract

`POST /api/rag/answer` returns `answer`, normalized `usage`, `claims`, and
`citations`. A supported claim has `status: "supported"` and references one or more
server-generated `citation-*` IDs. Each citation contains document ID/name, chunk
ID/index, a bounded exact source excerpt, and the available page number and section.
If a source has no page or section, its JSON field is `null`; EKAA never invents a
location.

The provider returns an internal, constrained proposal only:

```json
{"claims":[{"text":"The recovery target is four hours.","citation_chunk_ids":["chunk-123"]}]}
```

When high-scoring selected evidence still supports no factual claim, the provider
must return `{"claims":[]}`. This is a valid no-claim proposal, not malformed
output: EKAA returns the deterministic insufficient-information answer with empty
claims/citations and preserves the provider usage because the call already occurred.

The proposal contains no public citation metadata. EKAA resolves IDs exclusively
against chunks selected into this request's final context, creates the public IDs,
and copies all provenance from those source objects. A model-invented, stale, or
unselected chunk ID is never exposed as a citation.

## Validation and unsupported claims

`CitationOutputParser` accepts the explicit empty proposal and otherwise rejects
malformed JSON, extra fields, blank values, and configured claim-count/claim-length
violations as invalid provider output. A non-empty valid proposal is then passed to
`CitationValidator`:

1. Each proposed chunk ID must exist in the exact selected context.
2. Every proposed citation must contain one complete source sentence whose normalized
   meaningful tokens contain the complete normalized claim-token sequence in order,
   including repeated tokens.
3. A sentence with `not`, `no`, `never`, `without`, or a normalized negative
   contraction (for example `didn't` or `can't`) cannot support a claim lacking a
   negation marker. EKAA deliberately treats every sentence-level marker as relevant
   rather than guessing grammatical scope.
4. The sentence must fit `EKAA_CITATION_MAX_EXCERPT_CHARACTERS`; EKAA does not
   truncate an excerpt because a truncation could change its support.

This is an entailment-oriented deterministic baseline rather than a semantic proof.
It rejects reordered subject/object fixtures, repeated-token claims, direct negative
evidence paired with a positive claim, changed numbers, missing terms, and unrelated
sentences, but may also reject valid paraphrases or multi-sentence reasoning. Its
negation rule is intentionally sentence-level and cannot determine grammatical scope,
synonymy, double negatives, or broader logical entailment. It does not use an LLM
judge and makes no citation-quality claim beyond the recorded tests.

If a claim is missing a citation, references a chunk not selected for the request,
or lacks a supporting bounded excerpt, it is returned with `status: "unsupported"`
and a stable reason code. EKAA replaces the answer with the fixed insufficient-
information response. Unsupported claims receive no citation IDs; citations for any
separately supported claims remain traceable for the UI, while the top-level answer
does not present a partly unsupported model response as fact. Provider usage remains
honestly reported if the provider was called.

## Safety and scope boundaries

BR-001/2 are preserved: retrieval still occurs before generation, no qualifying
evidence makes no provider call, and unsupported generated claims are not returned
as an answer. BR-003 is strengthened by citation metadata copied only from selected
chunks. Retrieved text, questions, and provider output remain untrusted (BR-007).
Errors remain stable and do not disclose provider payloads or source data (BR-008).
The provider gets only the bounded context needed for a request (BR-009).

This stage adds no hybrid/keyword retrieval, reranking, semantic judge, authorization,
protected content, tools, agents, memory, frontend, or production-provider calls.
Authorization remains mandatory before protected content can enter retrieval in
Stage 9.
