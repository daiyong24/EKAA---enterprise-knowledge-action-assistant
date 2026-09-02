# Stage 19 Report

## Goal

Test whether a bounded multi-agent route provides a measured benefit over the
Stage 14 single-agent baseline, while preserving safety and keeping single-agent
as the default when it does not.

## Current branch

Existing user branch; no commit or push performed.

## Repository status

Uncommitted Stage 19 implementation; unrelated changes preserved.

## Files created

* `backend/app/multi_agent/errors.py`
* `backend/app/multi_agent/models.py`
* `backend/app/multi_agent/routing.py`
* `backend/app/multi_agent/orchestrator.py`
* `backend/app/multi_agent/evaluation.py`
* `backend/app/multi_agent/telemetry.py`
* `backend/app/multi_agent/__init__.py`
* `tests/unit/test_multi_agent.py`
* `docs/STAGE_19_MULTI_AGENT_DESIGN.md`
* `docs/adr/ADR-014-bounded-multi-agent-candidate.md`
* `docs/exec-plans/completed/stage19_multi_agent_architecture.md`

## Files modified

* `AGENTS.md`, `ARCHITECTURE.md`, `README.md`, `ROADMAP.md`, and Stage 14
  evaluation models/scoring for provenance fingerprints.

## Implementation

The candidate router defaults to `single-agent`; only explicit specialization and
an enabled experiment select the three-worker route. The orchestrator uses immutable
state, digest-bound handoffs, one combined budget, strict worker output validation,
permission/approval digest preservation, duplicate-worker loop prevention, conflict
termination, and safe fallback/partial-failure results. It has no tool executor or
external side effect. Its result is explicitly evaluation-only and cannot carry a
factual answer to a user-facing API.
An injected content-minimized telemetry sink records route, worker, handoff,
failure, conflict, and fallback lifecycle events with correlation IDs; sink failure
is isolated from execution. Synchronous handoff and fallback boundaries run in
bounded worker threads when needed, and asynchronous variants share the run
deadline.

The comparison harness reuses Stage 14 complete-batch scoring and rejects selective,
version-mismatched, fingerprint-mismatched, or scenario-mismatched comparisons. It
requires verifiable complete replicate artifacts (not a caller count), with
unique underlying baseline/candidate run IDs, and an authoritative scenario
manifest used to recompute the suite fingerprint; the caller-provided
`dataset_fingerprint` field alone is not trusted. It then requires strictly
positive success improvement, bounded latency/step/cost overhead, non-regressing
tool/argument accuracy, non-regressing escalation correctness (including the
Stage 14 escalation failure surface), and perfect approval compliance; otherwise
rollback means retain single-agent.
Three replicates alone are reproducibility evidence, not statistical significance;
adoption additionally requires a typed `StatisticalEvidence` artifact bound to the
replicates, manifest fingerprint, and observed effect, with authoritative,
independently verified powered-analysis fields. Missing evidence returns
`statistical_evidence_unavailable` and retains single-agent.

## Architecture decisions

See ADR-014. Multi-agent is a measured candidate, not an automatic replacement.

## Tests

Focused Stage 19 tests cover default routing, handoff delivery/loss, loop and
handoff budgets, permission mutation, conflicting claims, partial failure/fallback,
Stage 14 comparative/rollback decisions, escalation-correctness regression,
unnecessary-call regression, authoritative-manifest absence, model-copy fingerprint
tampering, and missing powered-statistical evidence.

## Test results

Focused tests: 27 passed. Full suite: 398 passed, 7 environment-dependent tests
skipped. Ruff lint, format check, strict mypy, and `git diff --check` passed.

## Manual verification

No provider, database, external tool, real approval, production deployment, or
external write was executed.

## Known limitations

State, handoff transport, and telemetry sink are in-memory reference boundaries.
Comparison adoption requires the authoritative scenario manifest; if it is absent
or an artifact fingerprint is forged at a model-copy boundary, comparison fails
closed or retains single-agent.
Synchronous adapter timeouts cannot kill the underlying thread; only explicitly
trusted side-effect-free callables are acceptable. No production multi-agent
adoption is claimed; repeated statistically powered evaluation,
durable state, distributed leases, authenticated worker identity, and operational
deployment remain future work.

## Security review

Permission and approval digests are preserved across every worker boundary; changed
values fail closed. Handoffs contain digests and bounded metadata only. The route
requires an evaluation-read-only capability token, cannot execute tools or
consequential writes, and all claims are treated as worker data rather than
instructions.

## Regression review

Existing single-agent, approval, evaluation, retrieval, reliability, deployment,
and security behavior is not replaced by the candidate. The default route remains
single-agent.

## Acceptance criteria

PASS for the local reference candidate and experiment harness. Adoption is deferred
because a measured benefit over the Stage 14 baseline has not been established.

## Next stage

Stage 20 — Portfolio Finalization.

No Stage 20 implementation performed.
