# Stage 8 RAG Evaluation Design

## Scope and corpus

Stage 8 adds a local, reproducible evaluation boundary. It does not change retrieval,
reranking, RAG generation, citation validation, database behavior, or the public API.
The initial dataset is `docs/evaluation/golden_dataset_v1.json`: 50 reviewed synthetic
questions with declared expected chunk IDs. It is versioned, schema-validated,
fingerprinted with SHA-256, and rejects any provenance other than synthetic/public.
`docs/evaluation/synthetic_corpus_v1.json` is a separate versioned synthetic corpus:
every expected ID must exist there before replay starts. This distinction prevents a
golden annotation from pretending to be a retrievable source by itself.

Each corpus chunk is a reviewed synthetic factual passage that directly answers one
or more golden questions; it is not a copied question or a retrieval-label placeholder.
Review records confirm the expected chunk ID against that factual passage. This supports
testing the normal RAG path: retrieved evidence is passed as untrusted data, the model
proposes concise factual claims, and EKAA validates the claim against a selected source
sentence. The fixture remains a self-contained test corpus, not a claim about real-user
or enterprise-document quality.

Question-to-evidence relevance is a reviewed dataset annotation. It is not a claim
that the fixture measures user-population quality, provider quality, or production
retrieval performance. Stage 9 authorization is required before protected content is
evaluated.

## Controlled local runner

`python -m backend.app.evaluation.cli` is a maintenance/evaluation command, not an
HTTP endpoint. `--fixture` remains a content-free metric-plumbing path only; it is
not a quality baseline. `--synthetic-corpus` runs actual deterministic lexical ranking
over the separate synthetic corpus and does not create a database connection or provider
request. `SyntheticCorpusRetrievalTarget` returns ranked server-owned chunk IDs for
retrieval metrics. `RagGenerationEvaluationTarget` can adapt an injected `RagService`
only for generation/citation/claim observations; it deliberately returns no retrieval
IDs because final citations are not retrieval candidates. No non-synthetic adapter is
exposed by the CLI without separate data-use review.

The explicitly opt-in `--synthetic-ollama-corpus` CLI target is the only provider
path. It requires `--allow-local-ollama`, `EKAA_LLM_ENABLED=true`, a configured model,
and exactly the loopback Ollama OpenAI-compatible base URL
`http://127.0.0.1:11434/v1`. It maps only the versioned `synthetic_only` corpus to the
existing `RagService` retrieval seam, prompt builder, bounded context builder, and
citation parser/validator. It does not instantiate the application, open a database,
or expose an HTTP route. It captures retrieval IDs from that synthetic retrieval seam,
never from model-proposed citations. The provider's returned model must exactly match
the configured model whenever a generated answer reaches the RAG result; a deterministic
evidence-policy refusal retains its configured provider provenance. The artifact contains
the target/provider identity, configured model, and SHA-256 of the non-secret generation
configuration, never the base prompt, question, chunks, answer, raw provider response,
API key, or endpoint details.

For this target, the CLI also accepts only the repository's exact
`docs/evaluation/synthetic_corpus_v1.json` baseline path. Evaluating another corpus,
including a file labelled synthetic, requires a separately reviewed Stage 8 change;
there is no CLI switch for arbitrary or private content. Path checking resolves links,
and provider construction is separately blocked unless the loaded corpus SHA-256 equals
the code-pinned value and the reviewed policy's `synthetic_corpus_sha256`; an altered
file at the expected path therefore fails before any provider request.

The historical local generation targets are versions `1.2.0`, `1.3.0`, `1.3.1`, and
`1.3.2`; they remain eligible only so their synthetic source artifacts can be read and
scored. A normal full local replay identifies as target version `1.4.0`; historic
pre-baseline replays are descriptive only, and a fresh replay is required after any
policy-identity change before that identity can serve as a baseline. Like `1.3.0`, it enables the default-off, evaluation-only
`selected_ids_json_v1` direct-evidence prompt profile: it appends a static instruction
plus a JSON selected-ID list derived in order only from
`ContextWindow.selected_chunks` to the user message. It does not place dynamic IDs in
the system message, and no non-evaluation or non-local target opts into it.

