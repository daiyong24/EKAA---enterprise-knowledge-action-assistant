# ADR-014 — Bounded Multi-Agent Candidate and Single-Agent Default

## Status

Accepted for a local Stage 19 experiment; multi-agent adoption deferred.

## Context

Stage 14 already provides a deterministic, bounded single-agent contract baseline.
Introducing multiple workers adds handoff, conflict, permission, latency, and
partial-failure surfaces. The roadmap requires measured benefit before adopting it.

## Decision

Implement a provider-neutral sequential candidate with a deterministic router,
immutable shared state, digest-bound handoffs, combined budgets, strict permission /
approval preservation, conflict detection, a read-only capability token, and
explicit single-agent fallback. Synchronous boundaries are deadline-bound through
worker threads and telemetry is bounded/non-blocking. Compare complete replicate
artifacts with unique underlying run identities and scenario fingerprints through
the Stage 14 scorer. The scorer must receive an authoritative scenario manifest
and recompute its fingerprint; a caller-only `dataset_fingerprint` is not trusted.
Without that manifest, keep single-agent as the production/default route unless
all declared comparison gates pass,
including non-regression of tool/argument accuracy and escalation correctness—the
Stage 14 failure surface.
Three replicates establish reproducibility only; adoption also requires a typed,
authoritative, independently verified `StatisticalEvidence` artifact bound to the
replicate IDs, scenario fingerprint, and observed effect. Missing or invalid
powered evidence retains single-agent. Synchronous adapters
must be explicitly trusted as side-effect-free because a thread timeout cannot stop
their underlying callable.

## Consequences

The candidate is deterministic and mockable and can expose whether specialization
helps without changing existing agent behavior. It cannot execute tools or external
writes. Process-local state and transport are intentionally reference boundaries;
durable persistence, distributed coordination, and repeated statistical experiments
remain deployment work.
