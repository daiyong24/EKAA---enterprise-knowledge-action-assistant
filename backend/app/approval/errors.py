"""Safe, provider-neutral approval errors."""


class ApprovalError(Exception):
    """Base class for expected approval boundary failures."""


class ApprovalNotFoundError(ApprovalError):
    pass


class ApprovalDeniedError(ApprovalError):
    pass


class ApprovalExpiredError(ApprovalError):
    pass


class ApprovalConflictError(ApprovalError):
    pass


class ApprovalStateError(ApprovalError):
    pass


class ExecutionUnknownError(ApprovalError):
    pass
