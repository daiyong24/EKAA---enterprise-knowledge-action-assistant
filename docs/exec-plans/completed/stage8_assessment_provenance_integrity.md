# Stage 8 Assessment Provenance Integrity — Execution Plan

## Metadata

- Status: complete
- Risk: HIGH (`CRITICAL_DATA_INTEGRITY`)
- Coordinator: Codex
- Started: 2026-08-25
- Scope: Stage 8 human-assessment provenance only; synthetic/public data only.

## Original issue

Human assessment JSON currently contains only per-question judgements, reviewer, and
calibration-set ID. It is not explicitly bound to the exact content-free generation
replay/model/configuration it evaluates. A later full replay can therefore accept
scores that were recorded from a different generated output, undermining an eventual
quality baseline without producing a schema error.

## Repository baseline

- Branch/revision: `main` / `6473ff5`.
- Existing user/worktree changes: the Stage 8 implementation and reports are
  uncommitted, including evaluation code, docs, and earlier Stage 0–7 changes. They
  must be preserved; no reset, checkout, or unrelated edit is permitted.
- Environment: local Ollama is intentionally opt-in and only the approved synthetic
  corpus may enter the provider path. No database, protected data, cloud provider,
  secret, API route, retrieval/reranking change, or Stage 9 feature is in scope.

## Evidence ledger

| Evidence | Classification | Detail |
| --- | --- | --- |
| `HumanAssessment` fields | CONFIRMED | Contains question judgement values, reviewer, and calibration-set ID only. |
| Assessment JSON schema | CONFIRMED | `load_assessments` accepts no replay/model/configuration binding. |
| CLI flow | CONFIRMED | Loads assessments before building the target and passes only a derived calibration ID to provenance. |
| Runner/regression flow | CONFIRMED | Computes human rates from supplied records; comparisons require calibration ID but not an assessment fingerprint or source-run binding. |
| Misapplied human scores accepted | SUPPORTED | The code path exposes no value with which to prove that a submitted score belongs to the currently evaluated generated output. Reproduction and independent review are required before implementation. |

## Hypothesis ledger

| Hypothesis | Status | Evidence needed |
| --- | --- | --- |
| The primary defect is missing assessment-to-run provenance binding, rather than a metric-formula error. | HYPOTHESIS | Investigator trace and adversarial root-cause review. |
| A versioned, strict assessment envelope bound to content-free source-run provenance can fix the issue without retaining answer/content. | HYPOTHESIS | Approved plan, tests, architecture/business review. |

## Investigation and root-cause review

- Investigator reproduction: PASS. A temporary content-free CLI reproduction attached
  one calibrated assessment file to two full-dataset fixture replays with different run
  IDs and configuration fingerprints. Both summaries accepted identical human metrics.
  No provider, database, question/answer/corpus text, or secret was used or retained.
- Earliest divergence: `load_assessments` accepts schema-v1 records without a source
  identity; `cli._run` later retains only the calibration-set string. The runner then
  joins by question ID and calculates rates from the supplied booleans.
- Root-cause review: `ROOT_CAUSE_VERIFIED`. Existing comparability checks reduce some
  mismatch risk but cannot establish that a reviewer evaluated the output from this
  particular replay; model/configuration identity alone is insufficient because output
  can vary across runs.
- Required invariant: a scoring assessment must bind to a validated, content-free,
  full-dataset source artifact and exact dataset/corpus/K/target/model/configuration/
  policy/source-run identity. The assessment artifact must have its own recorded
  fingerprint. Missing, legacy, malformed, partial, or mismatched evidence fails
  before summary emission. This records provenance; it does not claim cryptographic
  proof that a human viewed a particular answer, which is intentionally not retained.

## Intended behavior

Human judgements may contribute to Stage 8 metrics only when their assessment file
declares and exactly matches the source evaluation's permitted content-free identity
(at minimum dataset/corpus/K/target/model/configuration/policy and **both** source run
ID and raw-byte source-artifact SHA-256). The assessment file itself must be
fingerprinted and its identity recorded in output provenance. Missing, mismatched, or
incomplete binding must fail closed before a result is summarized. The format must
retain no questions, answers, chunks, citations, provider payloads, endpoint,
credentials, or protected data.

## Behavior preserved

- No LLM-as-judge and no invented human judgements.
- Existing non-assessment evaluation replays remain usable and retain null human
  metrics/calibration identity.
- The local Ollama boundary, approved synthetic corpus, 10-second phase timeout,
  generation/retrieval behavior, public APIs, and database behavior do not change.
- Regression comparison remains strict and continues to reject non-comparable runs.

## Planned gates

1. Investigator establishes an executable reproduction and earliest divergence.
2. Independent root-cause reviewer attempts to disprove the proposed cause.
3. Planner proposes the smallest fail-closed schema/provenance change.
4. Plan reviewer verifies scope, compatibility, privacy, and test coverage.
5. Implementer changes only approved files and adds regression/negative tests.
6. Code, architecture, business, and applicable security reviews inspect the actual
   diff; no BLOCKER/MAJOR finding may remain.
