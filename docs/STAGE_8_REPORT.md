# Stage 8 Report

Goal: Establish reproducible retrieval and generation quality gates without
inventing results or sending data to external systems.

Current branch: main.

Repository status: Stage 8 implementation is uncommitted. No push, database
operation, Docker operation, protected-data operation, or production action was
performed. Explicit local Ollama calls used only the approved synthetic corpus.

Files created: Evaluation contracts, strict dataset/assessment/policy/corpus loaders,
deterministic metrics, actual synthetic-corpus retrieval target, local CLI, regression
comparator, 50-question golden dataset, 48-chunk traceable synthetic corpus, the
versioned active baseline policy, content-minimized replay artifacts, focused tests,
this design, and this report.

Files modified: Active-stage markers, README, and local-evaluation configuration
documentation only.

Implementation: The golden dataset and separate corpus are both versioned and strict;
coverage validation refuses a replay if an expected chunk ID does not exist. The local
synthetic target executes actual deterministic lexical ranking over server-owned corpus
chunks. Each reviewed corpus chunk contains a factual synthetic passage that answers
its mapped question rather than a copy of the question. Static fixture input remains
metric-plumbing only and requires exactly one row
for every dataset question. Retrieval IDs are separate from generation/citation fields;
final citations never score Recall/MRR. Live replays keep all human metrics unavailable.
The v2 offline assessment envelope binds a complete reviewer bundle to both the exact
raw-byte source artifact SHA-256 and source run ID, plus dataset/corpus/K/target/model/
configuration/policy identity. The strict reader accepts only unscored full synthetic
local-generation artifacts, validates complete record coverage, and recomputes safe
aggregates before applying human rates. Scored provenance retains paired assessment and
source hashes; incomplete, stale, legacy, smoke, fixture, retrieval-only, or forged
inputs fail before output. Regression comparison rejects internally inconsistent human
metric/provenance states while allowing separately reviewed comparable runs to retain
different source and assessment hashes.
Fresh source production validates bounded, content-free run and model identities before
provider/target construction, so a normal local replay cannot label an artifact as
scoreable if its own reader would later reject that provenance. Every live and offline
input path is checked against both output paths before execution; equal paths, resolved
symbolic-link aliases, and existing hard-link aliases fail before any source/provider/
target/output work, preventing evidence inputs from being overwritten.
The policy SHA-256 is recorded and must match the currently loaded policy, preventing
same-version policy files with different thresholds from comparing silently.
The opt-in local-generation target accepts only the loopback Ollama-compatible endpoint
and the synthetic corpus. It constructs the existing `RagService` around an in-memory
synthetic retrieval adapter, retaining bounded context and server-side citation
validation while avoiding a database or HTTP endpoint. It records a configured model,
provider-target identity, and non-secret configuration SHA-256; provider responses,
questions, chunks, answers, endpoint details, and keys stay out of artifacts.
The local generation path requires both the resolved approved corpus path and a
byte-for-byte SHA-256 match against a code-pinned reviewed hash and the policy hash;
altered content fails before target construction or provider use.
After the local replay timeout investigation, the private synthetic-only local target
was revised to version `1.2.0`: it accepts only the canonical
`http://127.0.0.1:11434/v1` endpoint, replaces configured credentials with the fixed
non-secret `ollama-local` marker, and sets `trust_env=False` for its own HTTP client so
environment proxy settings cannot alter this egress boundary. It adds fixed
structured-output controls (`reasoning_effort: "none"`, JSON-object mode, and
`max_tokens: 256`), uses a 10-second HTTPX pool/connect/read/write phase timeout and
zero RAG retries, and fingerprints the endpoint identity, marker/proxy/finish policies,
and all effective controls. The generic provider's default request body, headers, and
environment behavior remain unchanged and protected from extension replacement. Only a
`stop` completion reason is accepted; missing, capped, or other reasons are safe output
failures even if their JSON parses. Numeric usage and deterministic retrieval IDs are
retained for output-failure records without retaining response content. Mock tests cover
exact generic/local request bodies, credential replacement, proxy isolation, canonical
endpoint rejection, valid source-backed output, valid JSON with `length`, malformed or
cap-truncated output, timeout and retryable-error failure rows, profile fingerprint
changes, and existing SHA, no-evidence, fake-injection, and artifact-exclusion guards.
The 10-second value configures HTTPX pool/connect/read/write phase timeouts, not a
strict per-question, request, or full-replay wall-clock bound; no such SLO is claimed.

