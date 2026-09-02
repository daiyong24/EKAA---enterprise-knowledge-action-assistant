# Stage 15/16 Runbook — Observability and Security

## Safe diagnosis

Search logs and traces by `x-correlation-id` / trace ID. Inspect route, status,
duration, and bounded failure category only. Never add request text, retrieved
chunks, prompts, embeddings, authorization headers, or provider payloads to a
debug statement. `/metrics` contains only bounded counters and duration summaries.

## Alerts and drills

Recommended alert contracts (thresholds must be baselined in deployment):

* `ekaa_http_requests_total{status=~"5.."}`: elevated server errors.
* `ekaa_http_request_duration_ms_count`: increased latency volume.
* exporter-failure log event: telemetry backend unavailable; application traffic
  remains usable and the exporter is repaired or replaced.
* rate-limit denials: abuse pattern; verify the keying policy and deployment state.

An alert drill records timestamp, correlation ID, signal observed, operator action,
and resolution. It must not copy protected content into the ticket.

## Incident response

1. Triage by correlation ID and bounded status/route metrics.
2. Verify provider/database health through existing content-free health checks.
3. If prompt injection or exfiltration is suspected, preserve only the sanitized
   finding category and deny the proposed action through existing policy gates.
4. Rotate any exposed credential out-of-band; do not place it in an incident note.
5. Add a remediation record with severity, owner, evidence reference, and test.

The local sinks are bounded reference implementations. Shared durable exporters,
alert routing, and distributed abuse controls are deployment responsibilities.