The private evaluation-only provider adapter accepts only the canonical endpoint
`http://127.0.0.1:11434/v1`, replaces any configured API key with the fixed non-secret
`ollama-local` marker before creating an HTTP client, and sets `trust_env=False` so
proxy environment variables cannot change the local-only egress boundary. It preserves
the generic OpenAI-compatible request body and appends exactly
`reasoning_effort: "none"`, `response_format: {"type": "json_object"}`, and
`max_tokens: 256`. Its HTTPX pool/connect/read/write phase timeout is 20 seconds and
its `RagService` retry count is zero; these values do not alter the configured provider
defaults used outside this target. Version `1.3.1` used a 12-second phase timeout but
the G050 smoke still failed at 13,026.6222 ms; a separate no-artifact local diagnostic
also timed out at 13.0739 seconds. Version `1.3.2` is the reviewed, bounded 20-second
candidate. For a full `1.4.0` replay only, after every dataset/corpus/policy/path input
validation and before `EvaluationRunner`, EKAA makes exactly one static generic
content-free completion through the same private local provider instance as generation.
It requires both `finish_reason: "stop"` and the configured model identity, then
immediately discards the answer and usage. The only readiness output is the six-field
content-free record `event,outcome,duration_ms,model,profile,correlation`; it never
contains an exception message, traceback, provider payload, response, usage, endpoint,
prompt, corpus, question, or evidence. Any ordinary completion or validation error
becomes a failed record and safely aborts before runner execution, JSON/CSV source
artifacts, or reviewer packets; no readiness retry occurs. The readiness call is
environmental preparation only: it is excluded from all source records and quality,
latency, token, and failure metrics, and cannot guarantee later provider availability.
Single-question smoke, fixture, deterministic retrieval, and offline-scoring paths make
zero readiness calls and retain their historical `1.3.2` smoke identity. The adapter
accepts only a `stop`
completion reason:
missing, `length`, and other completion reasons are content-free output failures even
when their JSON is syntactically valid. Numeric provider usage is retained for output
failures and deterministic synthetic retrieval IDs are retained in the failure row;
model text, claims, citations, and endpoint/key values are never written. The canonical
endpoint identity, marker-key mode, proxy isolation, finish policy, prompt-profile
identifier, and SHA-256 of the effective static direct-evidence instruction are included
in the non-secret configuration fingerprint; the instruction text itself is not stored
in artifacts. Full `1.4.0` provenance additionally fingerprints the readiness profile,
static-prompt SHA-256, full-dataset-only scope, same-provider-instance policy, exact
stop/model validation, answer/usage discard policy, exactly-one/zero-retry policy, and
the six-field record schema. Artifacts created under different controls are not
comparable. In particular, `1.2.0`, `1.3.0`, `1.3.1`, `1.3.2`, and `1.4.0` source
identities have distinct
target/configuration provenance, so any results are descriptive experimental comparisons
only, not a policy regression comparison. The HTTPX phase timeout is not a hard
per-question or full-replay wall-clock deadline: serial total duration also depends on
transport/event-loop shutdown behavior, so no latency SLO is claimed.

```powershell
python -m backend.app.evaluation.cli `
  --dataset docs/evaluation/golden_dataset_v1.json `
  --synthetic-corpus docs/evaluation/synthetic_corpus_v1.json `
  --output-json C:\safe-local\stage8_result.json `
  --output-csv C:\safe-local\stage8_records.csv `
  --top-k 5 `
  --run-id reviewed-local-run-id
```

After a user intentionally starts Ollama locally, the generation replay command is:

```powershell
python -m backend.app.evaluation.cli `
  --dataset docs/evaluation/golden_dataset_v1.json `
  --synthetic-ollama-corpus docs/evaluation/synthetic_corpus_v1.json `
  --allow-local-ollama `
  --output-json C:\safe-local\stage8_ollama_qwen3_4b.json `
  --output-csv C:\safe-local\stage8_ollama_qwen3_4b.csv `
  --top-k 5 `
  --run-id ollama-qwen3-4b-YYYYMMDD
