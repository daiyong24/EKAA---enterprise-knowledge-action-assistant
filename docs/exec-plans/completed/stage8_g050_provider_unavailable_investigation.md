# Stage 8 G050 Provider-Unavailable Investigation

## Metadata

- Status: COMPLETED
- Workflow: Adaptive Verified Bug-Fix, AUTO classified as MEDIUM
- Date: 2026-08-27
- Scope: one local synthetic Stage 8 generation failure only

## Original issue

The controlled `qwen3:8b` v1.3 full replay completed 49 normal rows and one
`RagUnavailableError` for G050. Retrieval still returned five chunks with one expected
match. The content-free record has no provider usage and a measured local duration of
10,230.2789 ms, slightly beyond the fixed 10-second HTTPX phase timeout.

## Boundaries

Investigate whether the failure came from the local provider, timeout profile, resource
state, transport, or an EKAA code path. Preserve synthetic-only content, canonical
loopback-only egress, fixed local profile safety controls, retrieval/reranking, citation
validation, public API behavior, corpus/dataset/policy, and normal runtime behavior.
Do not assume a timeout is the confirmed root cause merely because the measurement is
slightly above ten seconds.

## Repository baseline

- Revision: `6473ff5`
- Existing uncommitted Stage 8 work is user-owned and must be preserved.
- Candidate source artifact:
  `C:\safe-local\stage8_direct_evidence_qwen3_8b_source_20260827.json`.

## Evidence ledger

| Evidence | Status | Notes |
| --- | --- | --- |
| Failed record | CONFIRMED | G050 is the only `RagUnavailableError`; retrieval found five chunks and one expected chunk. |
| Local duration | CONFIRMED | 10,230.2789 ms; source artifact does not retain provider text/payload. |
| Fixed profile at failure | CONFIRMED | The failed `1.3.0` target used 10-second HTTPX phase timeouts and zero retries. |
| Primary root cause | UNKNOWN | Requires reproduction and code/provider-path investigation. |

## Hypothesis ledger

| Hypothesis | Status | Evidence needed |
| --- | --- | --- |
| G050 hit the fixed read-phase timeout under local model/resource variability. | HYPOTHESIS | Controlled same-profile single-question reproduction plus exception-path evidence. |
| Provider/server restart or connection interruption caused the error. | HYPOTHESIS | Local diagnostics/log evidence without secrets. |
| EKAA incorrectly maps a non-timeout provider failure to `RagUnavailableError`. | HYPOTHESIS | Trace error mapping and reproduce with controlled provider error. |
| Retrieval/context/citation handling caused the error. | DISFAVORED | Failure record has no provider usage but retrieval completed; must still inspect path. |

## Investigation and planning gates

- Investigator: `READY_FOR_REVIEW`. It reproduced G050 at 10,967.933 ms and an exact
  direct-provider prompt at 10,544.735 ms as `httpx.ReadTimeout`.
- Root-cause reviewer: `ROOT_CAUSE_VERIFIED`. The confirmed EKAA-boundary cause is a
  local-provider read-phase timeout, correctly mapped through `ProviderTimeoutError`
  to `RagUnavailableError` because the profile permits zero retries. Ollama's internal
  reason remains unknown.
- Plan: change only the local synthetic evaluation phase timeout from 10.0 to 12.0
  seconds, preserve zero retries and all local-only controls, bump target to `1.3.1`,
  retain profile timeout in the content-free fingerprint, and allow-list only the exact
  new source identity alongside historical `1.2.0` and `1.3.0`.
- Plan review: `PLAN_VERIFIED`. Twelve seconds is a bounded evidence-supported change;
  it is an HTTPX phase timeout, not a wall-clock/SLO guarantee. A fresh source, review,
  and descriptive-only reporting remain mandatory.

## Implementation record

- Status: IMPLEMENTED; replay and independent review remain pending.
- Changed only the private synthetic-only local evaluation profile from a 10.0-second
  to a 12.0-second HTTPX phase timeout, preserved zero retries, and bumped its target
  version to `1.3.1`.
