# Stage 18 — CI/CD Release Contract

## Required promotion gates

Every artifact must pass quality, integration, evaluation, security, and image build
jobs. The release workflow promotes the exact image digest built from the exact commit;
it never rebuilds an image between staging and production. A failed, skipped, missing,
or stale gate blocks promotion. Production additionally requires the protected GitHub
environment approval.

The security job runs both `detect-secrets` and the fail-closed
`scripts/verify_secret_scan.py` comparison against the reviewed `.secrets.baseline`.
Any finding not explicitly reviewed as a non-secret fails the job.

Third-party GitHub Actions are pinned to reviewed immutable commit SHAs (checkout,
Python setup, Docker BuildKit, Trivy, artifact upload/download). The platform owner
owns monthly action update review: verify the upstream release/tag and security notes,
update the SHA plus version comment in both workflows, then rerun all gates.

The provider-neutral `backend.app.deployment.release` contract and
`tests/unit/test_deployment.py` prove these invariants without calling GitHub or AWS.

## Flow

```text
push/PR -> quality + integration + evaluation + security
        -> immutable image + provenance -> staging approval/deploy
        -> smoke/health/evaluation -> production approval/deploy
        -> rollback to previously attested digest
```

The release workflow is manually dispatched for deployment. Its concurrency group is
`ekaa-release-<target-environment>` for both promotion and rollback, with
`cancel-in-progress: false`; no two operations for the same environment can overlap.
Deploy steps are placeholders until a reviewed AWS adapter is selected; no workflow in this
repository deploys real infrastructure today.

## Artifact provenance and secrets

The image is tagged with the commit SHA and addressed by a registry digest. The image
job emits an immutable OCI artifact and a structured BuildKit provenance artifact
containing the exact digest and commit; the release job downloads and structurally
validates that artifact rather than trusting free-form text. Registry signature
verification remains an environment dependency. No secret is passed as a Docker build
argument. GitHub Actions permissions are read-only by default; environment secrets and
short-lived cloud credentials are available only to the deployment job after approval.

## Branch protection and rollback

The default branch must require the `quality`, `integration`, `evaluation`, `security`,
and `image` checks, require pull requests, dismiss stale approvals, and disallow force
pushes. Configure `staging` and `production` as protected environments with named
reviewers; production reviewers must not be the author where the GitHub policy supports
that control.

Rollback is a new promotion of the last known-good attested digest, not a mutable tag
or an ad-hoc rebuild. It uses the same five required gates and protected environment
approval as a normal promotion; arbitrary or never-promoted digests are rejected by
the release contract. Run the rollback drill in staging each release cycle and record
the digest, approval, health result, and audit/change identifier. If migration rollback
is unsafe, restore the compatible database backup first and escalate to the data owner.
