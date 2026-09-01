from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class DependencyHealthResponse(BaseModel):
    name: str
    status: Literal["ok", "timeout", "unavailable"]
    latency_ms: float | None = None


class DependenciesResponse(BaseModel):
    status: Literal["ok", "degraded"]
    dependencies: list[DependencyHealthResponse]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report process health without checking future external dependencies."""
    return HealthResponse(status="ok")


@router.get("/health/dependencies", response_model=DependenciesResponse)
async def dependency_health(request: Request) -> DependenciesResponse:
    """Bounded content-free readiness signal for configured dependencies."""
    service = request.app.state.dependency_health
    status, dependencies = await service.check()
    return DependenciesResponse(
        status=status,
        dependencies=[
            DependencyHealthResponse(
                name=item.name, status=item.status, latency_ms=item.latency_ms
            )
            for item in dependencies
        ],
    )
