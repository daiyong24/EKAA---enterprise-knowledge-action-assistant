"""Deterministic route planning; single-agent remains the default."""

from __future__ import annotations

from backend.app.multi_agent.models import RoutePlan


class CandidateRouter:
    """Select a bounded route using only explicit task metadata."""

    def plan(
        self, *, requires_specialization: bool, enabled: bool = False
    ) -> RoutePlan:
        if not enabled or not requires_specialization:
            return RoutePlan(
                workers=("single-agent",), reason_code="single_agent_default"
            )
        return RoutePlan(
            workers=("researcher", "verifier", "synthesizer"),
            reason_code="specialized_candidate",
        )