The controlled CLI also supports a one-question local smoke selector only for the
explicit synthetic Ollama target and its opt-in flag. It validates the full reviewed
dataset and pinned corpus before selecting a known question, disallows assessments,
and records the content-free `single_question_smoke` scope. This scope is rejected by
regression comparison; commands without the selector retain the `full_dataset` scope
and the existing 50-question behavior.

Architecture decisions: Evaluation is a separate application package and controlled
local command, not a public route or RAG change. Artifact records retain no question,
answer, source ID, document metadata, excerpt, or content. Stage 5 validation stays
the authority for citation metadata. Synthetic retrieval is an evaluation target, not
a substitute for production RAG generation or provider quality.

Tests: Dataset/corpus coverage, source/review status and 50-question target; actual
ranked synthetic retrieval; Recall, Hit Rate, Precision@K, MRR, duplicate/missing/
empty results; deterministic replay; unavailable cost/human measures; mandatory human
calibration; policy/provenance mismatch; duplicate/outside fixture IDs; regression
thresholds; artifact exclusion; explicit Ollama CLI opt-in; local-only endpoint
rejection; code/policy/loaded-corpus SHA agreement; altered corpus rejection at the
approved path; and RAG generation flow through a fake provider returning a source-backed
factual claim.

Test results: PASS locally after the final assessment-provenance safeguards, local
review-packet implementation, direct-evidence experiment, readiness remediation, and
active-baseline preflight implementation — `267 passed`,
`7 skipped`. Four skipped tests require an explicitly configured disposable
PostgreSQL/pgvector database; three symbolic-link collision cases require a Windows
symbolic-link privilege unavailable to this operator. Equal-path and real hard-link
collision cases passed, and the shared symbolic-link branch was independently reviewed.
Ruff, formatting, strict mypy, and `git diff --check` passed. These checks use mocks/
fakes for local generation and do not include a new real Ollama call. The
deterministic synthetic
corpus replay was regenerated after the factual-passage correction. It is a local
retrieval command only, not an Ollama generation call. A real local
synthetic corpus replay was run with the documented CLI and emitted
`docs/evaluation/artifacts/synthetic_retrieval_baseline_v1.{json,csv}`. It recorded
50/50 retrieval hits, Recall@5=1.0, Hit Rate@5=1.0, MRR=1.0, Precision@5=0.2, zero
failures, and zero tokens on this deliberately self-contained lexical fixture. Its
local timing is recorded in the artifact, not claimed as an SLO.

Measured local generation replay: PASS as an operational run, NOT_PROMOTED as a
quality baseline. A one-question `single_question_smoke` and a full 50-question
`full_dataset` replay were deliberately run against local `qwen3:4b` through the
canonical loopback target version `1.2.0`. The full replay produced 50 content-free
records with the approved corpus/policy hashes, Recall@5=1.0, Hit Rate@5=1.0,
MRR=1.0, Precision@5=0.2, total tokens=25,829 (23,805 input; 2,024 output),
p50 latency=2,139.3324 ms, and p95 latency=2,757.0695 ms. Its coarse failure rate
was 0.06 (one `RagUnavailableError`, two `RagOutputError` rows). The deterministic
validator marked 40 of 47 emitted claims unsupported, an unsupported-claim rate of
0.8510638298. These are measured model-quality outcomes, not evidence of a citation
validator defect; the validator was intentionally not weakened. Artifacts were written
only to the operator's ignored safe-local directory and were inspected to confirm they
contain no question, answer, corpus, provider payload, endpoint, or credential text.

