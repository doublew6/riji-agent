"""Bound request size and avoid echoing private input in validation responses."""

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute


class PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 2_000_000:
                    raise HTTPException(413, "mentor_request_too_large")
                chunks.append(chunk)
            request._body = b"".join(chunks)
            try:
                response = await original(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except RequestValidationError:
                raise HTTPException(422, "mentor_request_invalid") from None
        return handle