7. Final verifier replays the original mismatch case, targeted/full checks, static
   checks, and final-diff inspection.

## Revised implementation contract

### Schema v2 assessment bundle

The loader accepts exactly this envelope: `schema_version`, `calibration_set_id`,
`source`, and `assessments`. `source` accepts exactly `source_artifact_sha256`,
`source_run_id`, `dataset_id`, `dataset_version`, `dataset_sha256`, `corpus_sha256`,
`top_k`, `target_id`, `target_version`, `model_identity`, `configuration_sha256`,
`policy_version`, and `policy_sha256`. Each assessment row accepts exactly
`question_id`, `correctness`, `faithfulness`, `citation_accuracy`, and `reviewer_id`.
IDs use a bounded safe identifier grammar; SHA values are lower-case 64-character hex;
`top_k` is a positive integer but never a boolean; verdicts are strict booleans or null.
Unknown, duplicate, blank, oversized, malformed, or schema-v1 values reject. The bundle
must cover each reviewed question exactly once. A human metric is only calculated if all
covered rows supply a non-null verdict for that metric; otherwise that metric is null.

### Eligible source artifact and read-once validation

New generation-source artifacts use a strict versioned envelope with exact keys
`artifact_schema_version: 2`, `artifact_kind: "stage8_generation_source"`, and
`summary`. Only a fresh, unscored, `full_dataset`, approved synthetic-generation target
artifact with a non-empty model and run ID is eligible. Legacy/schema-less files,
fixture/retrieval targets, smoke scope, hand-authored conversions, and previously scored
artifacts are permanently non-scoreable.

The reader reads source bytes once and computes SHA-256 over exactly those bytes. It
uses allowlisted top-level/provenance/retrieval/record fields and safe bounded strings;
it rejects content-bearing fields, duplicate JSON keys, booleans used as numbers,
negative/non-finite numeric values, invalid hashes, or unknown data. Before scoring it
requires exact unique dataset record coverage, dataset-derived expected-count invariants,
valid source identity and null human/assessment provenance, then recomputes retrieval,
unsupported-claim, latency, token, and failure aggregates from the records. A mismatch
between computed and declared values rejects; the artifact cannot be trusted merely
because its hash was copied into an assessment bundle.

### Offline-only scoring and provenance

Run provenance gains nullable `assessment_artifact_sha256` and
`assessment_source_artifact_sha256`. A live target replay always produces unassessed
human metrics and null new fields, and rejects either assessment flag before Settings,
target/provider/retrieval/database construction or output construction. The mutually
exclusive offline scoring mode requires exactly `--assessments` plus
`--assessment-source-artifact`, permits no live target/Ollama/smoke/run-ID argument,
validates the source and every bound source field including **both** source run ID and
raw-byte source SHA, and applies human rates without any target/provider/database work.
It fingerprints the raw assessment bytes and writes both hashes into scored provenance.

All resolved input and JSON/CSV output paths must be distinct (including links), and
all validation/signing-free artifact construction must finish before either output is
created or truncated. Failed validation leaves inputs and outputs untouched.

### Comparison, scope, and tests

Regression rejects partial/missing provenance pairs, human metrics without a complete
scored state, or other internally inconsistent scored summaries before findings. It
continues to allow independently reviewed candidate/baseline runs to have distinct
source run and artifact hashes when dataset/corpus/K/target/model/configuration/policy
and calibration protocol are otherwise comparable. No HMAC, signing key, secret/config,
or key lifecycle is introduced: raw-byte hashes provide binding only, not proof that a
human saw intentionally unretained answer content.

Tests cover valid propagation; run-only/hash-only and all identity mismatches; schema,
scope, target, source-structure, type, aggregate-forgery, coverage, collision/no-write,
reader/provider isolation, privacy exclusion, normal unassessed replay, and regression
self-consistency cases. The original stale-score reproduction must fail before writing.

Files expected to change: `backend/app/evaluation/{models,dataset,runner,cli,regression}.py`,
new `backend/app/evaluation/artifacts.py`, `tests/unit/test_evaluation.py`, `README.md`,
`docs/STAGE_8_EVALUATION_DESIGN.md`, and `docs/STAGE_8_REPORT.md`.

Files explicitly out of scope: API/RAG/LLM/retrieval/embedding code, database and
migrations, Ollama configuration, dataset/corpus/policy content, dependencies, Docker,
CI, authentication, frontend, and all later stages.

## Plan-review findings and revision required

`PLAN_REJECTED` pending the following BLOCKER corrections:

1. Require **both** a validated raw-byte source-artifact SHA-256 and a non-empty source
   run ID. Each must exactly match the eligible source artifact; neither is sufficient
   alone.