Measured local generation comparison: PASS as an operational run, NOT_PROMOTED as a
quality baseline. On 2026-08-25, local `qwen2.5:3b` was first warmed locally and then
run through the same version `1.2.0` target, full 50-question dataset, approved corpus
and policy hashes, and `top_k=5` as the `qwen3:4b` baseline. It again recorded
Recall@5=1.0, Hit Rate@5=1.0, MRR=1.0, and Precision@5=0.2; retrieval is deterministic
and therefore identical. Its total token count was 26,108 (24,603 input; 1,505 output),
with p50 latency=849.1978 ms and p95 latency=1,002.2183 ms, faster than the `qwen3:4b`
run. Its coarse failure rate remained 0.06, now three `RagOutputError` rows and no
`RagUnavailableError` row. However, the deterministic validator marked all 47 of 47
emitted claims unsupported (unsupported-claim rate=1.0), worse than `qwen3:4b`'s
0.8510638298. The speed gain does not compensate for this quality regression, so
`qwen2.5:3b` is not selected as a Stage 8 quality baseline. Its content-free artifacts
were written only to the ignored safe-local directory and inspected under the same
artifact-exclusion rules.

Measured local generation comparison: PASS as an operational run, NOT_PROMOTED as a
quality baseline. On 2026-08-25, the originally selected local `qwen3:8b` model was
warmed locally and then evaluated through that same 50-question target, dataset and
corpus hashes, policy, and `top_k=5`. The device has a 4 GB RTX 3050 Laptop GPU, so
Ollama reported a mixed 39% GPU / 61% CPU allocation for this model; the full run still
completed without any coarse failure. It recorded Recall@5=1.0, Hit Rate@5=1.0,
MRR=1.0, Precision@5=0.2, total tokens=26,671 (25,003 input; 1,668 output), p50
latency=5,113.1026 ms, and p95 latency=6,529.1956 ms. All 50 outputs completed without
a `RagUnavailableError` or `RagOutputError`. The deterministic validator nonetheless
marked 39 of 50 emitted claims unsupported (unsupported-claim rate=0.78). This is the
best measured operational and groundedness result of the three local candidates, so
`qwen3:8b` is retained as the local development model. It is still not a promotable
quality baseline: the unsupported-claim rate remains far too high and calibrated human
correctness, faithfulness, and citation-accuracy measures are unavailable. Its
content-free artifacts were written only to the ignored safe-local directory and passed
the same artifact-exclusion inspection.

Fresh v2 source replay: PASS as an operational source artifact, NOT_PROMOTED as a
quality baseline. On 2026-08-26, after the assessment-provenance safeguards were
implemented, local `qwen3:8b` completed a fresh warmed full 50-question replay through
the same approved synthetic target. Its content-free artifact uses schema version 2 and
kind `stage8_generation_source`, has the exact recorded raw-byte SHA-256
`2fe973296873c96e849ed65e05417fd15e73e381ca61b658c8a16b9b85af1883`, and has null
human/assessment provenance as required for an unscored source. It recorded zero coarse
failures, Recall@5=1.0, Hit Rate@5=1.0, MRR=1.0, Precision@5=0.2, total tokens=26,693
(25,003 input; 1,690 output), p50 latency=5,049.1059 ms, p95 latency=5,948.0252 ms,
and unsupported-claim rate=0.74. The artifact is eligible for the new offline human
assessment flow, but no human score was invented or applied; correctness, faithfulness,
and citation accuracy therefore remain unavailable. It was stored only in the ignored
safe-local directory and was checked to contain no question, answer, corpus, provider,
endpoint, credential, or other content-bearing field.

Local solo-review material: PASS as a controlled synthetic-only review aid. At the
user's explicit request, a fresh full local `qwen3:8b` replay generated a 50-question
Markdown reviewer packet for pseudonymous `reviewer_a` outside the repository in the
operator's safe-local directory. It is bound in its header to source artifact SHA-256
`08db264991d9dafd6c61a285d948bd41cbcb057c98d78052cb09b4f58982d029`; the official source
artifact remains content-free. The packet contains synthetic-only question/answer/
evidence/citation review material from that exact replay, is not Git-tracked or a
scoring input, and is retained under the operator's control for later review. A single
reviewer's eventual scores are provisional evidence only, not a claim of inter-reviewer
agreement or model promotion.

