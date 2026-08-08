# Adaptive Verified Bug-Fix Workflow

Version 2.0.0 · Default mode: `AUTO`

This is the project workflow used with the Agent Library. Its purpose is to correct the actual root cause with the minimum safe change, evidence-led review, and honest verification. It must never substitute guessing, green but irrelevant tests, or confidence for evidence.

## Modes and triage

The Coordinator accepts `AUTO`, `LOW`, `MEDIUM`, or `HIGH`. In `AUTO`, classify from blast radius, uncertainty, business/architecture/data/security/contract impact, regression surface, and production impact. If LOW cannot be confidently justified, choose MEDIUM; if MEDIUM cannot be confidently justified due to material high-risk concerns, choose HIGH.

`LOW` is only for isolated deterministic behavior with an obvious or readily verifiable cause and narrow regression surface. It excludes security boundaries, auth, migrations, destructive data, public API changes, critical calculations, infrastructure, concurrency, and ambiguous product requirements.

`MEDIUM` covers multiple modules, client/server paths, integration and configuration behavior, asynchronous state, shared utilities, or material uncertainty.

`HIGH` is mandatory for authentication, authorization, security boundaries, credentials/secrets, sensitive data, database migrations or destructive data operations, production data, payment or critical calculations, breaking public APIs, production infrastructure, concurrency/races, privacy, critical external integrations, broad architecture changes, seriously uncertain causes, or repeated failed fixes. These are hard overrides: `AUTHENTICATION`, `AUTHORIZATION`, `SECURITY_BOUNDARY`, `SECRET_HANDLING`, `DESTRUCTIVE_DATA_CHANGE`, `DATABASE_MIGRATION`, `PRODUCTION_DATA`, `PUBLIC_BREAKING_API`, `CRITICAL_BUSINESS_CALCULATION`, `CONCURRENCY`, `CRITICAL_DATA_INTEGRITY`, and `PRODUCTION_INFRASTRUCTURE`.

Risk may escalate only (`LOW → MEDIUM → HIGH`). Record the prior level, trigger, and evidence; then run any missing gates at the new level. Do not automatically downgrade once material higher risk is known. A requested lower level never bypasses a mandatory safety gate.

## Shared invariants

- Classify conclusions as `CONFIRMED`, `SUPPORTED`, `HYPOTHESIS`, `UNKNOWN`, or `DISPROVEN`; never convert a hypothesis into a fact.
- Treat tests as evidence only. Use `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`, or `NOT_APPLICABLE`; never render `NOT_RUN` as `PASS`.
- Distinguish symptom, trigger, contributing factor, and primary root cause.
- Prefer the smallest correct change. Preserve business rules, API contracts, database semantics, security rules, workflows, compatibility, and unrelated behavior.
- Never hide errors by swallowing exceptions, disabling validation/auth, false success, deleting valid tests, or weakening assertions.
- Use progressive disclosure. Read `AGENTS.md`, active plan, `ARCHITECTURE.md`, `BUSINESS_RULES.md`, relevant specifications/contracts/code/tests/configuration/logs, then history only as evidence requires.
- Do not deploy, mutate production data, change secrets, or perform irreversible production actions without explicit authorization. Report local, CI, staging, and production verification distinctly.

## Role map

The Coordinator owns state, classification, agent selection, gates, escalation, failure routing, loop protection, and the final report. It treats sub-agent output as evidence rather than fact.

The Investigator establishes behavior, reproduction, execution path, earliest divergence, hypotheses, and a root-cause candidate. The Root Cause Reviewer tries to prove that candidate wrong. The Planner creates the smallest safe plan. The Plan Reviewer challenges that plan. The Implementer executes only an approved plan. The Code Reviewer inspects the actual diff. The Architecture and Business Reviewers validate their authoritative project documents. Specialists are conditional. The Final Verifier re-tests the original failure mechanism and evaluates completion.

Producer and required reviewer must be different agents when delegation is available. If independent agents are unavailable, record `INDEPENDENT_AGENT_UNAVAILABLE` and perform a separate second pass without claiming independence. See `docs/agents/AGENT_CONTRACT.md` and each native role file for exact role boundaries, outputs, and stop conditions.

## Routing

