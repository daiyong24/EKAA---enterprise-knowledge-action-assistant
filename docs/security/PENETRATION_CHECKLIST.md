# Penetration Checklist and Remediation Record

| ID | Test | Result | Evidence |
|---|---|---|---|
| SEC-001 | prompt/tool injection corpus cannot grant authority | PASS | `tests/unit/test_security.py` |
| SEC-002 | authorization boundary remains fail-closed | PASS | existing authorization tests |
| SEC-003 | path traversal and malicious filename rejection | PASS | `tests/unit/test_security.py` |
| SEC-004 | SSRF private/credentialed URL rejection | PASS | `tests/unit/test_security.py` |
| SEC-005 | oversized/control-character input rejection | PASS | existing ingestion/tool tests |
| SEC-006 | replay and approval parameter mutation | PASS | `tests/unit/test_approval.py` |
| SEC-007 | logs/telemetry redact secrets and content fields | PASS | `tests/unit/test_observability.py` |
| SEC-008 | dependency and secret scan hook exists | PASS | `.github/workflows/ci.yml` |

No critical/high finding is accepted in this reference slice. Remaining medium
risks are recorded in the threat model: shared durable rate limiting, DNS pinning,
vendor scanner integration, and production exporter access control.