Direct-evidence prompt experiment: PASS as a controlled Stage 8 experiment,
NOT_PROMOTED for normal application use. Investigation of the fresh `qwen3:8b` source
showed that 38 prior final safe refusals had reached citation validation: 37 had model
proposed a citation ID outside the selected context and one did not lexically support
its claim. Retrieval, transport, empty claims, and JSON parsing were not the primary
recorded cause. The server correctly failed closed; validation was not weakened. A
default-off, evaluation-only `selected_ids_json_v1` prompt profile was therefore added
to the local synthetic target only. It serializes exactly the ordered
`ContextWindow.selected_chunks` IDs as JSON data in the user message and instructs the
model to copy only those values. Normal application prompt construction remains
unchanged. The direct-evidence experiment first ran as target version `1.3.0`; historical
`1.2.0`, `1.3.0`, and `1.3.1` remain eligible so their synthetic source artifacts can
be read and scored. The reviewed 20-second candidate `1.3.2` is also eligible for a
future content-free source artifact, but no `1.3.2` result exists yet. The separate
`1.4.0` source identity additionally fingerprints its full-run-only readiness protocol,
so it remains deliberately non-comparable through the strict regression gate. The
content-free fingerprint records both profile ID and a SHA-256 of the static instruction.

G050 provider-unavailable investigation: RESOLVED for the investigated local
cold-readiness condition. The one `RagUnavailableError` in the `1.3.0` direct-evidence replay
occurred after 10,230.2789 ms. Controlled same-profile and direct-provider reproductions
then produced `httpx.ReadTimeout` beyond the fixed 10-second read phase. EKAA correctly
maps that timeout through `ProviderTimeoutError` to `RagUnavailableError`; with the
evaluation profile's zero retries, no second attempt occurs. Retrieval had completed,
so this is not a retrieval, context, or citation-validation defect. The evaluation-only
The `1.3.1` 12-second smoke still failed at 13,026.6222 ms with the same safe failure
shape; a separate no-artifact local diagnostic also timed out at 13.0739 seconds. The
evaluation-only target retained the reviewed `1.3.2` 20-second candidate, with its HTTPX
pool/connect/read/write phase timeout raised only from 12 to 20 seconds. All other
local-only controls, including zero retries and the content-free timeout fingerprint,
are preserved. A controlled no-artifact sequence then showed an initially resident model,
a successful generic local completion, and an immediate exact G050 completion; this
supports cold local initialization/readiness as the trigger without proving subsequent
availability. The full-only `1.4.0` candidate now performs that exact one-call generic
readiness prerequisite through the same provider instance, fails closed with a six-field
content-free record, and excludes warm-up data from all source artifacts and metrics.
On 2026-08-29, with Ollama intentionally started, the new `1.4.0` full replay
`readiness-qwen3-8b-20260829` first reported `ready` in 9,807.255 ms, then completed
all 50 questions with Recall@5=1.0, zero coarse failures, and no `RagUnavailableError`.
Its content-free source SHA-256 is
`777e5f054d879d3e0ae7128d41bee461e8da92b6e7dfc80a953201a6cec3e2b7`; its configuration
SHA-256 is `610d5101efecece5e669e1702e7ea0e6a9789cb0ef6dd0be1d5554fac0f92230`.
The local reviewer packet has 50 questions and binds that exact source SHA; readiness
data is absent from the source JSON/CSV, packet, and all measured metrics. This confirms
the bounded incident remediation, not a wall-clock or future-availability guarantee, and
does not change normal runtime behavior. Its fresh human assessment is recorded below.