```

`--top-k` must equal `EKAA_RAG_RETRIEVAL_TOP_K`, so the recorded retrieval metric and
the RAG context use the same K. This is an opt-in measured run, not an automatic
evaluation action; do not run it with private or enterprise corpus files.

Before that full replay, an operator may run one reviewed question as a controlled
local smoke test by adding `--question-id <reviewed-question-id>` to the same
Ollama-only command. This selector is invalid for fixture or retrieval-only targets,
requires both Ollama opt-in flags, and forbids assessment input. The command first
loads and validates the entire reviewed dataset plus the complete pinned corpus before
selecting the requested known question and constructing the local provider target.
Its artifact records `evaluation_scope: "single_question_smoke"`; a normal command
without the selector records `"full_dataset"`. Smoke artifacts are explicitly
ineligible for regression comparison and do not establish a quality baseline.
The normal full command performs the readiness prerequisite described above, including
when `--review-packet` is selected; a failed readiness prerequisite writes no source,
CSV, or reviewer packet.

### Local Ollama readiness preflight

`--local-ollama-preflight` is a separate, explicit local diagnostic for the same
full-dataset readiness prerequisite. It requires `--dataset`, the exact approved
`--synthetic-ollama-corpus`, `--allow-local-ollama`, and a bounded `--run-id`; it
loads and validates the dataset, policy, corpus coverage, and corpus approval before
constructing its readiness-only provider boundary. This factory validates only the
configured model, canonical endpoint, bounded run ID, and configured `top-k`; it has no
corpus/evidence parameters and constructs no retrieval, RAG service, context, citation,
provenance, or review object. It invokes the static readiness helper exactly once and
emits only its six-field content-free readiness record. The full generation target uses
that same helper while retaining its existing same-provider-instance and once-only
state.

The mode forbids `--output-json`, `--output-csv`, assessments, a fixture or retrieval
target, `--question-id`, and reviewer packet arguments. It does not construct
`EvaluationRunner`, retrieve evidence, generate a RAG answer, capture reviewer material,
or write a JSON/CSV/packet artifact. A failed record exits with the fixed safe error
`local readiness check failed`; no traceback or provider detail is emitted. A ready
preflight is environmental preparation only: it does not create replay evidence,
activate a baseline, skip the readiness call of a later full replay, or guarantee
subsequent provider availability.

```powershell
python -m backend.app.evaluation.cli `
  --dataset docs/evaluation/golden_dataset_v1.json `
  --policy docs/evaluation/baseline_policy_v1.json `
  --local-ollama-preflight `
  --synthetic-ollama-corpus docs/evaluation/synthetic_corpus_v1.json `
  --allow-local-ollama `
  --top-k 5 `
  --run-id baseline-policy-preflight-YYYYMMDD
```

JSON/CSV artifacts contain only dataset/version/fingerprint, aggregate metrics,
question IDs, counts, timing, token totals, and coarse failure kinds. They exclude
questions, answers, document names/content, snippets, raw chunks, provider payloads,
secrets, and protected data. Operators choose a safe local artifact directory.

## Metric definitions

For a question `q`, expected evidence `E(q)`, and first unique returned IDs `R_k(q)`:

```text
Recall@K(q)    = |E(q) ∩ R_k(q)| / |E(q)|
HitRate@K(q)   = 1 when E(q) ∩ R_k(q) is non-empty, else 0
Precision@K(q) = |E(q) ∩ R_k(q)| / K
RR(q)          = 1 / rank of the first expected ID in R_k(q), else 0
MRR            = mean RR(q)
```

All retrieval values are macro means over every reviewed question. Missing results
are zero-relevance rows rather than silently removed; duplicate IDs count only at
first rank; empty results are valid zero rows. Latency reports nearest-rank p50/p95
from finite non-negative local measurements. Token totals are normalized values from
the target. Failure rate is coarse failure rows divided by all dataset questions.

Unsupported-claim rate is deterministic only when a target supplies both claim
counts: `unsupported claims / emitted claims`. It is `null` when no claims exist.
Correctness, faithfulness, and citation accuracy are `null` without calibrated human
assessments. No LLM judge is used. Estimated cost is `null` with a reason unless every
observed model has a versioned price schedule; no provider price/cost is committed.

