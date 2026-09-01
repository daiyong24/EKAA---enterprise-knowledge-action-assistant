"""Safe, provider-neutral tool boundary errors."""


class ToolError(Exception):
    """Base class for failures at the tool boundary.

    Messages are for diagnostics only; callers should map these to stable public
    messages and never expose the underlying exception or tool output.
    """


class ToolNotFoundError(ToolError):
    pass


class ToolInputError(ToolError):
    pass


class ToolAuthorizationError(ToolError):
    pass


class ToolTimeoutError(ToolError):
    pass


class ToolUnavailableError(ToolError):
    pass


class ToolExecutionError(ToolError):
    pass


class WriteExecutionForbiddenError(ToolError):
    """Raised if code attempts to execute a proposal as a write."""


class ToolAuditError(ToolError):
    pass
