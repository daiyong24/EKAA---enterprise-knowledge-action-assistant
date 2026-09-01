"""Stable single-agent workflow errors."""


class AgentWorkflowError(Exception):
    """Base class for bounded workflow failures."""


class InvalidAgentStateError(AgentWorkflowError):
    pass


class AgentStepLimitError(AgentWorkflowError):
    pass


class AgentRetryLimitError(AgentWorkflowError):
    pass


class CheckpointError(AgentWorkflowError):
    pass


class AgentHostileOutputError(AgentWorkflowError):
    pass