Fresh readiness-gated assessment: PASS as a source-bound offline scoring workflow,
NOT_PROMOTED as a quality baseline. `reviewer_a` independently reviewed all 50 questions
in the new local-only packet without reusing a prior assessment. The strict v2 assessment
SHA-256 `e1d42b23a6fb613efbc1f848223cc5d970037d2cc6e79e6a8a2a8a162dd075f3` binds exactly
to source SHA-256 `777e5f054d879d3e0ae7128d41bee461e8da92b6e7dfc80a953201a6cec3e2b7`
and run ID `readiness-qwen3-8b-20260829`; the resulting scored artifact SHA-256 is
`3f2034a1ff874587187de86a2f5330f7c7dfa23cca71f146cfcbad5e8099938a`. Offline scoring
validated all 50 rows and measured correctness=0.88 (44/50), faithfulness=0.88
(44/50), and citation accuracy=0.88 (44/50). The six false verdicts were G015, G019,
G025, G033, G041, and G042: in each case the model returned an insufficient-evidence
answer despite directly retrieved synthetic evidence. The same source has Recall@5=1.0,
zero coarse failures, unsupported-claim rate=0.1090909091, 32,661 total tokens,
p50 latency=4,629.3553 ms, and p95 latency=8,094.1920 ms. Readiness remains excluded
from these measurements. This is a descriptive, one-reviewer result under a distinct
v1.4.0 target/configuration; it is neither a strict regression comparison nor a general
reliability or promotion claim.

Second independent review: PASS as an agreement check, NOT_PROMOTED as a production
quality claim. `reviewer_b` independently supplied a complete second 50-question
assessment against the same retained packet and exact source. Its assessment SHA-256 is
`933ee88404fb3727bbcf55a5998d5ede7effca95f56079ad649af7fa3c1b37c9`; its separately
scored artifact SHA-256 is
`9a1087d8115254c62bc31d473ff7c550c4e087f6f661a9452c5e4cdc247b320d`. It binds to the
same source SHA, run, dataset/corpus, K, target v1.4.0, model, configuration, and policy
identity. It independently produced 0.88 (44/50) for correctness, faithfulness, and
citation accuracy, with the same six false verdicts. Per-metric inter-reviewer agreement
is 50/50 (100%) for all three judgements. This records agreement on this constrained
synthetic packet; it does not establish population-level calibration, a strict regression
comparison, normal-runtime adoption, or a production-quality guarantee.

Active-baseline readiness preflight implementation: COMPLETE and independently
verified. The controlled CLI now has an explicit
`--local-ollama-preflight` mode for investigating a failed policy-bound readiness
attempt without producing replacement evidence. It requires the full reviewed dataset,
active policy, exact approved synthetic Ollama corpus, explicit opt-in, and a bounded
run ID; it validates those inputs before target/provider construction, invokes the
isolated readiness-only provider boundary exactly once, and emits only the six approved
content-free fields. That boundary validates only model/endpoint/run ID/top-K and
constructs no corpus/evidence/retrieval/RAG/context/citation/provenance/review object;
the full target delegates to the same static helper while retaining its existing
same-provider and once-only gate. It forbids output paths, assessment, smoke, reviewer
packet, and other target modes; it cannot construct the runner, retrieval, reviewer
 material, or artifacts. Focused mock tests cover argument rejection,
validation/provider ordering, constructor isolation, success, and safe failure without
runner or artifact writes. The real preflight was then run once and failed safely at
the readiness boundary after 14,447.998 ms; the earlier active-policy full replay had
failed at the same boundary after 17,854.488 ms. A separate content-free Ollama warm-up
also terminated during local GPU/CUDA backend initialization. No replacement source,
CSV, packet, assessment, or baseline artifact was created, and no quality measurement
or baseline activation is claimed.

