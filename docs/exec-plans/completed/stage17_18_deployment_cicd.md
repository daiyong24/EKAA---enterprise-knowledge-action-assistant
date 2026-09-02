# Stage 17/18 Execution Plan

## Goal

Make local deployment reproducible and establish enforced, traceable, reversible
promotion controls without deploying AWS infrastructure or using real secrets.

## Steps

1. Read architecture, business rules, and stage acceptance criteria.
2. Add non-root Docker/Compose runtime, health checks, migrations, environment and
   secret boundaries, backup/restore scripts, and AWS topology contract.
3. Add release-domain contracts, CI quality/security/integration/evaluation gates,
   immutable artifact/provenance flow, staged approvals, concurrency, and rollback
   documentation.
4. Add focused static/policy tests and run local checks.
5. Review scope, security, rollback, and acceptance evidence; fix findings.

## Review-fix pass

The follow-up review required exact gate names, structured provenance artifact
verification, known-good promotion history for rollback, fail-closed secret-baseline
comparison, safe-basename/symlink backup handling, immutable image/action pins, and a
clear release-migration versus app-startup-migration split. These were implemented and
covered by focused tests and workflow contracts.

## Scope exclusions

No AWS apply, registry push, real credentials, production data, frontend, chat memory,
multi-agent architecture, or unattended consequential write is included.
