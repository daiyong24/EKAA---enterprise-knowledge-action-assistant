"""Safe errors from the identity and authorization boundary."""


class IdentityError(Exception):
    """The identity integration could not safely establish a principal."""


class InvalidIdentityError(IdentityError):
    """The supplied identity credential is invalid or malformed."""


class AuthorizationUnavailableError(Exception):
    """Authorization metadata could not be read; callers must fail closed."""


class AuthorizationDeniedError(Exception):
    """An authenticated caller lacks a required administrative permission."""


class InvalidDocumentPolicyError(Exception):
    """A document classification/ACL payload violates the policy contract."""


class DocumentNotFoundError(Exception):
    """The requested document is unavailable without revealing existence to callers."""
