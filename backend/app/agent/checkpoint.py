"""Replaceable checkpoint boundary for resumable agent runs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from backend.app.agent.models import AgentState


class CheckpointStore(Protocol):
    def save(self, state: AgentState) -> None: ...

    def load(self, run_id: str) -> AgentState | None: ...


class InMemoryCheckpointStore:
    def __init__(self) -> None:
        self._states: dict[str, AgentState] = {}

    def save(self, state: AgentState) -> None:
        self._states[state.run_id] = state.model_copy(deep=True)

    def load(self, run_id: str) -> AgentState | None:
        state = self._states.get(run_id)
        return state.model_copy(deep=True) if state is not None else None

    def snapshot(self) -> Mapping[str, AgentState]:
        return {key: value.model_copy(deep=True) for key, value in self._states.items()}