```text
LOW:    Coordinator investigation + root-cause confirmation
        → Implementer → Code Reviewer → Final Verifier

MEDIUM: Investigator → Root Cause Reviewer → Planner → Implementer
        → Code Reviewer → architecture/business check when relevant
        → Final Verifier

HIGH:   Investigator → Root Cause Reviewer → Planner → Plan Reviewer
        → Implementer → Code Reviewer → Architecture Reviewer + Business Reviewer
        → triggered specialist review → Final Verifier
```

For MEDIUM, use a Plan Reviewer when uncertainty remains, architecture/business behavior is affected, or regression surface is significant. For HIGH, independent root-cause review, plan review, code review, architecture/business review, and triggered specialist review are mandatory where available.

Specialist triggers: security for identity, authorization, access control, tokens, secrets, sensitive data, and security boundaries; database for schema/migration/persistence/data integrity; API for material contract changes; concurrency for races, concurrent writes, duplicate processing, retries, ordering, or transaction timing.

## Gates and execution

Before modification, record branch/revision, Git status, existing user changes, relevant documents, and environmental limits. Establish a reproduction if possible and trace the path to the earliest incorrect divergence. MEDIUM/HIGH work maintains an evidence ledger and hypothesis ledger in its execution plan.

LOW requires clear reproduction/evidence, code-path confirmation, and a causal explanation. MEDIUM requires `ROOT_CAUSE_VERIFIED` before planning/implementation. HIGH requires an adversarial independent root-cause review and cannot implement before `ROOT_CAUSE_VERIFIED`.

MEDIUM/HIGH plans explicitly state the verified root cause, intended behavior, behavior preserved, files to change and not change, exact change and reason, architecture/business/API/database/security/concurrency/performance impacts, tests, verification, rollback, and risks. HIGH cannot implement before `PLAN_VERIFIED`.

The Implementer rechecks the repository baseline before editing, protects existing changes, implements the minimum scope, adds or updates appropriate tests, and records commands and deviations. A minor equivalent deviation is recorded. A new migration, public API change, architecture component, security/busines change, or substantially broader scope is material: stop, return to planning, and re-review.

Review findings use `BLOCKER`, `MAJOR`, `MINOR`, `NIT`, or `NONE`. BLOCKER and MAJOR findings prevent completion. The Final Verifier evaluates the original scenario, targeted/regression/negative tests, static checks, lint, build, integration/E2E/full suites where applicable, business and architecture conformance, unresolved findings, and the final diff. A passing unrelated test never replaces original-failure verification.

## Failure routing and loop protection

Return to the earliest invalid assumption:

| Failure | Route |
|---|---|
| Insufficient evidence, rejected root cause, competing cause | Investigation |
| Rejected plan or material plan deviation | Planning |
| Local implementation defect or code-review MAJOR | Implementation |
| Architecture/business issue caused by plan | Planning |
| Final test finds coding defect | Implementation |
| Final verification disproves cause | Investigation |
| Required service unavailable | Blocked |
| Human product decision required | Escalated |

After three materially unsuccessful cycles at the same gate without significant new evidence, stop automatic retry, summarize attempts and uncertainty, identify missing capability or information, reassess earlier assumptions, and enter `BLOCKED` or `ESCALATED`. Meaningful progress introduces evidence, a hypothesis, a corrected assumption, a materially different implementation, a new capability, or new context.

## Durable execution plans and recovery

For every MEDIUM/HIGH task, create `docs/exec-plans/active/<task>.md`; move it to `docs/exec-plans/completed/` on completion. Record metadata, original issue, context and repository baseline, triage, reproduction/path, evidence/hypotheses, root-cause and plan reviews, implementation/deviations, code/architecture/business/specialist reviews, verification table, decision log, residual risks, limitations, and final state.

On recovery, read `AGENTS.md`, the active plan, Git status/diff, current revision, and compare actual state with the plan. Resume from the earliest unverified gate; never trust stale documentation blindly.

## Completion

Completion requires a sufficiently verified root cause, completed implementation, all relevant reviews, no unresolved BLOCKER/MAJOR, original issue resolved or sufficiently verified, relevant checks passed, business and architecture preserved, final diff inspected, and residual uncertainty disclosed. HIGH additionally requires independent root-cause verification, a verified plan, applicable specialist review, and stronger regression evidence.

The final result reports `COMPLETE`, `BLOCKED`, or `ESCALATED`; risk level; concise root cause and evidence; change and files; checks actually executed; reviews; remaining risks; and deployment verification state.
