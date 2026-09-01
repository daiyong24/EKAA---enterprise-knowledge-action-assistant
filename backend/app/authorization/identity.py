"""Replaceable identity-provider boundary with a safe local implementation."""

from collections.abc import Mapping
from typing import Protocol

from backend.app.authorization.errors import InvalidIdentityError
from backend.app.authorization.models import ANONYMOUS, Principal


class IdentityProvider(Protocol):
    """Verify request credentials without coupling the domain to an IAM vendor."""

    def resolve(self, headers: Mapping[str, str]) -> Principal: ...


class AnonymousIdentityProvider:
    """Local default: anonymous users can only see explicitly public documents."""

    def resolve(self, headers: Mapping[str, str]) -> Principal:
        del headers
        return ANONYMOUS


class StaticTokenIdentityProvider:
    """Test/local integration seam; tokens are injected, never stored in source."""

    def __init__(self, tokens: Mapping[str, Principal]) -> None:
        self._tokens = dict(tokens)

    def resolve(self, headers: Mapping[str, str]) -> Principal:
        authorization = headers.get("authorization", "")
        if not authorization:
            return ANONYMOUS
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or token not in self._tokens:
            raise InvalidIdentityError("identity credential was rejected")
        principal = self._tokens[token]
        try:
            principal.validate()
        except ValueError as exc:
            raise InvalidIdentityError("identity claims were rejected") from exc
        return principal
