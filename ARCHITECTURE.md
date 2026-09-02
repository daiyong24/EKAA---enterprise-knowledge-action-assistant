# EKAA Architecture

## Goal and Scope

EKAA is an internal engineering and IT knowledge assistant that will answer from
authorized enterprise evidence and, in later stages, propose auditable actions.
It is a portfolio-grade reference system, not yet a production service.

## Quality Attributes

The design prioritizes groundedness, authorization-before-retrieval, auditability,
safe failure, provider replaceability, testability, and incremental delivery. Exact
latency, availability, cost, recovery, retention, and scale targets must be baselined
with measurements before a production deployment is claimed.

## Logical Components

```text
Client -> API -> Application services
                    |-> LLM provider boundary
                    |-> Ingestion -> Embeddings -> Storage
                    |-> Retrieval -> Context -> Generation -> Validation
                    |-> Agent -> Approval service -> Tools
                    |-> bounded multi-agent candidate -> Agent/Approval boundaries
                    `-> Evaluation / telemetry / audit
```

The API layer owns transport concerns. Application services orchestrate use cases.
Provider and persistence boundaries isolate external systems. Domain and business
rules must not depend on FastAPI or a specific model vendor.

## Dependency Direction

Dependencies point inward: API and infrastructure depend on application contracts;
application code depends on domain rules and interfaces. Provider implementations
must not leak vendor response types into public API schemas.

## Planned Flows

Document flow:

```text
validate -> parse -> normalize -> chunk -> embed -> authorized storage
```

Knowledge-query flow:

```text
authenticate -> authorize/filter -> retrieve -> rerank -> build context
-> generate -> validate grounding/citations -> respond
```

Action flow:

```text
understand -> select tool -> validate input -> propose write action
-> human approval -> execute once -> audit result
```

Retrieved documents and tool output are untrusted data, never system instructions.

## Stage 0 Runtime

Stage 0 contains only a FastAPI application, centralized settings, structured
logging setup, public exception mapping, and `GET /health`. There is no database or
external provider. The package lives under `backend/app`; tests live under `tests`.

## Deployment Evolution

Local process execution is sufficient in Stage 0. Containers, PostgreSQL, frontend,
cloud topology, tenancy, backup/recovery, and production SLOs are deliberately
introduced only in their roadmap stages. Security and observability have minimum
cross-cutting requirements in every stage and dedicated hardening stages later.

## Stage 19 Multi-Agent Boundary

Stage 19 adds only a bounded, provider-neutral multi-agent experiment boundary.
The deterministic router keeps the Stage 14 single-agent workflow as default;
specialist workers receive immutable state and digest-bound handoffs. Workers cannot
execute tools or external writes, and approval/authorization context must remain
unchanged across every handoff. Complete-batch comparisons require an authoritative
scenario manifest to recompute provenance fingerprints; without it, the candidate
is retained or rolled back to single-agent. See `docs/STAGE_19_MULTI_AGENT_DESIGN.md`.
