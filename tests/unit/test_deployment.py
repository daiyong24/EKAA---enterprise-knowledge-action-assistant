import json
from pathlib import Path

import pytest

from backend.app.deployment.config import DeploymentConfig
from backend.app.deployment.release import (
    GateStatus,
    PromotionError,
    PromotionRequest,
    ProvenanceRecord,
    ReleaseArtifact,
    ReleaseController,
    RollbackRequest,
)
from backend.app.deployment.secrets import verify_secret_scan

ROOT = Path(__file__).parents[2]


def artifact(seed: str = "a") -> ReleaseArtifact:
    digest = "sha256:" + seed * 64
    commit_sha = "a" * 40
    return ReleaseArtifact(
        digest=digest,
        commit_sha=commit_sha,
        provenance=ProvenanceRecord(
            schema_version=1,
            image_digest=digest,
            commit_sha=commit_sha,
            builder="buildkit",
            attestation_type="buildkit-provenance-v1",
        ),
    )


def passed_request(environment: str = "staging") -> PromotionRequest:
    return PromotionRequest(
        artifact=artifact(),
        environment=environment,
        gates={
            "quality": GateStatus.PASSED,
            "integration": GateStatus.PASSED,
            "evaluation": GateStatus.PASSED,
            "security": GateStatus.PASSED,
            "image": GateStatus.PASSED,
        },
        approved=environment != "production",
    )


def test_release_requires_all_explicitly_passed_gates(tmp_path: Path) -> None:
    with pytest.raises(PromotionError):
        PromotionRequest(
            artifact=artifact(),
            environment="staging",
            gates={"quality": GateStatus.FAILED},
        )

    with pytest.raises(PromotionError, match="exact provenance"):
        ReleaseArtifact(
            digest="sha256:" + "a" * 64,
            commit_sha="a" * 40,
            provenance=ProvenanceRecord(
                schema_version=1,
                image_digest="sha256:" + "a" * 64,
                commit_sha="b" * 40,
                builder="buildkit",
                attestation_type="buildkit-provenance-v1",
            ),
        )
    with pytest.raises(PromotionError, match="exactly"):
        PromotionRequest(
            artifact=artifact(),
            environment="staging",
            gates={
                "quality": GateStatus.PASSED,
                "integration": GateStatus.PASSED,
                "evaluation": GateStatus.PASSED,
                "security": GateStatus.PASSED,
            },
        )

    record_path = tmp_path / "provenance.json"
    record_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "image_digest": "sha256:" + "a" * 64,
                "commit_sha": "a" * 40,
                "builder": "buildkit",
                "attestation_type": "buildkit-provenance-v1",
            }
        ),
        encoding="utf-8",
    )
    assert ProvenanceRecord.from_file(record_path).image_digest == "sha256:" + "a" * 64
    with pytest.raises(PromotionError, match="exactly"):
        PromotionRequest(
            artifact=artifact(),
            environment="staging",
            gates={
                "quality": GateStatus.PASSED,
                "integration": GateStatus.PASSED,
                "evaluation": GateStatus.PASSED,
                "security": GateStatus.PASSED,
                "image": GateStatus.PASSED,
                "stale": GateStatus.PASSED,
            },
        )


def test_production_requires_protected_environment_approval() -> None:
    request = passed_request("production")
    with pytest.raises(PromotionError):
        ReleaseController().promote(request)

    approved = PromotionRequest(
        artifact=request.artifact,
        environment="production",
        gates=request.gates,
        approved=True,
    )
    assert ReleaseController().promote(approved) == request.artifact


def test_promotion_is_digest_immutable_and_rollback_is_explicit() -> None:
    controller = ReleaseController()
    controller.promote(passed_request())
    second = PromotionRequest(
        artifact=artifact("b"),
        environment="staging",
        gates={
            name: GateStatus.PASSED
            for name in ("quality", "integration", "evaluation", "security", "image")
        },
    )
    controller.promote(second)
    with pytest.raises(PromotionError, match="previously promoted"):
        controller.rollback(
            RollbackRequest(
                artifact=artifact("c"),
                environment="staging",
                gates=second.gates,
            )
        )
    restored = controller.rollback(
        RollbackRequest(
            artifact=artifact(),
            environment="staging",
            gates=second.gates,
        )
    )
    assert restored.digest == "sha256:" + "a" * 64