- Kept the timeout in the non-secret configuration fingerprint and allow-listed exactly
  `1.3.1` beside historical `1.2.0` and `1.3.0` for content-free offline scoring.
- Added mocked `httpx.ReadTimeout` coverage for the existing provider mapping, plus
  evaluation target/profile/fingerprint/source-eligibility/offline-scoring tests.
- No local Ollama replay, assessment, or reliability outcome has been recorded by this
  implementation step.
- Focused verification: `.venv\Scripts\python.exe -m pytest
  tests/unit/test_openai_compatible_provider.py tests/unit/test_evaluation.py -q`
  passed with 96 tests; three existing symbolic-link cases were skipped because this
  Windows operator lacks symbolic-link privilege.
- Full verification: `.venv\Scripts\python.exe -m pytest -q` passed with 237 tests;
  four existing PostgreSQL/pgvector cases were skipped without a disposable test
  database and the same three Windows symbolic-link cases were skipped.
  `.venv\Scripts\ruff.exe check .`, `.venv\Scripts\ruff.exe format --check .`,
  `.venv\Scripts\mypy.exe backend`, and `git diff --check` passed.

## Verified readiness prerequisite remediation

- Root-cause review: `ROOT_CAUSE_VERIFIED`. A controlled no-artifact sequence showed a
  resident local model, one successful generic completion, and an immediate successful
  exact standard G050 completion. This supports cold initialization/readiness as the
  trigger; it does not claim that a successful warm-up guarantees later availability.
- Plan and plan review: `PLAN_VERIFIED`. A full-dataset-only `1.4.0` target performs
  exactly one static generic non-dataset readiness completion after dataset/corpus/policy/
  path validation and before `EvaluationRunner`. It uses the same private provider
  instance and fixed local controls as generation; success requires `stop` plus the
  configured model. Answer and usage are immediately discarded.
- Implementation: `IMPLEMENTED`. Ordinary readiness errors,
  bad model identity, and other `Exception` failures become a content-free failed record
  with exactly `event,outcome,duration_ms,model,profile,correlation`, then abort before
  runner execution, source JSON/CSV, or a reviewer packet. The CLI reports only the
  fixed error `local readiness check failed`; it does not expose exception details.
  No `BaseException` is caught and no readiness retry occurs. Smoke, fixture,
  deterministic retrieval, and offline scoring make zero readiness calls; smoke remains
  historical `1.3.2`. The full `1.4.0` configuration fingerprint records the complete
  readiness protocol and `1.4.0` is added to the strict scoreable-source allow-list;
  `1.4.1` is rejected.
- Code-review correction: the full target itself now blocks `evaluate()` before any
  retrieval or provider work unless exactly one successful readiness call has completed.
  A duplicate readiness attempt, including after a failed first attempt, is rejected
  before issuing a second provider completion. This closes the programmatic bypass while
  preserving the historical `1.3.2` smoke path.
- Focused verification: `tests/unit/test_evaluation.py` passed 102 tests with three
  existing Windows symbolic-link skips. Ruff check/format and `mypy backend` passed.
- Fresh replay: `PASS` on 2026-08-29. The intentionally started local Ollama service
  completed the one generic readiness call as `ready` in 9,807.255 ms, then completed
  the new 50-question `1.4.0` replay with run ID `readiness-qwen3-8b-20260829`, source
  SHA-256 `777e5f054d879d3e0ae7128d41bee461e8da92b6e7dfc80a953201a6cec3e2b7`, and
  configuration SHA-256 `610d5101efecece5e669e1702e7ea0e6a9789cb0ef6dd0be1d5554fac0f92230`.
  It wrote exactly 50 source records, 50 CSV data rows, and a 50-question local reviewer
  packet bound to that exact source SHA. It had Recall@5=1.0 and zero failures,
  including zero `RagUnavailableError` rows. Readiness content, duration, usage, and
  outcome are absent from the source JSON/CSV, reviewer packet, and evaluation metrics.
