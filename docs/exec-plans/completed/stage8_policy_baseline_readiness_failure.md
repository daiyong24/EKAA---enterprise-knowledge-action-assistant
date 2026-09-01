# Stage 8 Active-Baseline Readiness Failure Investigation

## Metadata

- Status: COMPLETED — environment-blocked, no code defect established
- Workflow: Adaptive Verified Bug-Fix, AUTO classification
- Date: 2026-09-01
- Scope: the policy-bound local synthetic baseline replay readiness failure only

## Trigger

The first fresh replay under active policy `1.1.0` safely failed before the evaluation
runner, source JSON/CSV, or reviewer packet. Its only permitted output was the
content-free readiness record: `outcome=failed`, duration `17,854.488 ms`, configured
model `qwen3:8b`, and run ID `baseline-policy-1-1-qwen3-8b-20260901`.

## Invariants

- Do not retry the failed replay or create replacement artifacts before the cause is
  investigated and reviewed.
- Preserve the exact synthetic-only corpus, canonical loopback provider boundary,
  zero-retry policy, retrieval/citation behavior, normal runtime, policy thresholds,
  and all source-binding rules.
- Keep all failure output content-free. Do not record prompt, response, provider payload,
  endpoint, exception detail, answer, or usage in this plan or a source artifact.
- Existing user-owned changes and local artifacts are preserved.

## Initial evidence

| Evidence | Status | Notes |
| --- | --- | --- |
| Active policy validates | CONFIRMED | `1.1.0`, status `active`, SHA-256 `691a2d0d66105f738d82c801b74dc334a1d6f1752fb21cc74ac755f52472f345`. |
| Readiness failure | CONFIRMED | One safe failed readiness record at 17,854.488 ms. |
| Artifact exclusion | CONFIRMED | Expected JSON, CSV, and packet paths do not exist after failure. |
| Provider root cause | UNKNOWN | Safe readiness output intentionally withholds provider/validation detail. |

## Reproduced preflight outcome

- The independently reviewed `--local-ollama-preflight` implementation was run once on
  2026-09-01 with no resident model. It safely failed after 14,447.998 ms with the same
  six-field record and fixed public error. It created no JSON, CSV, packet, assessment,
  source, or baseline artifact; `ollama ps` afterward still showed no resident model.
- This is the second fresh command to fail at the content-free readiness boundary after
  the earlier 17,854.488 ms full-replay gate failure. It is not a retry and must not be
  counted as an evaluation observation or quality result.
- The preflight code was reviewed and final-verified before execution: it constructs only
  the private local provider/readiness helper, not retrieval, RAG, context, citations,
  runner, provenance, review, or artifact objects. The safe failure therefore does not
  implicate those excluded components.

## Operator readiness attempt

- A separate static, content-free local model warm-up was attempted once after the
  blocked preflight. The local Ollama model runtime terminated during GPU/CUDA backend
  initialization, before returning a completion or making the model resident. No EKAA
  dataset, corpus, source artifact, packet, assessment, or scored result was involved.
- This is direct evidence of an external local runtime/environment fault. It does not
  justify changing EKAA timeout, retry, retrieval, citation, normal runtime, or policy
  controls. The active-policy baseline remains environment-blocked pending operator
  restoration of a stable local Ollama model runtime.

## Required gates

1. Investigator classifies the failure through safe, no-artifact diagnostics.
2. Root-cause reviewer verifies or limits the conclusion and rejects unsupported fixes.
3. If a code or policy change is justified, plan, review, implement, test, review, and
   final-verify it before a fresh replay. Otherwise document the operating prerequisite.
4. Only after the gate passes, run one new policy-bound full replay and obtain fresh,
   source-bound assessments from both reviewers.

## Final investigation outcome

- Root-cause recheck decision: `BASELINE_ENVIRONMENT_BLOCKED`. The active-policy full
  replay, the independently reviewed readiness-only preflight, and one separate static
  content-free Ollama warm-up all failed before a stable local model completion. The
  warm-up terminated during local GPU/CUDA backend initialization; the precise provider
  subtype remains intentionally undisclosed at the EKAA boundary.
