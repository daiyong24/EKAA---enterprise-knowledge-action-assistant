# EKAA Agent Library

The Agent Library is a project-scoped Codex role library for the Adaptive Verified Bug-Fix Workflow. The parent Codex session is the Coordinator; it selects only the roles needed, validates their findings, owns risk escalation and durable execution-plan state, and never recursively delegates a new Coordinator.

## Roles

| Role | Purpose | Logical permission | Typical workflow use |
|---|---|---|---|
| `investigator` | Establish evidence and a root-cause candidate | Read-only | LOW/MEDIUM/HIGH |
| `root-cause-reviewer` | Falsify the candidate root cause | Read-only | MEDIUM/HIGH |
| `planner` | Produce the smallest safe plan | Read-only | MEDIUM/HIGH |
| `plan-reviewer` | Challenge the plan | Read-only | HIGH; conditional MEDIUM |
| `implementer` | Apply an approved minimal plan | Workspace-write | LOW/MEDIUM/HIGH |
| `code-reviewer` | Independently review the actual diff | Read-only | LOW/MEDIUM/HIGH |
| `architecture-reviewer` | Check `ARCHITECTURE.md` conformance | Read-only | HIGH; when structure is affected |
| `business-reviewer` | Check `BUSINESS_RULES.md` conformance | Read-only | HIGH; when behavior is affected |
| `final-verifier` | Verify original defect and completion gates | Read-only plus safe commands | LOW/MEDIUM/HIGH |

Specialists are conditional, read-only reviewers: `security-reviewer` for identity/access/secrets/security boundaries; `database-reviewer` for schema, migration, persistence, or data integrity; `api-reviewer` for material API contracts; and `concurrency-reviewer` for races, retries, ordering, or duplicate processing. Do not run all specialists by default.

## Workflow routing

`LOW`: Coordinator investigation and lightweight root-cause confirmation → Implementer → Code Reviewer → Final Verifier. Avoid unnecessary agents.

`MEDIUM`: Investigator → Root Cause Reviewer → Planner → Implementer → Code Reviewer → Architecture/Business review when relevant → Final Verifier. Use Plan Reviewer if uncertainty, architecture or business impact, or significant regression surface remains.

`HIGH`: Investigator → Root Cause Reviewer → Planner → Plan Reviewer → Implementer → Code Reviewer → Architecture Reviewer and Business Reviewer → triggered specialist reviewers → Final Verifier. HIGH prioritizes reliability and requires independent root-cause and plan review where agents are available.

Risk only escalates (`LOW → MEDIUM → HIGH`) on evidence. The Coordinator routes failures back to the earliest invalid assumption: investigation for rejected root cause, planning for rejected plan or material deviation, implementation for local defects, and blocked/escalated for unavailable access or required human product decisions.

## Durable state and recovery

Create MEDIUM/HIGH plans in `docs/exec-plans/active/` and move completed plans to `docs/exec-plans/completed/`. The Coordinator records the issue, risk, baseline, evidence and hypotheses, reviews, implementation deviations, verification, decisions, and residual risks. A later session reads `AGENTS.md`, the active plan, Git status/diff, and current revision before resuming from the earliest unverified gate.

## Runtime configuration

Native role definitions live in `.codex/agents/` and are registered by `.codex/config.toml`. The supported role-file fields are `name`, `description`, and `developer_instructions`. The current runtime has no supported per-role sandbox field; therefore read/write limits are explicit role instructions and the Coordinator must spawn review roles under a read-only session/permission profile whenever the host supports it. See `docs/workflows/ADAPTIVE_VERIFIED_BUG_FIX.md` for the workflow and `AGENT_CONTRACT.md` for the shared rules.

The installed Codex desktop package exposes the native agent-role parser, including its required role fields and project `agents.<role>.config_file` registrations. Its packaged CLI executable is not directly executable from this WindowsApps installation, so direct load/discovery was not runnable from this setup session. The library is statically validated against that parser's required fields and every registration path; open a new Codex task in this workspace to exercise runtime discovery. This is a validation-environment limitation, not an unsupported configuration fallback.

## Maintaining the library

Add a persistent role only when a repeated responsibility needs distinct instructions, independent context, a different permission profile, or a meaningful verification domain. Register it in `.codex/config.toml`, give it a stable functional name, an explicit output and stop condition, and document its workflow purpose here. Change an existing role only after checking its workflow gates, role boundaries, and native schema; do not introduce unsupported TOML keys.
