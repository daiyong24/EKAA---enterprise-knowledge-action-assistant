# Security and Privacy Notes

- Demo inputs are synthetic and the validator rejects unexpected portfolio sources.
- Run artifacts are content-minimized; the portfolio commands do not print questions,
  document text, prompts, model responses, secrets, or provider payloads.
- Existing authorization is a pre-retrieval boundary; retrieved content and tool
  output are treated as untrusted data.
- Consequential execution remains behind exact approval and idempotency controls and
  is not invoked by the demo.
- The local validator performs a tracked-file and Git-history secret-path contract
  check. It is a
  guardrail, not a substitute for `detect-secrets`, `pip-audit`, penetration testing,
  or repository-host branch protection.
- No real external calls, real credentials, production data, registry pushes, cloud
  changes, or external writes are made by Stage 20 commands.

Before introducing enterprise data, define retention/deletion, residency, provider
data use, access review, backup, and incident response decisions. Before production,
repeat the security review in the target environment and obtain explicit risk
acceptance for any remaining findings.