Historical active-policy readiness incident: BLOCKED BY EXTERNAL LOCAL RUNTIME. Policy
`1.1.0` is active and its exact SHA-256 is
`691a2d0d66105f738d82c801b74dc334a1d6f1752fb21cc74ac755f52472f345`, but a fresh
policy-bound replay cannot produce the required immutable source artifact until the
local Ollama/GPU environment completes a stable static request. Root-cause recheck
classifies this as `BASELINE_ENVIRONMENT_BLOCKED`; the precise provider subtype is
intentionally not exposed. EKAA correctly failed closed before retrieval, generation,
scoring, reviewer material, or artifact writes. No timeout, retry, retrieval,
citation, normal-runtime, or policy change is justified by this evidence.
Read-only post-checks show Ollama `0.33.2`, `qwen3:8b` installed but not resident,
and the local RTX 3050 (driver `581.29`) visible and idle. This supports the
environment classification without identifying the precise driver/runtime subtype.

Active-policy baseline replay and source-bound scoring: PASS as a controlled workflow,
NOT_PROMOTED as a quality baseline. After the operator restored a stable CPU-only local
Ollama runtime, the approved preflight completed in 3,218.685 ms and one full 50-question
replay completed under policy `1.1.0`, target `1.4.0`, model `qwen3:8b`, and `top_k=5`.
The strict source artifact is outside the repository at
`C:\safe-local\stage8_baseline_policy_1_1_qwen3_8b_source_20260901_cpu.json` with
raw SHA-256
`493275a91233b04d4d3e4e6bf4cda60b32848993f024da28130a128f83dfc413`; the companion CSV
and reviewer packet are retained in the same safe-local directory. The source passed
strict loading with all 50 records and the exact active-policy SHA. Deterministic
retrieval was Recall@5=1.0, Hit Rate@5=1.0, MRR=1.0, and Precision@5=0.2. The run had
15 `RagUnavailableError` rows (failure rate 0.30), 20,846 total tokens (19,610 input;
1,236 output), p50 latency=17,775.1549 ms, p95 latency=20,245.5552 ms, and an
unsupported-claim rate of 0.1315789474. Two complete, independent source-bound
assessments were then scored. Reviewer-1 scored correctness/faithfulness/citation
accuracy at `0.60/0.60/0.60` (30/50 each); its assessment SHA-256 is
`f581b6a97b04cabc43538f3f9cef9e42c3fedea32d253983ccecf8551044ce87` and scored result
SHA-256 is `aae73324f6c31fc57599ae15d3edaf560df6ce17619e21d65b5284f71fd326bc`.
Reviewer-2 scored `0.58/0.60/0.60` (29/50, 30/50, 30/50); its assessment SHA-256 is
`68e71c1672ca302d442c91b1ae8b6fedc7856cbf9b43f914eaf25914a038228a` and scored result
SHA-256 is `18525dfb3ac8f1ccb28deb48a57a00341739595b136bf49867a43e2ebd178c5f`.
Per-metric agreement is 49/50 (98%) for correctness and 50/50 (100%) for both
faithfulness and citation accuracy. The 30% provider failure rate, low human scores,
and absence of a broader calibrated review process prevent promotion. This CPU-only
result is operational evidence for the active policy, not a GPU-versus-CPU comparison
or a production performance claim.

