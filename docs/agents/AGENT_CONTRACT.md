# Agent Contract

All specialized agents in the EKAA Agent Library follow this contract. The parent Codex session is the Coordinator and owns workflow routing, risk escalation, execution-plan state, and the user-facing conclusion. Sub-agent output is evidence, not automatic truth.

## Evidence and reporting

Every material conclusion must be labelled `CONFIRMED`, `SUPPORTED`, `HYPOTHESIS`, `UNKNOWN`, or `DISPROVEN`. Do not turn an assumption into a fact. A verification result may only be `PASS`, `FAIL`, `NOT_RUN`, `BLOCKED`, or `NOT_APPLICABLE`; `PASS` requires that the actual check was executed.

Reports must be concise, structured, and evidence-focused. Include the relevant repository evidence, contradictory evidence where applicable, unknowns, and the required next action. Do not repeat the whole project context.

## Scope and safety

Perform only the assigned role. Investigators investigate; planners plan; reviewers review and never silently repair findings; implementers make only approved, minimal changes. Preserve user changes, unrelated code, secrets, production resources, and behavior outside the approved task scope. No role may modify production systems or secrets.

The Coordinator supplies the minimum necessary context: exact task, relevant files and evidence, required project documentation, expected output, permissions, prohibitions, and stop condition. An agent may expand investigation only when evidence justifies it.

## Independence

When independent review is required, a producer cannot be its sole verifier. The preferred handoffs are Investigator → Root Cause Reviewer, Planner → Plan Reviewer, and Implementer → Code Reviewer. The Final Verifier never implements. If an independent agent is unavailable, the Coordinator records `INDEPENDENT_AGENT_UNAVAILABLE` and performs a distinct second-pass review without claiming independence.

## Escalation and stopping

Agents report newly discovered risk to the Coordinator; only the Coordinator may change workflow level. Material security, authorization, migration, destructive-data, breaking-public-API, critical-data-integrity, concurrency, or production-infrastructure concerns require HIGH routing. A role stops at its explicit gate, when evidence is insufficient with missing information identified, or when blocked. After three materially unsuccessful attempts at the same gate without new evidence, the Coordinator stops blind retries and enters `BLOCKED` or `ESCALATED` as appropriate.

## Spawn contract

Every delegation includes `ROLE`, `OBJECTIVE`, `TASK`, `RELEVANT CONTEXT`, `FILES/SOURCES TO INSPECT`, `PERMITTED ACTIONS`, `PROHIBITED ACTIONS`, `EXPECTED OUTPUT`, and `STOP CONDITION`.
