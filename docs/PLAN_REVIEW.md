# Starting Plan Review

## Verdict

The starting plan has a strong learning progression, sensible stage isolation, and
good core AI safety instincts. It is suitable as a project charter after the
clarifications below. `ROADMAP.md` is the executable source of truth; the original
download remains background material.

## Improvements Applied

1. **Removed stage-number ambiguity.** The first implementation phase is called
   Stage 0; “Stage 1” always means LLM Foundation.
2. **Added product boundaries.** The first user is an internal Engineering/IT user;
   the project uses synthetic/public knowledge until retrieval authorization exists.
3. **Shifted cross-cutting concerns left.** Safe errors, secret hygiene, structured
   logs, dependency review, and security review apply from Stage 0. Their later
   stages are hardening and measurement work, not first introduction.
4. **Resolved persistence ambiguity.** Stage 2 introduces PostgreSQL document and
   chunk metadata plus migrations; Stage 3 adds pgvector and embeddings.
5. **Resolved citation sequencing.** Stage 4 must return retrieval-derived source
   references to satisfy grounding rules. Stage 5 adds claim-level presentation and
   citation validation rather than introducing traceability for the first time.
6. **Clarified reliability overlap.** Stage 1 adds provider-local timeout, bounded
   retry, and error mapping. Stage 10 adds system-level fallback, circuit breaking,
   degradation, and failure testing.
7. **Made evaluation incremental.** Deterministic fixtures and quality assertions
   begin with each feature; Stage 8 builds the formal golden dataset and comparative
   metrics.
8. **Kept CI and deployment milestones coherent.** Basic CI starts in Stage 0;
   Stage 18 hardens gates and release flow. Containers remain deferred until they
   support integration or deployment rather than existing as unused scaffolding.
9. **Added missing operational decisions.** Before production claims, define SLOs,
   scale assumptions, cost budgets, retention/deletion, backup/recovery, data
   residency, provider data use, and tenancy boundaries.
10. **Strengthened action safety.** Approval binds to exact parameters, and
    consequential operations need replay/idempotency protection.

## Gaps to Decide Later

- Target organization size, document count/size, query concurrency, and languages.
- Single-tenant versus multi-tenant architecture.
- Initial LLM/embedding provider based on budget, region, privacy, and evaluation.
- Conversation retention and whether chat history may be used as retrieval context.
- Production SLOs and cost-per-query budget after a measurable baseline exists.
- AWS region, network boundary, secrets manager, backup, and disaster recovery plan.
- License, demo-data policy, and threat model for public portfolio release.

## Roadmap Shape

Twenty-one gates are useful for learning and review but create coordination overhead.
They are retained for traceability. Closely related stages may later share a release
branch, but acceptance and reports must remain separate so quality changes can be
measured independently.
