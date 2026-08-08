import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.app.core.exceptions import ApplicationError, register_exception_handlers


@pytest.mark.anyio
async def test_application_error_has_stable_safe_shape() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise ApplicationError(
            code="example_failure",
            message="The example operation failed.",
            status_code=409,
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/failure")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "example_failure",
            "message": "The example operation failed.",
        }
    }


@pytest.mark.anyio
async def test_unexpected_error_does_not_leak_internal_details() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise RuntimeError("sensitive internal detail")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/failure")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "An unexpected error occurred.",
        }
    }
    assert "sensitive internal detail" not in response.text
