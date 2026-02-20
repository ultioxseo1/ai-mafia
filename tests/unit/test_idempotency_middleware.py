"""Tests for the idempotency middleware."""

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from services.api_fastapi.middleware.idempotency import IdempotencyMiddleware


async def _echo(request: Request) -> JSONResponse:
    """Echo endpoint that returns the idempotency key from request state."""
    key = getattr(request.state, "idempotency_key", None)
    return JSONResponse({"idempotency_key": key})


def _build_app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/test", _echo, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
        ],
    )
    app.add_middleware(IdempotencyMiddleware)
    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(_build_app())


# --- Read-only methods pass through without header ---

@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_readonly_methods_pass_without_header(client: TestClient, method: str):
    resp = client.request(method, "/test")
    assert resp.status_code == 200


# --- State-changing methods rejected without header ---

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_state_changing_methods_rejected_without_header(client: TestClient, method: str):
    resp = client.request(method, "/test")
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "missing_idempotency_key"
    assert body["error"]["retriable"] is False


# --- State-changing methods pass with header and key is forwarded ---

@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_state_changing_methods_pass_with_header(client: TestClient, method: str):
    resp = client.request(method, "/test", headers={"Idempotency-Key": "abc-123"})
    assert resp.status_code == 200
    assert resp.json()["idempotency_key"] == "abc-123"


# --- Empty idempotency key is treated as missing ---

def test_empty_idempotency_key_rejected(client: TestClient):
    resp = client.post("/test", headers={"Idempotency-Key": ""})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "missing_idempotency_key"
