"""Safe errors for the bounded multi-agent reference boundary."""

from __future__ import annotations


class MultiAgentError(Exception):
    """Base error whose public message is safe and content-free."""


class HandoffError(MultiAgentError):
    """A handoff was missing, stale, tampered with, or out of order."""


class AgentLoopError(MultiAgentError):
    """The route attempted a repeated worker or exceeded its handoff budget."""


class AgentConflictError(MultiAgentError):
    """Workers produced incompatible claims."""


class PermissionBoundaryError(MultiAgentError):
    """A worker attempted to cross the run's permission or approval boundary."""


class CombinedBudgetError(MultiAgentError):
    """The aggregate run budget was exhausted."""


class PartialFailureError(MultiAgentError):
    """A worker failed and no safe fallback was available."""
