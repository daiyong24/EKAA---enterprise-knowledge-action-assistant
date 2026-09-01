import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.core.config import Settings
from backend.app.main import create_app


@pytest.mark.anyio
async def test_health_returns_ok() -> None:
    app = create_app(Settings(environment="test", log_level="CRITICAL", _env_file=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.anyio
async def test_dependency_health_returns_bounded_content_free_status() -> None:
    app = create_app(Settings(environment="test", log_level="CRITICAL", _env_file=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/dependencies")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["dependencies"][0]["name"] == "database"
    assert payload["dependencies"][0]["status"] == "ok"
    assert "content" not in str(payload)
