"""Provider-neutral release promotion contracts used by CI/CD.

This module deliberately does not deploy anything.  It makes the safety gates
testable without coupling application code to GitHub, AWS, or a deployment tool.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock
from types import MappingProxyType

REQUIRED_GATES = frozenset(
    {"quality", "integration", "evaluation", "security", "image"}
)
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PromotionError(ValueError):
    """Raised when an artifact cannot be promoted safely."""


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    """Strict schema for the immutable provenance artifact emitted by CI."""

    schema_version: int
    image_digest: str
    commit_sha: str
    builder: str
    attestation_type: str

    @classmethod
    def from_file(cls, path: str | Path) -> "ProvenanceRecord":
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PromotionError("provenance artifact is unreadable") from exc
        if not isinstance(payload, dict):
            raise PromotionError("provenance artifact must be an object")
        required = {
            "schema_version",
            "image_digest",
            "commit_sha",
            "builder",
            "attestation_type",
        }
        if set(payload) != required:
            raise PromotionError("provenance artifact schema is not exact")
        values = {key: payload[key] for key in required}
        schema_version = values["schema_version"]
        string_values = {key: values[key] for key in required - {"schema_version"}}
        if not isinstance(schema_version, int):
            raise PromotionError("provenance schema version is invalid")
        if any(not isinstance(value, str) for value in string_values.values()):
            raise PromotionError("provenance fields must be strings")
        return cls(
            schema_version=schema_version,
            image_digest=str(string_values["image_digest"]),
            commit_sha=str(string_values["commit_sha"]),
            builder=str(string_values["builder"]),
            attestation_type=str(string_values["attestation_type"]),
        )

    def validate(self) -> None:
        if self.schema_version != 1:
            raise PromotionError("unsupported provenance schema version")
        if not _SHA256_PATTERN.fullmatch(self.image_digest):
            raise PromotionError("provenance digest is invalid")
        if not _COMMIT_PATTERN.fullmatch(self.commit_sha):
            raise PromotionError("provenance commit is invalid")
        if self.builder != "buildkit":
            raise PromotionError("untrusted provenance builder")
        if self.attestation_type != "buildkit-provenance-v1":
            raise PromotionError("untrusted provenance attestation")


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    """Immutable build identity and provenance required for promotion."""

    digest: str
    commit_sha: str
    provenance: ProvenanceRecord

    def __post_init__(self) -> None:
        if not _SHA256_PATTERN.fullmatch(self.digest):
            raise PromotionError("artifact must have a valid sha256 digest")
        if not _COMMIT_PATTERN.fullmatch(self.commit_sha):
            raise PromotionError("artifact must identify a full commit SHA")
        self.provenance.validate()
        if (
            self.provenance.image_digest != self.digest
            or self.provenance.commit_sha != self.commit_sha
        ):
            raise PromotionError("artifact is not bound to exact provenance")


@dataclass(frozen=True, slots=True)
class PromotionRequest:
    """A request to promote one immutable artifact to one named environment."""

    artifact: ReleaseArtifact
    environment: str
    gates: Mapping[str, GateStatus]
    approved: bool = False

    def __post_init__(self) -> None:
        if self.environment not in {"staging", "production"}:
            raise PromotionError("unknown deployment environment")
        if set(self.gates) != REQUIRED_GATES:
            raise PromotionError(
                "release gates must be exactly quality, integration, evaluation, "
                "security, and image"
            )
        if any(not isinstance(status, GateStatus) for status in self.gates.values()):
            raise PromotionError("release gate statuses must be explicit")
        if any(status is not GateStatus.PASSED for status in self.gates.values()):
            raise PromotionError("all required release gates must pass")
        object.__setattr__(self, "gates", MappingProxyType(dict(self.gates)))


@dataclass(frozen=True, slots=True)
class RollbackRequest:
    """A rollback request that carries the same gates and approval boundary."""

    artifact: ReleaseArtifact
    environment: str
    gates: Mapping[str, GateStatus]
    approved: bool = False

    def __post_init__(self) -> None:
        request = PromotionRequest(
            artifact=self.artifact,
            environment=self.environment,
            gates=self.gates,
            approved=self.approved,
        )
        if self.environment == "production" and not request.approved:
            raise PromotionError("production rollback requires approval")
        object.__setattr__(self, "gates", request.gates)


class ReleaseController:
    """Small concurrency-safe controller for promotion and rollback intent.

    The actual deployment adapter belongs to infrastructure.  A controller can
    only record an approved immutable digest, so a failed gate or changed digest
    cannot be promoted through this boundary.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._promoted: dict[str, ReleaseArtifact] = {}
        self._known_good: dict[str, dict[str, ReleaseArtifact]] = {}

    def promote(self, request: PromotionRequest) -> ReleaseArtifact:
        if request.environment == "production" and not request.approved:
            raise PromotionError("production promotion requires approval")
        with self._lock:
            self._promoted[request.environment] = request.artifact
            self._known_good.setdefault(request.environment, {})[
                request.artifact.digest
            ] = request.artifact
            return request.artifact

    def rollback(self, request: RollbackRequest) -> ReleaseArtifact:
        with self._lock:
            known_good = self._known_good.get(request.environment, {}).get(
                request.artifact.digest
            )
            if known_good != request.artifact:
                raise PromotionError("rollback artifact was not previously promoted")
            self._promoted[request.environment] = request.artifact
            return request.artifact

    def current(self, environment: str) -> ReleaseArtifact | None:
        with self._lock:
            return self._promoted.get(environment)
