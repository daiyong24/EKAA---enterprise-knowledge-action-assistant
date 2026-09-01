# Stage 10 RAG Reliability Runbook

## Provider unavailable or circuit open

1. Check `GET /health/dependencies` and confirm only the provider is degraded.
2. Inspect sanitized logs for `llm_retry`, `rag_generation_retry`, or circuit
   state events. Never copy prompts, answers, keys, or provider payloads into logs.
3. Verify the provider endpoint and model configuration through the deployment's
   secret/configuration mechanism, not source control.
4. Allow the breaker recovery window to elapse and send one safe synthetic probe.
5. If failures continue, keep the dependency disabled and preserve the stable 503
   behavior; do not increase retries during an incident.

## Database or retrieval unavailable

1. Check the database entry in `/health/dependencies`.
2. Confirm connection-pool/database availability using the database operator's
   own tooling; do not log query text or retrieved document content.
3. Verify authorization metadata is available before retrying retrieval.
4. Restore the dependency, then run the existing retrieval and authorization
   integration tests before reopening traffic.

## Malformed output or elevated low-confidence answers

Malformed output is a contract error, not a retry storm: it maps to a stable 502.
Low-confidence evidence retains the existing insufficient-information response and
does not call the provider. Inspect model/provider contract changes and rerun the
Stage 8 synthetic evaluation before changing thresholds or prompts.

## Cancellation and retry-storm guard

Request cancellation must not increment breaker failures or schedule another
attempt. If request latency is high, measure provider and database phase timings
before changing deadlines. Keep exponential backoff, jitter, retry caps, and the
shared circuit breaker enabled. Stage 10 does not claim a production SLO without
load data.
