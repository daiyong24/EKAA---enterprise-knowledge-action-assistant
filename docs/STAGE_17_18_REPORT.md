# Stage 17/18 Report

## Goal

Provide a reproducible local deployment path and enforce traceable, gated, reversible
release controls without applying AWS infrastructure or using real credentials.

## Current branch

Working tree branch unchanged; no commit or push was performed.

## Repository status

Stage 17/18 changes are present in the working tree. Existing user changes were
preserved.

## Files created

* `Dockerfile` and `compose.yaml` use immutable base-image digests; `.dockerignore`,
  `.env.docker.example`, and `constraints.txt` provide artifact/config boundaries.
* `docker/entrypoint.sh`
* `scripts/backup_postgres.sh`, `scripts/restore_postgres.sh`,
  `scripts/verify_provenance.py`, `scripts/verify_promotion_history.py`,
  `scripts/verify_secret_scan.py`, `.secrets.baseline`
* `backend/app/deployment/config.py`, `backend/app/deployment/release.py`,
  `backend/app/deployment/secrets.py`
* `tests/unit/test_deployment.py`
* `.github/workflows/release.yml`
* `docs/deployment/STAGE_17_DEPLOYMENT.md`
* `docs/deployment/STAGE_18_CICD.md`
* `docs/adr/ADR-013-deployment-and-release-boundaries.md`
* `docs/exec-plans/completed/stage17_18_deployment_cicd.md`

## Files modified

* `.github/workflows/ci.yml`: reusable workflow plus integration, evaluation, security,
  immutable-image, BuildKit provenance, and Trivy vulnerability-scan gates. All
  third-party actions are pinned to reviewed commit SHAs.
* `AGENTS.md`, `ROADMAP.md`, `README.md`: current stage, status, and usage guidance.

## Implementation

Stage 17 supplies a Python 3.12 slim image running as UID/GID 10001, read-only
filesystem and `no-new-privileges` Compose settings, PostgreSQL/pgvector wiring,
health checks, explicit migration entrypoint, environment separation, secret
boundaries, backup/restore guardrails, and the selected AWS ALB/ECS Fargate/private
RDS topology with ownership and planning cost assumptions.

Stage 18 supplies the exact quality/integration/evaluation/security/image required gate
set, reusable CI invocation from the manual release workflow, immutable digest and
commit/provenance binding, protected staging/production environments, one shared
environment-scoped concurrency group for promotion and rollback, build provenance
metadata, Trivy vulnerability scanning, fail-closed secret-baseline comparison, and
explicit rollback-by-digest contracts.
The release workflow contains no AWS deployment adapter and therefore cannot perform
an external deploy.

## Architecture decisions

See `docs/adr/ADR-013-deployment-and-release-boundaries.md`. Infrastructure remains
replaceable and deployment is kept outside application route handlers.

## Tests

* Focused deployment tests: 7 passed.
* Full suite: 371 passed, 7 skipped.
* Ruff lint: passed.
* Ruff format check: passed.
* Mypy: passed.
* `git diff --check`: passed.
* `docker compose --env-file .env.docker.example config --quiet`: passed.

## Manual verification

Compose syntax and interpolation were validated. Docker image build and Compose smoke
run were not executed because the Docker CLI reported that the Docker daemon was not
running. AWS deployment, registry push, and real credential checks were intentionally
not attempted.

## Known limitations

The AWS topology is documented but not provisioned. Artifact provenance is a structured
BuildKit provenance artifact checked against exact digest and commit; cryptographic
registry signature verification and the deployment adapter remain environment
integration work. The CI image gate includes a Trivy HIGH/CRITICAL scan; local Windows
validation cannot run that container scan without a Docker daemon.
Release gates run in CI, while GitHub branch
protection and environment reviewers must be configured in the repository settings.
The application still has in-memory state from earlier reference stages; durable,
distributed stores are required before scaling beyond a single task. The planning cost
range is not an AWS quote and must be refreshed before approval.

## Security review

No real secrets, production data, AWS calls, registry pushes, or external writes were
used. Build context excludes `.env`, `.secrets`, local databases, and evaluation
artifacts. Production validation requires HTTPS, private networking, explicit
migrations, protected environment approval, and a secret manager.

## Regression review

The existing 363-test Stage 15/16 baseline remained green; the combined suite now
reports 371 passed with seven environment-dependent skips. No frontend, chat memory,
multi-agent architecture, or unattended consequential write was added.

## Acceptance criteria

**PASS for the local reference contract; AWS-dependent checks are explicitly deferred.**

The local image/Compose, health, configuration, backup/restore, release-gate,
artifact-identity/provenance, approval, concurrency, vulnerability-scan, and rollback
contracts are implemented and tested. Staging AWS deployment, registry attestation,
backup restore drill,
and branch-protection settings require an authorized environment and are documented as
follow-up verification, not claimed as completed here.

## Next stage

Stage 19 — Multi-Agent Architecture.

No next-stage implementation performed.
