# ADR-010: Provider-Neutral Explicit State Machine for Stage 12

Status: Accepted for Stage 12 baseline

## Context

The roadmap describes a bounded single-agent workflow with explicit state, nodes,
edges, limits, checkpoints, and termination rules. It names LangGraph as a planned
implementation, but the repository does not currently include LangGraph as an
approved dependency. Stage 12 must not expand dependency or trust boundaries merely
to satisfy an implementation label.

## Decision

Implement the Stage 12 baseline as the provider-neutral `SingleAgentWorkflow` with
explicit decision and tool transitions, terminal states, and a replaceable
`AgentPolicy` protocol. Checkpoints use a replaceable `CheckpointStore`. Tool calls
are made only through the Stage 11 typed registry after the Stage 9 authorization
boundary has supplied a fresh authorization proof. Proposal-classified actions stop
without an executable handler.

## Rationale and trade-offs

This keeps the current state graph small, deterministic, dependency-light, and easy
to inspect in code and tests. It makes retry/step/repetition budgets, principal
binding, recovery, hostile-output handling, and proposal termination explicit.
The trade-off is that the baseline does not provide LangGraph's graph visualization,
persistence adapters, or ecosystem integrations. Those capabilities are not needed
for the current single-agent contract and would add operational surface before
evidence justifies it.

## Migration boundary

LangGraph may be introduced only after a later decision documents a concrete need
and verifies feature parity against the existing contracts. The adapter must keep
the same `AgentState`, `AgentDecision`, `ToolRegistry`, checkpoint, hard-budget,
authorization, and proposal-only boundaries. It must pass the existing Stage 12
contract suite and prove that it does not add autonomous writes, bypass approval,
or weaken terminal/recovery guarantees. Multi-agent orchestration remains outside
this decision and belongs to Stage 19.