def test_production_rollback_requires_approval() -> None:
    controller = ReleaseController()
    request = passed_request("production")
    controller.promote(
        PromotionRequest(
            artifact=request.artifact,
            environment="production",
            gates=request.gates,
            approved=True,
        )
    )
    with pytest.raises(PromotionError, match="rollback requires approval"):
        controller.rollback(
            RollbackRequest(
                artifact=request.artifact,
                environment="production",
                gates=request.gates,
            )
        )


def test_production_deployment_config_requires_safe_defaults() -> None:
    DeploymentConfig(
        environment="production",
        database_url="postgresql+psycopg://db",
        debug=False,
        release_migration_job_enabled=True,
        app_startup_migrations_enabled=False,
        tls_termination="load_balancer",
    ).validate()
    with pytest.raises(ValueError):
        DeploymentConfig(
            environment="production",
            database_url="sqlite:///bad.db",
            debug=False,
            release_migration_job_enabled=True,
            app_startup_migrations_enabled=False,
            tls_termination="load_balancer",
        ).validate()
    with pytest.raises(ValueError, match="app-startup"):
        DeploymentConfig(
            environment="production",
            database_url="postgresql+psycopg://db",
            debug=False,
            release_migration_job_enabled=True,
            app_startup_migrations_enabled=True,
            tls_termination="load_balancer",
        ).validate()
    with pytest.raises(ValueError, match="explicit migration"):
        DeploymentConfig(
            environment="production",
            database_url="postgresql+psycopg://db",
            debug=False,
            release_migration_job_enabled=False,
            app_startup_migrations_enabled=False,
            tls_termination="load_balancer",
        ).validate()


def test_docker_image_and_compose_contracts_are_non_root_and_health_checked(
    tmp_path: Path,
) -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    compose = (ROOT / "compose.yaml").read_text()
    assert "USER 10001:10001" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "read_only: true" in compose
    assert "no-new-privileges:true" in compose
    assert "condition: service_healthy" in compose
    assert "POSTGRES_PASSWORD:?" in compose
    assert ".env" in (ROOT / ".dockerignore").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    assert "*.dump" in dockerignore and "*.sql" in dockerignore

    backup = (ROOT / "scripts/backup_postgres.sh").read_text()
    restore = (ROOT / "scripts/restore_postgres.sh").read_text()
    assert '-L "$output"' in backup
    assert '-L "$backup"' in restore
    assert "safe basename" in backup and "safe basename" in restore
    assert "EKAA_CONFIRM_RESTORE" in restore

    finding = {"type": "Basic Auth", "hashed_secret": "hash-1", "line_number": 1}
    scan = tmp_path / "scan.json"
    baseline = tmp_path / "baseline.json"
    scan.write_text(json.dumps({"results": {"README.md": [finding]}}))
    baseline.write_text(
        json.dumps({"results": {"README.md": [{**finding, "is_secret": False}]}})
    )
    verify_secret_scan(scan, baseline)
    baseline.write_text('{"results":{}}')
    with pytest.raises(ValueError, match="unapproved"):
        verify_secret_scan(scan, baseline)


def test_ci_release_cannot_skip_required_gates() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "quality:" in ci and "integration:" in ci
    assert "evaluation:" in ci and "security:" in ci and "image:" in ci
    assert "needs: [quality, integration, evaluation, security]" in ci
    assert 'pip install -c constraints.txt -e ".[dev]"' in ci
    assert "trivy-action" in ci
    assert "provenance: mode=max" in ci
    assert "steps.build.outputs.digest" in ci
    assert "outputs: type=oci,dest=/tmp/ekaa-image.tar" in ci
    assert "input: /tmp/ekaa-image.tar" in ci
    assert "docker save" not in ci
    assert "download-artifact" in release
    assert "target_environment" in release
    assert "cancel-in-progress: false" in release
    assert "uses: ./.github/workflows/ci.yml" in release
    assert "needs: gates" in release
    assert "environment:" in release
