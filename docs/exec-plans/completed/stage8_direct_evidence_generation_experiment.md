# Stage 8 Direct-Evidence Generation Experiment

## Metadata

- Status: ACTIVE
- Workflow: Adaptive Verified Bug-Fix, AUTO classified as MEDIUM
- Date: 2026-08-27
- Scope: synthetic-only local Stage 8 generation experiment
- Coordinator: Codex

## Original issue

The completed 50-question single-reviewer assessment of local `qwen3:8b` measured
correctness 0.26, faithfulness 0.26, citation accuracy 0.24, and unsupported-claim
rate 0.76. Most failed reviewed questions had retrieved evidence that directly stated
the answer, while the returned result was an insufficient-information refusal.

## Intended experiment boundary

Identify and make the minimum evidence-led change to improve direct-evidence answer
generation. Re-run the same approved 50-question synthetic corpus with the same local
model, `top_k=5`, loopback-only provider boundary, reviewed policy, and human-review
process. Do not change retrieval, reranking, corpus, golden labels, citation
validation, API behavior, database behavior, authorization, or future-stage scope.

## Repository baseline

- Revision: `6473ff5`
- Existing worktree changes: Stage 8 implementation and documentation are uncommitted;
  preserve all changes outside the approved minimal experiment.
- Previous scored source: `reviewer-a-qwen3-8b-20260826`, source SHA-256
  `08db264991d9dafd6c61a285d948bd41cbcb057c98d78052cb09b4f58982d029`.

## Evidence ledger

| Evidence | Status | Notes |
| --- | --- | --- |
| 50-question solo assessment | CONFIRMED | 13/50 correct and faithful; 12/50 citation-accurate. |
| Retrieval measurements | CONFIRMED | Recall@5, Hit Rate@5, and MRR were each 1.0. |
| Failure examples | CONFIRMED | The reviewer identified direct evidence in the retained synthetic packet for the rejected answers. |
| Root cause | CONFIRMED | 38 final safe refusals followed citation validation; 37 were `citation_not_selected`, one was `citation_does_not_support_claim`. |
| Prompt efficacy | HYPOTHESIS | A controlled presentation change may improve exact selected-ID compliance; no result is assumed. |
| First plan review | PLAN_REJECTED | A new target version must be explicitly accepted by strict source-artifact eligibility before it can be offline-scored. |

## Hypothesis ledger

| Hypothesis | Status | Evidence needed |
| --- | --- | --- |
| The generic RAG prompt's insufficient-evidence instruction overwhelms direct-evidence extraction for this small local model. | HYPOTHESIS | Fresh controlled replay with a distinct evaluation-only prompt profile. |
| Retrieval/context selection is the primary cause. | DISPROVEN for recorded failures | Every failed row completed provider generation and reached citation validation; 37 invalid IDs explain the dominant path. |
| Citation validator is the primary cause. | DISPROVEN as primary fault | It correctly failed closed for the malformed/unselected provider proposals; it does explain the final safe refusal. |

## Gates

1. Investigator and root-cause review establish `ROOT_CAUSE_VERIFIED`.
2. Planner defines a minimal Stage 8-only experiment, including strict eligibility for
   its distinct target version and provenance; Coordinator records approval.
3. Implementer changes only approved files and adds targeted regression coverage.
4. Code review and architecture/business reviews report no BLOCKER or MAJOR finding.
5. Re-run tests, lint, type checks, and a fresh 50-question local replay.
6. Human assessment binds only to the fresh replay source; do not reuse prior scores.
7. Final verifier checks the original direct-evidence failure mode and records measured
   comparison, residual uncertainty, and promotion decision.

## Plan-review correction required

The original implementation plan was rejected because strict generation-source loading
accepts the previous local target version only. The revised plan must add a bounded
allow-list entry and tests for the exact new evaluation target version, without accepting
arbitrary versions. It must also place selected IDs as JSON data in the user prompt,
derive them only from `context.selected_chunks`, fingerprint a content-free prompt
profile identifier, preserve the normal default prompt unchanged, and state that the
prior and candidate runs are descriptive comparisons rather than a policy regression
gate because their target/configuration provenance differs.

