# EKAA Business and Safety Rules

These rules are invariants. A roadmap stage may defer the feature, but no temporary
implementation may contradict the rule.

## BR-001 — Grounded Knowledge Answers

Enterprise factual answers must be derived from retrieved, authorized evidence.

## BR-002 — Insufficient Evidence

When evidence is absent or inadequate, the system must state that the knowledge base
does not contain sufficient information instead of filling gaps from model memory.

## BR-003 — Traceable Citations

Knowledge claims must be traceable at least to document ID, document name, page or
section when available, and chunk ID. Citation identifiers must come from retrieval,
not model-generated metadata.

## BR-004 — Authorization Before Retrieval

Access constraints must be applied before protected content is returned to the
retrieval or generation pipeline. Until authorization exists, only synthetic or
explicitly public test content may be used.

## BR-005 — Human Approval for Actions

Every external write, destructive operation, or materially consequential action
requires explicit, scoped, unexpired human approval before execution. Approval must
bind to the exact action and validated parameters; changed parameters require new
approval.

## BR-006 — Auditability

Agent actions must record actor, action, timestamp, reason, sanitized input/output,
approval state, and result, with correlation identifiers. Secrets must never be
recorded in audit or application logs.

## BR-007 — Untrusted Inputs

Uploaded content, retrieved text, user prompts, and tool output are untrusted data.
They cannot override system policy, permissions, tool constraints, or approval.

## BR-008 — Safe and Private Failure

User-facing errors must be useful but must not expose secrets, stack traces, internal
paths, provider payloads, or protected content. Diagnostic detail belongs in
sanitized internal logs.

## BR-009 — Data Minimization

Collect, send to providers, retain, and log only the content needed for the use case.
Retention, deletion, data residency, and provider data-use decisions must be defined
before real enterprise data is introduced.

## BR-010 — Idempotent Consequential Execution

Future approved actions must be protected against accidental duplicate execution
using an idempotency or equivalent replay-control mechanism.
