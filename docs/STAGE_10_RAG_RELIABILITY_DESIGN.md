# Stage 10 — RAG Reliability Design

## Scope

Stage 10 adds system-level reliability around the existing provider, retrieval,
and RAG application boundaries. It does not change retrieval ranking, grounding,
authorization-before-retrieval, or provider payload contracts.

## Bounded behavior

The application configures separate 30-second end-to-end deadlines for chat and
RAG requests. Synchronous persistence retrieval runs in a worker boundary so a
cancelled request is no longer waiting on that work. PostgreSQL engines use bounded
five-second connection and pool-wait defaults (configurable); the persistence
operation itself can still outlive a cancelled worker because thread cancellation
cannot interrupt an already-running synchronous driver call. SQLite remains a
local development store without PostgreSQL pool semantics.

Transient provider failures (timeouts, transport/5xx/408 responses, and 429 rate
limits) receive at most the configured retry count. Delays use exponential backoff
with a bounded 20% multiplicative jitter window. A valid numeric `Retry-After` is
honored but capped by the backoff maximum. Permanent 4xx and malformed output are
never retried. Cancellation is never converted into a retry or outage signal.

One circuit breaker is shared by chat and RAG calls to the configured provider. It
opens after three consecutive availability failures, rejects calls during a
30-second cooldown, then permits one recovery probe. A successful probe closes the
circuit. Malformed output and permanent request rejection do not trip the breaker:
the dependency responded, but the request did not satisfy its contract.

## Degradation policy

| Failure | User-visible behavior | Data-safety behavior |
| --- | --- | --- |
| Provider outage, open circuit, or request deadline | Stable 503 unavailable error | No provider output is returned |
| Provider malformed output | Stable 502 invalid-response error | No malformed answer is returned |
| Provider permanent rejection | Stable 502 rejected-request error | No provider detail is returned |
| Retrieval/database outage | Stable 503 retrieval-unavailable error | No partial/unauthorized result is returned |
| Low-confidence or absent evidence | Existing insufficient-information answer | Provider is not called |
| Reranker timeout/failure | Existing retrieval order | No new source is introduced |

## Dependency health

`GET /health` remains a liveness check and does not call external systems.
`GET /health/dependencies` performs bounded, content-free checks for the database
and enabled provider. It returns `ok` only when every configured check succeeds;
otherwise it returns `degraded` with only dependency name, status, and optional
latency. Provider health uses a no-prompt `/models` probe.

These signals are readiness diagnostics, not production SLO evidence. Load,
capacity, pool sizing, retry budgets, and multi-instance breaker coordination are
not claimed by this stage.
