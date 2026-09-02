# Stage 17 — Deployment Contract

## Local Compose

The supported local deployment is Docker Compose with two services:

* `api`: the EKAA image, running as UID/GID `10001`, read-only filesystem, and
  `no-new-privileges`.
* `db`: the pinned `pgvector/pgvector` PostgreSQL 16 image with a named volume and
  a database health check.

The backend network is internal and the API is published only on loopback. Copy
`.env.docker.example` to an untracked `.env`, replace the local password, and run:

```powershell
Copy-Item .env.docker.example .env
docker compose config
docker compose up --build -d
Invoke-WebRequest http://127.0.0.1:8000/health
docker compose down
```

`EKAA_RUN_MIGRATIONS=true` runs `alembic upgrade head` before the API process. This is
only a development convenience. For staging and production, the release migration
job must run `alembic upgrade head` as a separately reviewed, one-off job, then start
the API with `EKAA_RUN_MIGRATIONS=false`; this prevents every replica from racing
migrations. `release_migration_job_enabled` and `app_startup_migrations_enabled` in
the deployment contract validate this distinction.
The image contains no `.env`, database file, evaluation artifact, or secret.

## Environment separation and secrets

Development, staging, and production must each have separate database, LLM, signing,
and telemetry credentials. Values are injected by the deployment environment, never
committed to Git, Docker build arguments, image layers, or logs. AWS deployments use
Secrets Manager/SSM references and task-role access; CI receives only short-lived
deployment credentials. The Compose password is intentionally local-only.

Production must use `EKAA_ENVIRONMENT=production`, `EKAA_DEBUG=false`, PostgreSQL,
HTTPS provider endpoints, and an explicit migration job. The application has no
production default credentials.

## TLS and network contract

The selected AWS topology terminates TLS at an Application Load Balancer using an ACM
certificate. The ALB forwards to private ECS tasks; security groups allow only ALB to
task traffic and task to RDS traffic. RDS is private, encrypted at rest, and has no
public ingress. `/health` is the ALB target check; `/health/dependencies` is an
operator/readiness check and must not be used as an unauthenticated public data API.

## Backup, restore, and recovery

RDS automated backups and point-in-time recovery are required in staging/production.
Backups must be encrypted, access-controlled, retained according to the approved
retention policy, and periodically restore-tested in an isolated database. The local
operator scripts `scripts/backup_postgres.sh` and `scripts/restore_postgres.sh` use
`EKAA_DATABASE_URL` supplied by the secret boundary, accept only safe basenames, reject
symlink sources/targets, and require explicit restore confirmation.

Recovery procedure:

1. Declare the incident and stop promotion/consequential writes.
2. Select a recovery point and record the incident/change identifier.
3. Restore to an isolated database; run migrations and health checks there.
4. Validate schema version, synthetic smoke checks, authorization boundary, and
   metrics before switching the connection secret.
5. Promote the restored database connection through the approved staged release.
6. Verify `/health`, dependency health, metrics, evaluation smoke, and audit records;
   document RPO/RTO evidence and any data loss.

A failed or ambiguous restore is not silently retried against the source database.
Escalate to the service owner and database owner.

## AWS topology, ownership, and planning cost

The selected smallest reasonable AWS topology is: ALB + ECS Fargate service in two
private subnets, RDS PostgreSQL with pgvector in two private subnets, Secrets Manager,
CloudWatch logs/metrics, and S3 for encrypted backup exports. Terraform/IaC is the
future infrastructure adapter; this stage records the topology and contracts and
does not apply AWS changes.

| Area | Owner | Planning estimate (low traffic) |
| --- | --- | --- |
| ECS Fargate API (1–2 tasks) | Application owner | USD 30–100/month |
| RDS PostgreSQL (small Multi-AZ-ready class) | Data owner | USD 50–180/month |
| ALB, logs, backups, S3, secrets | Platform owner | USD 20–80/month |
| **Total planning range** | Service owner | **USD 100–360/month** |

This is not a quote: region, task size, Multi-AZ, storage, transfer, retention, and
LLM/provider usage can materially change it. Platform owner must refresh the estimate
with the AWS pricing calculator before any budget or production approval.
