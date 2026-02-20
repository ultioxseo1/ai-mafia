"""Idempotency middleware for FastAPI.

Requires an ``Idempotency-Key`` header on every state-changing request
(POST, PUT, PATCH, DELETE).  Read-only methods (GET, HEAD, OPTIONS) pass
through without the header.  The key is stored on ``request.state.idempotency_key``
for downstream service methods to consume.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_MISSING_KEY_RESPONSE = {
    "error": {
        "code": "missing_idempotency_key",
        "message": "State-changing requests require an Idempotency-Key header.",
        "retriable": False,
    }
}


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Enforce ``Idempotency-Key`` header on state-changing HTTP methods."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if request.method in _STATE_CHANGING_METHODS:
            idempotency_key = request.headers.get("idempotency-key")
            if not idempotency_key:
                return JSONResponse(
                    status_code=400,
                    content=_MISSING_KEY_RESPONSE,
                )
            request.state.idempotency_key = idempotency_key
        return await call_next(request)
