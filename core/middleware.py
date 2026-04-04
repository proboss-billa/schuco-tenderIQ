"""Request-scoped middleware for FastAPI."""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from core.logging import request_id_var


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign a short unique request ID to every incoming HTTP request.

    * Sets ``request_id_var`` so all downstream log lines include it.
    * Adds ``X-Request-ID`` response header for client-side correlation.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())[:8]
        request_id_var.set(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
