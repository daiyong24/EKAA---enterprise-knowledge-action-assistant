"""Provider-neutral identity, resource policy, and audit values."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

DocumentClassification = Literal["public", "internal", "confidential", "restricted"]
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")


@dataclass(frozen=True)
class Principal:
    """An authenticated subject supplied by an identity integration boundary."""

    subject: str | None
    roles: frozenset[str] = frozenset()
    groups: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Reject claims that could be ambiguous or unsafe in policy/audit records."""
        if self.subject is not None and not _SAFE_ID.fullmatch(self.subject):
            raise ValueError("principal subject must be a bounded safe identifier")
        for label, values in (("role", self.roles), ("group", self.groups)):
            if not isinstance(values, frozenset):
                raise ValueError(f"principal {label}s must be immutable sets")
            if any(not _SAFE_ID.fullmatch(value) for value in values):
                raise ValueError(f"principal {label} must be a bounded safe identifier")

    @property
    def authenticated(self) -> bool:
        return self.subject is not None

    @property
    def is_admin(self) -> bool:
        return bool({"admin", "knowledge_admin"} & self.roles)


ANONYMOUS = Principal(subject=None)


@dataclass(frozen=True)
class AuthorizationAuditEvent:
    """Sanitized audit record; request content and protected content are excluded."""

    action: str
    actor: str
    outcome: Literal["allow", "deny", "error"]
    correlation_id: str
    timestamp: datetime
    resource_id: str | None = None
    reason_code: str | None = None

    @classmethod
    def now(
        cls,
        *,
        action: str,
        actor: str,
        outcome: Literal["allow", "deny", "error"],
        correlation_id: str,
        resource_id: str | None = None,
        reason_code: str | None = None,
    ) -> "AuthorizationAuditEvent":
        return cls(
            action=action,
            actor=actor,
            outcome=outcome,
            correlation_id=correlation_id,
            timestamp=datetime.now(UTC),
            resource_id=resource_id,
            reason_code=reason_code,
        )
