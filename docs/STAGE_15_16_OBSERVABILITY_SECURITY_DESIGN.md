# Stage 15/16 Design — Observability / LLMOps and Security Hardening

## Safe telemetry contract

Every HTTP request receives a validated caller correlation ID or a generated ID,
and the same ID is returned in `x-correlation-id` and `x-trace-id`. A request span
uses a stable trace ID, supports parent/child spans through context variables, and
exports only bounded redacted attributes. The `Tracer` protocol is compatible with
an OpenTelemetry adapter without making an SDK a runtime dependency.

`MetricRegistry` provides counters and observations rendered as Prometheus text.
Metric names and labels are bounded, label values are truncated, and the number of
series is capped. No user message, path query, document content, or provider
payload is a metric label. Sampling is deterministic per trace and exporter
failures are swallowed after a safe diagnostic log.

## Security baseline

The security seam provides an injection corpus classifier, safe filename/path
validation, an HTTPS allowlist/public-address SSRF policy, bounded in-memory rate
limiting, and a conservative telemetry redactor. Detection does not authorize an
action; existing authorization and approval boundaries remain authoritative.

The threat model and penetration checklist in `docs/security/` enumerate assets,
trust boundaries, abuse cases, tests, and remediation status. Secrets and real
enterprise content remain out of local test artifacts.

## Operational contracts

The `/metrics` endpoint is content-free and suitable for a local Prometheus scrape.
Alert rules and diagnostic response procedures are documented in
`docs/runbooks/STAGE_15_16_OBSERVABILITY_SECURITY.md`. A dashboard is intentionally
represented by query contracts rather than a vendor-specific Grafana export; the
queries can be installed once an operator selects a metrics backend.
