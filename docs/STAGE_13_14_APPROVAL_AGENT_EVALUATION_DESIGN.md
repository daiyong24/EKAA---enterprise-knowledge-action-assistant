# Stage 13/14 — Approval and Agent Evaluation Design

## Scope

This combined vertical slice adds a provider-neutral human approval boundary and
reproducible scoring for bounded single-agent traces. It uses only synthetic/public
data and keeps consequential integrations disabled: the executor is an injected
boundary and no external write is registered by this repository.

## Approval invariants

An approval request stores an immutable action and canonical parameter digest. It
requires an authenticated requester, an authorized approver role, an expiry time,
and an explicit one-time transition from `approved` to `executing`. Parameter or
action mutation requires a new request. Concurrent calls are serialized, and an
idempotency key makes a completed execution replay-safe. A failed execution is
terminal. Cancellation/crash recovery moves an in-flight request to
`execution_unknown`; it never automatically retries a potentially completed write
and requires manual reconciliation.

Audit events contain actor, correlation, state, action hash, and parameter hash, but
never action arguments, execution output, prompts, or secrets.

## Evaluation invariants

`agent_evaluation` scores every declared scenario/trace pair. It requires a strict
one-to-one mapping, calculates tool and argument accuracy, success, steps, retries,
unnecessary calls, escalation, approval compliance, latency, and optional cost only
when a versioned price schedule is supplied. Ambiguous scenarios are marked for
human review. Batch scoring reports failure categories and prioritized fixes; the
regression gate applies to the complete run and requires approval compliance.
Reviewer agreement rejects missing or mismatched scenario IDs.

## Boundaries and non-goals

The in-memory service is a replaceable reference implementation, not a durable
distributed lock or enterprise workflow engine. A production adapter must use a
transactional store/lease and an external idempotency record. No blanket approval,
unattended action, multi-agent behavior, frontend, observability stack, deployment,
or real consequential integration is introduced.