- Reviews: code review and architecture/business review both approved the implementation
  after the target-level one-successful-call enforcement correction. Final verification
  passed the fresh replay, source/CSV/packet integrity checks, full test suite, static
  checks, and scope/business boundaries.
- Remaining Stage 8 work is deliberately separate from this resolved provider incident:
  a fresh 50-question human assessment of this new packet, offline scoring bound to this
  source SHA, and any later promotion decision. Do not reuse prior assessments or make a
  reliability guarantee from this one successful replay.
- Architecture-review MINOR remediation: corrected the design document to describe
  `1.3.2` as implemented with a fresh full replay pending, rather than as planned;
  no reliability, wall-clock, or SLO claim was added.
- Code review MINOR remediation: corrected the report's stale
  eligible-version statement to include reviewed candidate `1.3.1` without asserting a
  replay result, and expanded strict write/load/offline-score eligibility coverage to
  `1.2.0`, `1.3.0`, and `1.3.1`; unreviewed `1.3.2` remains rejected. Focused
  evaluation tests passed 93 with three existing Windows symbolic-link skips; the
  complete suite passed 239 with the same three skips and four existing PostgreSQL/
  pgvector environment skips. Ruff, format, mypy, and diff checks passed.

## Gates

1. Investigator and root-cause reviewer establish a supported/verified cause.
2. Planner defines the minimum safe remediation, if any; do not weaken reliability or
   safety controls merely to obtain a green replay.
3. Implementer, code review, relevant architecture/business review, and final verifier
   run only if a code/documentation change is justified.
4. Run targeted reproduction and, if a change is made, a fresh full replay before
   describing the candidate reliability result.

## Completion rule

Archive only after the cause is verified or explicitly documented as an unresolved local
environment limitation, all applicable checks run, and no promotion claim is made.

## First remediation outcome

- The reviewed `1.3.1` implementation increased only the local evaluation HTTPX phase
  timeout from 10 to 12 seconds, retained zero retries, and passed 239 tests with the
  required code/architecture/business reviews.
- G050 `1.3.1` smoke: `FAIL` for the original reliability condition. The CLI process
  completed safely with one failure row, but G050 again produced `RagUnavailableError`,
  zero usage, five retrieved chunks/one expected match, and 13,026.6222 ms latency.
- The 12-second bound therefore did not cover observed current local tail latency. No
  full replay, assessment, score, or promotion conclusion was produced. The plan's
  sufficiency assumption is invalid and routing returns to investigation before any
  further timeout/retry change.

## Final 1.3.2 candidate plan

- Status: IMPLEMENTED; replay and independent review remain pending.
- Evidence: `1.3.1` preserved zero retries but G050 still safely failed after
  13,026.6222 ms. A separate local diagnostic, which intentionally wrote no artifact,
  also timed out after 13.0739 seconds.
- Change only the private synthetic evaluation HTTPX phase timeout from 12.0 to 20.0
  seconds, retain zero retries and every local-only control, bump the target to `1.3.2`,
  retain timeout fingerprinting, and allow-list exact `1.3.2` beside `1.2.0`, `1.3.0`,
  and `1.3.1`. `1.3.3` must remain fail-closed.
- Do not make a reliability, replay, assessment, or promotion claim until a fresh full
  `1.3.2` replay and independent review complete.
- Verification: `.venv\Scripts\python.exe -m pytest
  tests/unit/test_openai_compatible_provider.py tests/unit/test_evaluation.py -q`
  passed 99 tests with three existing Windows symbolic-link skips.
  `.venv\Scripts\python.exe -m pytest -q` passed 240 tests with four existing
  PostgreSQL/pgvector environment skips and the same three symbolic-link skips.
  `.venv\Scripts\ruff.exe check .`, `.venv\Scripts\ruff.exe format --check .`,
  `.venv\Scripts\mypy.exe backend`, and `git diff --check` passed.