- The failure is external to the evaluated EKAA path. No evidence implicates timeout,
  retry, retrieval, reranking, citation validation, normal runtime behavior, or policy
  thresholds. No such control was changed.
- The full replay and preflight both failed closed before retrieval, generation,
  scoring, reviewer material, or artifact writes. No active-policy source artifact,
  CSV, packet, assessment, or baseline result exists to promote.
- Implementation review, architecture/business review, and final verification all
  approved the readiness-only preflight. Regression checks remain `267 passed,
  7 skipped`; the skips are the documented external pgvector and Windows symlink
  privilege cases.
- This plan is closed as an environment limitation, not as a successful baseline
  activation. The next authorized action is one operator-side static, content-free
  Ollama completion after the local runtime/GPU environment is restored, followed by
  exactly one new policy-bound full replay. No automatic retry is permitted.
- Read-only post-checks confirm Ollama `0.33.2`, `qwen3:8b` installed but no model is
  resident in `ollama ps`, and the local RTX 3050 (driver `581.29`) visible and idle.
  These observations support the environment classification but do not identify the
  underlying driver/runtime subtype.

## Subsequent operator unblock

- After the plan was closed, the operator restored a stable CPU-only Ollama runtime
  and completed the required static `READY.` request. The approved preflight then
  succeeded once in 3,218.685 ms.
- One subsequent full replay under active policy `1.1.0` completed with 50 records and
  produced the source artifact
  `C:\safe-local\stage8_baseline_policy_1_1_qwen3_8b_source_20260901_cpu.json`.
  Its raw SHA-256 is
  `493275a91233b04d4d3e4e6bf4cda60b32848993f024da28130a128f83dfc413`; strict source
  loading passed. It recorded 15 `RagUnavailableError` rows (failure rate 0.30),
  Recall@5/Hit Rate@5/MRR of 1.0, Precision@5 of 0.2, and unsupported-claim rate
  0.1315789474. Two fresh source-bound assessments were then scored successfully:
  reviewer-1 correctness/faithfulness/citation accuracy `0.60/0.60/0.60`, reviewer-2
  `0.58/0.60/0.60`, with 98% correctness agreement and 100% agreement on faithfulness
  and citation accuracy. The run remains not promoted because of the 30% provider
  failure rate, low human scores, and lack of broader calibrated review; CPU-only
  operation is recorded as configuration context, not a production performance claim.
- This later replay does not invalidate the original incident classification: the
  earlier failures were correctly diagnosed as environment-blocked at that time, and
  no EKAA control was changed to obtain this result.

## Approved implementation candidate

- Status: IMPLEMENTED and independently approved after correction of the code-review
  MAJOR.
- Correction: the initial CLI implementation incorrectly constructed the full
  generation target during preflight. The revised implementation uses a separate
  readiness-only factory, preserving input validation while preventing evaluation
  component construction.
- Scope: add the private CLI-only `--local-ollama-preflight` mode. It requires the
  full reviewed dataset, policy, approved synthetic Ollama corpus, explicit local
  opt-in, and bounded run ID; it forbids output paths, assessments, smoke selection,
  review packets, and non-Ollama targets.
- Behavior: validate dataset/policy/corpus coverage and approval before provider target
  construction, then construct a readiness-only provider boundary and call the shared
  static readiness helper exactly once. The readiness factory validates only
  model/endpoint/run ID/top-K and constructs no corpus/evidence/retrieval/RAG/context/
  citation/provenance/review object. The full target delegates to the same helper while
  retaining its existing same-provider and once-only state. Emit its six-field
  content-free record only. A failed outcome exits with the fixed safe readiness error
  before runner/retrieval/artifact/reviewer-packet work.
- Preserved behavior: the normal full local replay retains its own readiness gate;
  preflight cannot make a replay, activate a baseline, or guarantee later availability.
- Verification: focused mock tests cover negative arguments, validation ordering,
  successful single readiness call, and failed readiness artifact/runner exclusion.
  Independent architecture/business review and final verification approved the
  implementation; the full suite passed with `267 passed, 7 skipped`. The real
  preflight was executed once after those reviews and its safe failure is documented
  above; no replacement replay was run.
