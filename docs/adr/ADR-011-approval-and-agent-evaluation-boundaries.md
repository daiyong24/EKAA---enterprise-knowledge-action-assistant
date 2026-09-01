# ADR-011: Explicit Approval and Reproducible Agent Evaluation Boundaries

## Status

Accepted for the Stage 13/14 local reference implementation.

## Decision

Use an explicit approval state machine with exact action/parameter digests,
authorized human transitions, idempotency keys, and an `execution_unknown` recovery
state. Use strict scenario/trace contracts and deterministic batch scoring for
single-agent evaluation. Keep consequential execution injected and disabled by
default.

## Rationale

These controls enforce BR-005 and BR-010 without allowing a model or a transport
caller to bypass approval. Keeping the evaluator independent of model provider
responses makes replay and regression results auditable. An unknown in-flight
execution cannot safely be retried because an external side effect may have
already happened.

## Consequences

The in-memory implementation is suitable for local tests only. A future durable
adapter must provide transactional compare-and-set, idempotency uniqueness, and
reconciliation ownership before any real integration is enabled. Cost metrics stay
unset unless a versioned provider price schedule is supplied.