Fresh GPU/mixed active-policy replay and source-bound scoring: PASS as a controlled
workflow and PROMOTED as the Stage 8 local synthetic regression baseline. After the CUDA path recovered, the same policy
`1.1.0`, target `1.4.0`, model `qwen3:8b`, dataset, corpus, and `top_k=5` completed
50/50 questions with `61%/39% CPU/GPU` allocation. The source artifact is outside the
repository at
`C:\safe-local\stage8_baseline_policy_1_1_qwen3_8b_source_20260901_gpu.json` with raw
SHA-256
`17df123f16691205d0a526f644d47d8a1104ce26410b5bdc1b42f574a6c188b8`; its companion CSV
and fresh reviewer packet are in the same safe-local directory. Strict source loading
passed with the exact active-policy SHA. Retrieval remained Recall@5/Hit Rate@5/MRR
`1.0` and Precision@5 `0.2`; all 50 generation rows completed with zero coarse
failures, unsupported-claim rate `0.1153846154`, 32,573 total tokens, p50 latency
`4,742.8187 ms`, and p95 latency `5,974.2579 ms`. Two fresh complete source-bound
assessments both scored correctness/faithfulness/citation accuracy `0.88` (44/50),
with 50/50 agreement on every metric. Reviewer-1 assessment SHA-256 is
`fe3627e1e1d507fb7a30f4c9407913627d09a960ebd816d1f8959431067d50b3` and scored result
SHA-256 is `c3f76eedebcec905459564128e7502c469758f9f321449bb14744f3e05dda006`;
reviewer-2 assessment SHA-256 is
`eddd55874f4040d84a08ccd0c1b67f24fbcb0eccc43be2ed297e626e60cc43f1` and scored result
SHA-256 is `c19310ad7bb8a3d0567d4d79a66c25611078822acb1259a40ac7822af4a473c5`.
The joint calibration record `stage8-calibration-gpu-20260901` is retained outside the
repository with SHA-256
`c4745aed39a3ca4d122ffc95b7719485085ff69ffb590c1b9e25446e67ec3a07` and binds this
exact source run. The CPU-only and GPU/mixed runs remain separate; this promotion is
only for local synthetic regression and is not a production-quality or population claim.

Fresh direct-evidence measurement: PASS as an operational replay and provisional
single-reviewer assessment. On 2026-08-27, the new full local `qwen3:8b` replay used
the exact pinned 50-question dataset/corpus, `top_k=5`, reviewed policy, and canonical
loopback target. Its content-free source artifact is
`direct-evidence-qwen3-8b-20260827`, target version `1.3.0`, and raw SHA-256
`c72e6a81a99e5955fa043fd4418abde69d2b5d6dceb396d74071bcab5ecd83af`. A fresh retained
synthetic reviewer packet was independently reviewed by pseudonymous `reviewer_a`; no
prior verdict was reused. The complete strict v2 assessment (SHA-256
`c4bb0c1c6d375b258484c115f1014500411ab857c01f2d781ee30dc65d584e34`) bound to that
source and offline-scored correctness=0.86 (43/50), faithfulness=0.86 (43/50), and
citation accuracy=0.86 (43/50). Compared descriptively with the v1.2 solo result of
0.26, 0.26, and 0.24, the direct-evidence prompt hypothesis is supported on this
synthetic sample. Deterministic retrieval remains Recall@5=1.0, Hit Rate@5=1.0,
MRR=1.0, and Precision@5=0.2. Unsupported-claim rate improved from 0.76 to
0.1176470588; however failure rate rose from 0 to 0.02 (one `RagUnavailableError`),
total tokens rose from 26,702 to 31,975, p50 latency from 5,420.2179 ms to
5,563.5079 ms, and p95 latency from 7,159.7022 ms to 9,752.4626 ms. Estimated cost
remains unavailable without a versioned provider price schedule. The single reviewer,
small synthetic corpus, intentional target/configuration discontinuity, one provider
failure, and p95/token regression initially prevented a promotion claim or a policy-gate
pass. The later v1.4.0 two-reviewer result is recorded separately because its target and
configuration identity differs.

Provisional solo human assessment: PASS as an offline scoring workflow, NOT_PROMOTED
as a quality baseline. `reviewer_a` independently reviewed all 50 questions in the
retained local synthetic packet, using the recorded correctness, faithfulness, and
citation-accuracy rubric. The strict v2 assessment bundle is content-free and binds
the source run `reviewer-a-qwen3-8b-20260826` and raw source SHA-256
`08db264991d9dafd6c61a285d948bd41cbcb057c98d78052cb09b4f58982d029`. Offline scoring
validated the complete 50-row bundle before writing a scored result, with assessment
SHA-256 `e485e2f45a30fbbfa6ba6e0ddf850a19b2964cce2b62dc8b4a4c5f33a65b2d8f`. The measured
rates are correctness=0.26 (13/50), faithfulness=0.26 (13/50), and citation
accuracy=0.24 (12/50). The same source records deterministic retrieval Recall@5=1.0,
Hit Rate@5=1.0, MRR=1.0, Precision@5=0.2, zero coarse failures, unsupported-claim
rate=0.76, 26,702 total tokens (25,003 input; 1,699 output), p50 latency=5,420.2179 ms,
and p95 latency=7,159.7022 ms. Estimated cost remains unavailable because no versioned
provider price schedule was supplied. This is an honest one-reviewer measurement only:
it establishes no inter-reviewer agreement and does not promote the model or satisfy a
future regression gate requiring a calibrated review process.