## Human review, baseline, and gates

Human review is a strict v2, content-free assessment envelope containing a bounded
calibration-set ID, a source identity, and one row per reviewed question. The source
identity records the raw-byte SHA-256 and run ID of the exact unscored source artifact,
plus dataset/corpus/K/target/model/configuration/policy identity. Each row includes a
bounded reviewer ID and optional boolean correctness, faithfulness, and
citation-accuracy judgements. A score is available only when the bundle covers every
reviewed question exactly once and every covered row has a non-null judgement for that
metric; otherwise that metric is `null`, never a partial-subset rate.

Live replays never load assessments and always emit null human metrics plus null
assessment provenance. Offline scoring requires exactly `--assessments` and
`--assessment-source-artifact`; it loads no settings, provider, retrieval service, or
database. Before output, it reads the source bytes once, fingerprints those exact bytes,
requires the strict v2 full-dataset synthetic-generation schema, validates complete
record coverage and recomputes retrieval, unsupported-claim, latency, token, and
failure aggregates. It then requires every declared source field, including both the
source run ID and raw-byte SHA-256, to match exactly. The scored output records both
the assessment artifact SHA-256 and source artifact SHA-256. Hashes bind recorded
evidence but do not claim cryptographic proof that a reviewer viewed intentionally
unretained answer content. Only an unscored eligible generation replay is written as
`stage8_generation_source`; scored results are explicitly written as
`stage8_evaluation_result` and cannot become a later scoring source.
Before promotion, reviewers need a rubric, shared
calibration subset, disagreement discussion, and review provenance. Provider claims
and citation IDs remain untrusted; Stage 5 server validation remains authoritative.

### Local synthetic reviewer packet

An operator who explicitly selects a local synthetic review session may add
`--review-packet <outside-repository .md path>` and `--reviewer-id <bounded
pseudonymous ID>` to a normal full local Ollama replay. It is invalid for smoke,
fixture, deterministic retrieval, offline scoring, arbitrary corpus, or non-loopback
provider paths. The packet is written only after its content-free unscored source
artifact succeeds and records that artifact's raw-byte SHA-256 and run identity. It
contains synthetic question, displayed RAG answer, retrieved synthetic evidence, and
server-validated claim/citation material from that same replay so a human can review it.

The packet is not an evaluation artifact, a scoring input, an API response, or a Git
file. Its resolved path must be outside the repository; it is intentionally retained
only under the local operator's control and must be reconsidered before non-synthetic
data is ever allowed. The source artifact remains content-free and is the only file to
which an assessment bundle can bind. A single reviewer may produce provisional metrics;
two matching reviewers provide agreement only for the reviewed packet, not proof of
reviewer independence or population-level calibration. A new source requires new
source-bound assessments.

`docs/evaluation/baseline_policy_v1.json` is loaded strictly and fingerprinted; it is
a versioned policy, not an invented measurement. Gates permit at most 0.02 absolute decline in retrieval/calibrated-human
metrics, 0.01 failure-rate increase, 1.25x p95 latency, and 1.10x total tokens on an
identical dataset fingerprint and K. Missing measures are `not_evaluated`, never pass.
Every summary records dataset/corpus hashes, K, target/version/model identity,
configuration hash, run ID, calibration ID, and both policy version and SHA-256; scored
summaries also retain the paired assessment/source artifact hashes.
Regression comparison rejects dataset/corpus/K/policy-version/policy-SHA/target/model/
configuration/calibration mismatches and any non-`full_dataset` scope; run ID remains
recorded as identity rather than
a false requirement that two runs be the same event. These are local regression gates,
not production SLOs. The first immutable measured
baseline must record command, dataset hash, target/model versions, calibration evidence,
environment notes, safe artifact location, and caveats.

## Non-goals

No LLM-as-judge without human calibration, fine-tuning, retrieval/reranker change,
authentication/authorization, protected data, tools, agents, memory, frontend,
production provider call, or production performance claim is added.
