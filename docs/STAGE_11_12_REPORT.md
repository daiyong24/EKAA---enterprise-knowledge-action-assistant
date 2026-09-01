# Stage 11/12 Report

Goal: Introduce typed policy-classified tools and a bounded single-agent baseline.

Current branch: Existing user branch; no commit or push performed. Stage 10 is
complete; combined Stage 11/12 is complete and is the current promoted baseline.

Repository status: Uncommitted implementation; unrelated user changes preserved.

Files created: `backend/app/tools/*`, `backend/app/agent/*`, focused tool and
workflow tests, `docs/evaluation/stage12_baseline_v1.json`,
`docs/STAGE_11_12_TOOLS_AGENT_DESIGN.md`,
`docs/adr/ADR-010-provider-neutral-agent-state-machine.md`,
`docs/exec-plans/completed/stage11_12_tools_single_agent.md`, and this report.

Files modified: `AGENTS.md`, `README.md`, and `ROADMAP.md` active-stage markers.

Implementation: The tool registry validates schemas, requires upstream
authorization proof, applies per-tool timeouts, records structural-only audit
summaries, and
provides read-only knowledge/allowlisted URL seams plus a proposal-only write
boundary. The single-agent workflow uses explicit state, decision, tool, terminal,
and checkpoint transitions with hard step/retry/repetition/message budgets. Tool
output is bounded and explicitly untrusted; proposal results stop at
`awaiting_approval`. Checkpoints bind the request to the principal's subject,
roles, and groups fingerprint and require fresh authorization on resume.

Architecture decisions: A small provider-neutral state machine is used instead of
LangGraph because no LangGraph dependency is approved or present. The registry is
selection-independent. Stage 13 approval and idempotent consequential execution
remain out of scope.

Tests: Tool selection-independent execution, schema rejection, authorization gate,
SSRF rejection, deterministic proposal IDs, timeout, secret redaction, no-tool and
tool paths, proposal stop, loop prevention, retry/step budgets, checkpoint matching,
and hostile output handling. The deterministic Stage 12 baseline suite records 3/3
contract tasks successful (1.0 success rate) and is reproducible with the documented
pytest command.

Test results: Focused Stage 11/12 tests: 31 passed. The baseline artifact is
validated by actually instantiating and executing all three deterministic tasks,
then deriving the recorded success count and rate from those results. Full suite:
325 passed and 7 environment-dependent tests skipped. Ruff, format, and strict
mypy passed; diff check passed.

Manual verification: No external integration, network request, write, approval, or
production operation was executed.

Known limitations: The network callback must enforce DNS rebinding and egress
controls. The authorization proof is supplied by the Stage 9 caller and is not an
identity provider. This is a local baseline, not a production agent or SLO claim.

Security review: Fail-closed authorization gate, schema strictness, recursive input
bounds, URL allowlist, bounded output/state, structural-only secret-safe audit,
principal-bound checkpoints, fresh resume authorization, untrusted-output marker,
and no-handler proposal-only write enforcement are covered by tests. Fingerprint
budget exhaustion terminates and checkpoints safely, while malformed agent decision
payloads are converted to checkpointed terminal failure states.

Regression review: Retrieval, RAG generation, authorization implementation, and
Stage 10 reliability behavior were not changed.

Acceptance criteria: PASS

Next stage: Stage 13 — Human-in-the-loop.

No next-stage implementation performed.
