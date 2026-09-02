# ADR-012 — Provider-neutral observability and security boundaries

## Status

Accepted for the Stage 15/16 local reference slice.

## Decision

EKAA exposes correlation IDs, an OpenTelemetry-compatible span contract, and a
Prometheus text exporter through replaceable in-process seams. Telemetry is
content-free by default: prompts, retrieved documents, embeddings, provider
payloads, and secrets are redacted before export. Sampling and bounded series
limits are enforced at the telemetry boundary. Export failures are diagnostic
only and cannot change request outcomes.

Security controls remain application-boundary utilities: untrusted text is
classified but never promoted to instructions, filenames are path-component free,
network readers require HTTPS plus a host allowlist and public address policy, and
the local rate limiter is explicitly a replaceable abuse-control seam. Durable
telemetry, distributed rate limiting, DNS pinning, and organization-specific
security services are deployment decisions for later stages.

## Consequences

This adds no vendor SDK or network dependency and is straightforward to test with
fakes. The in-memory reference sinks are not durable or horizontally coordinated;
production deployment must supply bounded, access-controlled exporters and shared
rate-limit state before relying on operational SLOs.