## Investigation and planning gates

- Investigator: `READY_FOR_REVIEW`. It established that all 38 recorded refusals
  reached citation validation; 37 were unselected-ID rejections and one lacked lexical
  support. Empty claims, transport failure, JSON parsing, and retrieval absence were
  disproven as the primary recorded cause.
- Root-cause reviewer: `ROOT_CAUSE_VERIFIED`. The operational cause is citation
  validation rejection; prompt effectiveness remains a hypothesis to test.
- First plan review: `PLAN_REJECTED`. It identified that a new target version would be
  unscoreable without strict bounded eligibility support and that target/configuration
  mismatch must remain non-comparable in the regression gate.
- Revised plan: add exactly `1.3.0` alongside legacy `1.2.0` in the fail-closed source
  eligibility allow-list; use a content-free prompt-profile fingerprint; keep selected
  IDs JSON-encoded user-message data derived only from `context.selected_chunks`; do
  not change the regression comparator.
- Re-review: `PLAN_VERIFIED`. No BLOCKER or MAJOR finding remains before implementation.

## Completion rule

Move this plan to `docs/exec-plans/completed/` only after all applicable gates have
evidence. A better result remains provisional with one reviewer and does not itself
promote the model.

## Implementation and verification evidence

- Implemented a default-off `selected_ids_json_v1` direct-evidence prompt profile.
  It is enabled only by the synthetic local Ollama evaluation target, adds JSON-encoded
  IDs derived only from `context.selected_chunks` to the user message, and leaves the
  generic application prompt unchanged.
- The target now identifies itself as `synthetic-corpus-ollama-generation@1.3.0`.
  Source-artifact eligibility is a shared bounded allow-list for exactly historical
  `1.2.0` and reviewed experimental `1.3.0`; `1.3.1` and other identities reject.
- The content-free configuration fingerprint includes both the profile ID and the
  SHA-256 of the static effective direct-evidence instruction. No prompt text, source
  content, question, selected ID values, provider response, endpoint, or secret is
  stored in artifacts.
- Pre-replay verification: focused tests `106 passed, 3 skipped`; full suite
  `236 passed, 7 skipped`; Ruff, format, mypy, and `git diff --check` passed. The four
  non-applicable pgvector skips require an explicitly configured disposable database;
  three Windows symbolic-link tests require an unavailable local privilege.
- Independent code review initially found a MAJOR fingerprint gap. It was remediated,
  re-reviewed as `PASS`; architecture and business reviews also passed. No remaining
  BLOCKER or MAJOR finding exists before final verification.

## Measured candidate result

- Smoke G001: `PASS` operationally; one supported claim, zero unsupported claims,
  and no failure. It was intentionally not scored or treated as a baseline.
- Fresh full replay: `PASS` operationally as
  `direct-evidence-qwen3-8b-20260827`, exact source SHA-256
  `c72e6a81a99e5955fa043fd4418abde69d2b5d6dceb396d74071bcab5ecd83af`.
  It used the pinned 50-question dataset/corpus, local `qwen3:8b`, `top_k=5`, reviewed
  policy, canonical loopback endpoint, and target version `1.3.0`.
- A newly generated local synthetic reviewer packet was independently reviewed by
  `reviewer_a`; no prior human verdict was reused. The strict 50-row assessment bound
  to this source was offline-scored with assessment SHA-256
  `c4bb0c1c6d375b258484c115f1014500411ab857c01f2d781ee30dc65d584e34`.
- Measured candidate human rates: correctness=0.86 (43/50), faithfulness=0.86
  (43/50), citation accuracy=0.86 (43/50). Six questions had direct evidence but
  received the safe refusal; one question had a `RagUnavailableError`.
- The deterministic retrieval values remain Recall@5=1.0, Hit Rate@5=1.0, MRR=1.0,
  Precision@5=0.2. Unsupported-claim rate is 0.1176470588, failure rate=0.02, total
  tokens=31,975 (30,273 input; 1,702 output), p50=5,563.5079 ms, and p95=9,752.4626 ms.
  Cost remains unavailable without a versioned provider price schedule.
