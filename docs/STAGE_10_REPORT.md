# Stage 10 Report

Goal: Degrade safely across provider, database, and malformed-output failures.

Current branch: Existing user branch; no commit or push performed.

Repository status: Stage 10 implementation is uncommitted. No production
deployment, protected-data operation, or external write was performed.

Files created: `backend/app/reliability/*`, Stage 10 reliability design, Stage 10
RAG reliability runbook, reliability tests, and database timeout tests.

Files modified: provider error/HTTP mapping, chat and RAG orchestration, application
wiring, settings, database engine construction, dependency health route, and related
provider/configuration/integration tests.

Implementation: Chat and RAG requests use separate bounded 30-second end-to-end
deadlines. Provider transient failures are retried within the configured attempt
cap using exponential backoff, a maximum delay, and bounded jitter. Numeric
`Retry-After` values from rate limits are honored up to the same cap; invalid,
non-finite, negative, or oversized values are safely ignored/capped. A shared
provider circuit breaker opens after three consecutive availability failures,
rejects calls during a 30-second recovery window, and allows one half-open probe.
Cancellation never becomes a retry or breaker failure.

Provider malformed output and permanent request errors are not retried. Retrieval
and database failures map to stable safe errors, low-confidence evidence retains
the existing insufficient-information behavior, and reranker failure retains the
stable retrieval order. Synchronous retrieval runs behind a cancellable worker
boundary. PostgreSQL engines use bounded five-second connection and pool-wait
defaults; SQLite remains a local development path.

`GET /health` remains a liveness endpoint. `GET /health/dependencies` performs
bounded, content-free database and enabled-provider checks and reports `ok` only
when all configured checks succeed. Provider health uses a no-prompt `/models`
probe.

Architecture decisions: Reliability is implemented as application/provider
boundaries and does not change retrieval ranking, citation validation, or
authorization-before-retrieval. Circuit breaking is limited to provider availability
failures; malformed output and permanent rejection indicate a responsive dependency
and do not trip the breaker. No tools, agents, chat memory, frontend, or Stage 11
implementation was introduced.

Tests: Fault injection covers provider outage, timeout, malformed JSON and invalid
UTF-8, low-confidence evidence, database failure, rate limiting, retry bounds and
jitter, circuit opening/recovery, retry-storm prevention, request deadlines,
explicit cancellation, slow retrieval, dependency health failures, and safe HTTP
degradation.

Test results: 294 passed, 7 skipped. The skipped tests are four disposable
PostgreSQL/pgvector tests and three Windows symbolic-link privilege tests. Ruff,
formatting, strict mypy, and `git diff --check` pass.

Manual verification: The liveness and dependency-health contracts were exercised
through ASGI integration tests. Provider health uses no prompt or user content.

Known limitations: Worker cancellation cannot interrupt an already-running
synchronous database driver call; PostgreSQL connection establishment and pool
acquisition are bounded, but a long-running SQL statement still requires database
server/driver statement-timeout configuration. The circuit breaker is process-local
and not coordinated across instances. No load test, capacity measurement, recovery
time objective, or production SLO claim is made. Health status is a readiness
diagnostic, not proof that a full RAG request will succeed.

Security review: Public errors contain stable codes/messages only. Logs retain only
error types and dependency names; prompts, answers, retrieved content, provider
payloads, credentials, and protected metadata are not logged. Authorization remains
before retrieval, and cancellation/degradation never returns partial provider or
retrieval output.

Regression review: Existing Stage 0–9 contracts remain compatible. Existing service
constructors retain their prior defaults for direct callers; Stage 10 policies are
activated through application settings. Retrieval ordering, grounding, citation
validation, and ACL filtering are unchanged.

Acceptance criteria: PASS. Provider, database, malformed-output, timeout, rate-limit,
retry, circuit, cancellation, recovery, and low-confidence failure paths have
bounded safe behavior, with user-visible degradation and content-free dependency
health reporting. This is not a production SLO or load-capacity claim.

Next stage: Stage 11 — Tool Calling, only after explicit user authorization.

No next-stage implementation performed.