2. Define a strict, explicit versioned generation-artifact schema. Legacy/schema-less,
   fixture, retrieval-only, smoke, hand-authored, or previously scored artifacts are
   permanently non-scoreable; conversion is prohibited and a fresh full replay is
   required.
3. The source reader must allowlist every key and type, reject non-finite/boolean
   numeric values and unbounded/content-like strings, validate complete unique question
   coverage and dataset-derived count invariants, and recompute aggregate metrics from
   records before scores are applied. It must read source bytes once and fingerprint
   those exact bytes rather than a reserialized form.
4. Isolate scoring in an offline CLI mode. Every live replay mode must reject direct
   assessment input before target, provider, settings, retrieval service, database, or
   output construction. All input/output resolved paths must differ and validation must
   finish before either output is created or truncated.
5. Specify assessment v2 exact bundle/row fields, lower-case 64-character hashes and
   bounded identifiers. For a score to be reported, require complete reviewed-question
   coverage and a non-null judgement for that metric; otherwise the metric is explicitly
   unavailable, never silently calculated from a partial subset.
6. Regression must reject internally inconsistent scored summaries before emitting
   findings, while still allowing separately reviewed candidate/baseline artifacts to
   have different source and assessment hashes when their normal comparable identity and
   calibration protocol match.

The revised test matrix must include run-only/hash-only binding, legacy and wrong-target
artifacts, scope/model/configuration/policy/corpus/dataset/K mismatches, forged
aggregates, malformed numeric values, all collision/no-write cases, reader/provider
isolation, byte-fingerprint stability, and output content exclusion.

## Risks and rollback

- Risk: a compatibility path could permit unbound scores. Mitigation: no assessment
  data is accepted unless its binding is complete and exact.
- Risk: a provenance field could retain sensitive data. Mitigation: allowlist only
  content-free hashes/identifiers and test artifact exclusion.
- Rollback: revert only this scoped commit/change after preserving unrelated worktree
  changes; no data migration or external state exists.

## Decision log

- 2026-08-25: Classified HIGH because this controls integrity of quality evidence used
  to decide whether grounded RAG behavior may be promoted.
- 2026-08-25: `ROOT_CAUSE_VERIFIED` by independent adversarial review.
- 2026-08-25: Initial plan was rejected for ambiguous binding and underspecified source
  validation. The revised raw-byte, dual-binding, offline-only plan was independently
  marked `PLAN_VERIFIED`; HMAC/key-management scope was deliberately excluded.
- 2026-08-26: Implementation added the approved schema/artifact/CLI/provenance guards.
  Reviews found and fixed three additional fail-closed gaps before completion: scored
  artifact kind labelling, input/output hard-link aliases, and producer-side bounded
  run/model identity validation.

## Implementation and verification

- Implementation: complete. The change remains within `backend.app.evaluation`, its
  tests, and Stage 8 documentation. No dependency, API, RAG/retrieval, provider
  behavior, database, secret/configuration, Docker, auth, or protected-data change was
  introduced.
- Original reproduction: PASS. An unbound/stale or source-mismatched assessment now
  fails before scoring output; a valid complete v2 bundle can be applied only offline
  to its exact eligible source artifact and records both raw-byte hashes.
- Collision reproduction: PASS for equal paths and existing hard links across offline
  and all live target inputs; source bytes stay unchanged and no provider/target is
  constructed for rejected Ollama input collisions. Symbolic-link test execution is
  NOT_RUN on this Windows account because `WinError 1314` denies link creation; the
  identical `resolve()` guard was source-inspected and documented as an environment
  limitation rather than passed execution evidence.
- Checks: PASS — final full suite `230 passed, 7 skipped` (four explicitly configured
  PostgreSQL/pgvector integration skips; three Windows symbolic-link privilege skips),
  Ruff lint/format, strict mypy, and `git diff --check`.
- Independent reviews: root-cause `ROOT_CAUSE_VERIFIED`; plan `PLAN_VERIFIED` after one
  rejected/revised cycle; code PASS; architecture PASS after correcting artifact kind;
  business PASS; security PASS after correcting hard-link/live-input handling and
  producer-side identifiers; final verification COMPLETE. No BLOCKER or MAJOR remains.
- Residual limitation: hashes prove which content-free evidence files were supplied,
  not that a human viewed an answer whose content is intentionally not retained. Human
  quality metrics remain unavailable until a reviewer supplies a fresh complete v2
  assessment bundle for a fresh eligible v2 local-generation source artifact.

## Final state

COMPLETE — assessment evidence is now fail-closed and bound to exact eligible source
replays without expanding Stage 8 scope. Stage 8 itself remains `PENDING /
NOT_PROMOTED`; this fixes evidence integrity but does not invent the outstanding human
quality measurements or improve the measured model quality.