- This is a descriptive controlled comparison only: it improves upon the prior
  `1.2.0` solo result (correctness/faithfulness 0.26, citation accuracy 0.24,
  unsupported-claim rate 0.76), but target version and configuration fingerprint are
  intentionally different, so the strict policy regression comparator is not invoked.
  One reviewer provides provisional evidence only; no promotion is claimed.

## Implementation evidence

- Implemented 2026-08-27: `RagPromptBuilder` retains the byte-for-byte generic user
  prompt by default. The local synthetic Ollama target alone opts into an
  evaluation-only direct-evidence profile, which appends a static instruction and a
  JSON-encoded selected-ID list in the user message. The list is derived in order
  only from `ContextWindow.selected_chunks`; no dynamic IDs enter the system prompt.
- Implemented 2026-08-27: the local synthetic Ollama target is now `1.3.0` and its
  content-free configuration fingerprint includes the fixed
  `selected_ids_json_v1` prompt-profile identifier. Existing loopback, marker-key,
  proxy-isolation, timeout, retry, and pinned-corpus controls were not changed.
- Implemented 2026-08-27: source-artifact writing and loading share a strict
  allow-list for exactly `synthetic-corpus-ollama-generation@1.2.0` and `@1.3.0`.
  Other target/version pairs remain fail-closed.
- Added targeted coverage for generic default preservation, selected-only JSON
  serialization and escaping, selected versus unselected citation behavior, local
  target/profile provenance, and strict `1.3.0` write/load/offline-score eligibility.
  The tests also retain rejection coverage for unreviewed target versions.
- Verification executed 2026-08-27: `pytest tests/unit/test_rag_service.py
  tests/unit/test_evaluation.py -q` passed (106 passed, 3 skipped only because this
  Windows session lacks symlink privilege); Ruff check, Ruff format check, mypy, and
  `git diff --check` passed.

## Plan deviations

- None. No local Ollama replay, assessment, reviewer packet, regression comparison,
  or measured result was created during implementation.

## Code-review remediation evidence

- Independent code review identified a MAJOR provenance defect: the configuration
  fingerprint included only the `selected_ids_json_v1` identifier, so changing the
  actual direct-evidence instruction text would not have changed source provenance.
- Remediated 2026-08-27: the `1.3.0` local synthetic target now includes the SHA-256
  of the effective static direct-evidence instruction in its content-free
  configuration fingerprint. The instruction text itself is not retained in artifacts.
- Targeted coverage mutates the actual `RagPromptBuilder` direct-evidence instruction
  and verifies that the `1.3.0` configuration fingerprint changes. No model replay or
  measured result was created as part of this remediation.
- Architecture documentation now distinguishes historical `1.2.0` source eligibility
  from the `1.3.0` local-only, default-off evaluation profile and records that their
  different provenance permits descriptive comparison only, not a policy regression
  comparison.

## Final verification

- Final verifier: `COMPLETE`. The original direct-evidence safe-refusal pattern was
  reduced from 38 reviewed refusals to six direct-evidence refusals in the fresh 50-row
  candidate assessment; the prompt hypothesis is supported for this synthetic sample.
- Targeted tests: `PASS` (106 passed, 3 Windows symbolic-link privilege skips).
  Regression suite: `PASS` (236 passed, 7 skips: four require configured disposable
  pgvector; three require the unavailable symbolic-link privilege). Ruff, formatting,
  mypy for 62 source files, and `git diff --check` passed.
- Independent reviews: code review passed after remediation of one MAJOR instruction
  fingerprint finding; architecture and business reviews passed. No unresolved BLOCKER
  or MAJOR finding exists.
- Residual risks: six refusals, one `RagUnavailableError`, higher p95 latency and token
  usage, unavailable cost, one reviewer, a small synthetic corpus, and intentionally
  non-comparable target/configuration provenance. These prevent promotion or a strict
  regression-gate pass.
- Final state: COMPLETE for the controlled Stage 8 experiment; NOT_PROMOTED for normal
  application behavior or model-quality policy.
