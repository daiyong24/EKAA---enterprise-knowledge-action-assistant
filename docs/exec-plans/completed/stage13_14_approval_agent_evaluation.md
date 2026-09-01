# Stage 13/14 Approval and Agent Evaluation

## Objective

Implement exact scoped human approval for consequential proposals and a complete,
reproducible evaluator for bounded single-agent scenarios.

## Implementation

- Added approval domain models, safe audit sink, atomic decision service, expiry,
  exact parameter binding, idempotent executor boundary, and crash reconciliation.
- Added create/get/approve/reject API transport with safe public errors.
- Added strict scenario/trace contracts, per-trace and batch metrics, ambiguity and
  reviewer agreement checks, cost schedule handling, and regression gates.
- Added adversarial lifecycle and scoring tests plus design/ADR/report documents.

## Verification

Focused and full lint/type/test commands are run by the coordinator after review.
No commit or push performed. No external write executed.

## Risks and limitations

The in-memory approval store is not a distributed production store. Unknown
executions require manual reconciliation and are never auto-retried. The evaluator
does not claim population-level quality or cost when no price schedule is declared.
