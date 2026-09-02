# EKAA Threat Model — Stage 16

| Asset | Boundary | Threat | Control | Status |
|---|---|---|---|---|
| credentials and tokens | request/log/telemetry | leakage | redaction, safe errors, secret-free tests | mitigated |
| authorized evidence | retrieval/prompt/telemetry | exfiltration or ACL bypass | pre-retrieval authorization, no-content telemetry | mitigated in reference scope |
| tool/action authority | agent to tool/approval | injection or approval bypass | typed validation, approval binding, replay controls | mitigated in reference scope |
| upload filesystem | HTTP to parser | traversal, bombs, oversized data | filename and size/page/character bounds | mitigated in reference scope |
| network boundary | URL reader to network | SSRF/DNS rebinding | HTTPS + host allowlist + public address check; re-check at client boundary | partial |
| service capacity | public request boundary | abuse/denial of service | request caps and replaceable rate limiter | partial |
| telemetry | app to exporter | sensitive content / cardinality attack | redaction, bounded labels/series, sampling | mitigated in reference scope |

Trust boundaries treat prompts, uploads, retrieved evidence, and tool output as
untrusted data. No classifier or model output changes identity, authorization, or
approval state. “Partial” controls require deployment-specific DNS pinning,
distributed state, WAF/rate limits, and durable monitoring before production use.