Manual verification: PASS for the constrained one-question smoke and full local
synthetic replay. The smoke artifact is explicitly excluded from regression comparison.

Known limitations: The 50-question/48-chunk synthetic corpus is self-contained and
not population representative; its perfect retrieval result validates deterministic
fixture wiring over reviewed factual synthetic passages, not user quality or semantic
retrieval quality. A future generation promotion needs a separately approved
normal-runtime prompt decision and a provider schedule if cost is needed. The active
CPU-only packet documents the environmental cost of running without GPU; the newer
GPU/mixed packet has two source-bound reviews with 100% agreement and 0.88 human rates.
The shared calibration record binds those judgements to the exact source run. This is
still evidence on a constrained synthetic corpus, not a population-level claim.

Security review: Only synthetic/public data is versioned. Artifacts exclude questions
and document/provider content. There is no HTTP route, secret, protected-data access,
or database connection. A provider call is possible only through the explicit local
Ollama command and was deliberately limited to the approved synthetic corpus. Only a
coarse failure class and normalized usage are recorded. Assessment scoring is offline
only; strict source/assessment parsing, raw-byte binding, bounded provenance fields,
and pre-write equal/symlink/hard-link collision guards prevent unsafe evidence reuse or
overwrite without retaining answer or provider content.

Regression review: Stage 0–7 behavior is unchanged. Comparisons require equal dataset
SHA-256 and K; unavailable values are `not_evaluated`, not passed.

Acceptance criteria: PASS / PROMOTED FOR LOCAL SYNTHETIC REGRESSION ONLY — retrieval replay, traceable corpus,
formulas, strict policy/provenance, constrained local generation target, and a measured
synthetic generation replay are verified. The measured `qwen3:4b` run has a 0.06 coarse
failure rate and 0.8510638298 unsupported-claim rate; the comparable `qwen2.5:3b` run
has a 0.06 coarse failure rate and a worse 1.0 unsupported-claim rate. Neither is a
candidate quality baseline. The comparable `qwen3:8b` run has zero coarse failures and
the best initial unsupported-claim rate (0.78), but was not a candidate quality
baseline. The independently reviewed fresh `qwen3:8b` v1.2 source measured correctness
and faithfulness of 0.26, citation accuracy of 0.24, and unsupported-claim rate of
0.76. The controlled v1.3 evaluation-only prompt experiment improved provisional
single-reviewer human rates to 0.86 for all three measures and reduced unsupported
claims to 0.1176470588, but introduced one provider failure and worse p95/token
measurements. It is not a strict regression comparison or promotion decision because
its target/configuration identity differs and it has one reviewer only. The `1.3.1`
timeout remediation failed its G050 smoke, and the evaluation-only `1.3.2` 20-second
candidate has no replay result yet. The separate `1.4.0` readiness remediation completed
a clean fresh 50-question operational replay and two source-bound assessments at 0.88
for correctness, faithfulness, and citation accuracy, with 100% agreement. Its target/
configuration differs, so it remains descriptive only and is not a quality promotion or
general reliability guarantee. The active policy now has a valid GPU/mixed source,
two bound scored assessments at 0.88 with zero coarse failures, and a source-bound
shared calibration record. It is promoted only as a local synthetic regression
baseline; the CPU-only candidate remains retained as a separate diagnostic comparison.
Cost remains unavailable without a versioned provider price schedule, and the synthetic
corpus is not population representative. No unavailable or failed quality measure is
reported as passing.

Next stage: Stage 9 — Authentication & Authorization, only after explicit user
authorization.

No next-stage implementation performed.
