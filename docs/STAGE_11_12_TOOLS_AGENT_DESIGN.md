# Stage 11/12 — Tool Calling and Single-Agent Design

## Scope

This combined vertical slice introduces a typed tool boundary and a bounded,
provider-neutral single-agent workflow. It is intentionally local and uses only
synthetic/public data. No consequential operation is executed.

## Boundary

```text
authorized request
  -> ToolExecutionContext (authentication + upstream authorization proof)
  -> ToolRegistry (name lookup, policy, Pydantic schema validation, timeout)
  -> read-only integration OR in-process proposal builder
  -> bounded, sanitized audit event
```

The registry is callable without an agent, so tool execution does not depend on
an LLM's selection decision. Every tool is classified as `read_only` or
`propose_write`. A write-classified registration has no executable handler: the
registry builds a proposal internally from validated parameters. The resulting
object contains a stable proposal ID and cannot execute a write. Stage 13 owns
approval and future execution.

## Tool safety

Input models reject unknown fields, empty/bounded-invalid values, and control
characters. The optional public URL seam requires HTTPS, no credentials, and an
explicit host allowlist; private, loopback, link-local, multicast, reserved, and
unspecified literal addresses are rejected. The injected network adapter remains
responsible for DNS resolution and egress enforcement.

Tool outputs are untrusted, bounded data and are inserted into agent state with
an explicit marker. Audit events contain only bounded structural summaries and
redact secret-like keys and bearer values.

## Agent graph

The workflow has explicit `decide -> tool` edges and terminal edges for answer,
proposal/approval, termination, failure, and budget exhaustion. LangGraph was not
added: it is not an approved dependency in `pyproject.toml`, and the current
baseline needs a smaller auditable state machine. The `AgentPolicy` protocol keeps
the model boundary replaceable if a later decision requires a graph runtime.

Hard controls:

- maximum steps and decision retries;
- repeated tool-call fingerprint detection;
- checkpoint after every transition;
- resume only with matching run ID, correlation ID, user request, and principal
  authorization fingerprint; a fresh authorization proof is required on resume;
- no continuation after a proposal, completed, terminated, or failed terminal state;
- bounded messages and tool output;
- tool timeout and stable tool-error mapping.

The reproducible contract baseline is recorded in
`docs/evaluation/stage12_baseline_v1.json`. Its 3 tasks cover no-tool completion,
authorized read-tool completion, and proposal termination; the result is 3/3
successful contract outcomes, not a production quality claim.

Authorization is an upstream prerequisite represented by
`ToolExecutionContext.authorization_checked`; a false or missing proof blocks
both read and proposal tools. This does not replace Stage 9 authorization and
does not grant access.
