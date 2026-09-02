# ADR-013 — Reproducible Deployment and Gated Release Boundaries

## Status

Accepted for the combined Stage 17/18 local deployment and CI/CD contract.

## Decision

Use a non-root Python image and a small Docker Compose stack for reproducible local
execution. Select ALB + ECS Fargate + private RDS PostgreSQL/pgvector + Secrets Manager
as the AWS reference topology. Keep migrations, backups, and external deployment behind
explicit operator/release boundaries. Require immutable image digest, commit-bound
provenance, an exact set of quality/integration/evaluation/security/image gates, and
protected-environment approval for promotion and rollback. Use one non-canceling
concurrency group per target environment.

## Consequences

The local path is easy to reproduce and safety properties are testable without AWS.
The topology supports private networking and managed backups but introduces AWS cost and
platform ownership. Python dependencies and base images are pinned by the repository's
constraints and immutable digests; GitHub Actions are pinned to reviewed commit SHAs.
In-memory application state from earlier stages is not a substitute
for distributed production state; deployment must select durable stores before scaling.
The repository does not claim an applied AWS deployment, production SLO readiness, or a
deployed Grafana artifact.
