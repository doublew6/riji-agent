"""Owner-only account linking routes; transport tokens cannot approve links."""

from fastapi import Request, Response
from pydantic import Field

from riji_agent.mentors.models import Record


class LinkRequest(Record):
    application_id: str = Field(min_length=1, max_length=300)


class LinkConfirmation(Record):
    code: str = Field(min_length=1, max_length=100)


def attach_identity_routes(router, runtime, owned, safe) -> None:
    @router.get("/connections")
    def connections(request: Request, response: Response):
        principal = owned(request)
        response.headers["Cache-Control"] = "no-store"
        return safe(lambda: runtime.identity_links.connections(principal))

    @router.post("/identity-links")
    def create(body: LinkRequest, request: Request, response: Response):
        principal = owned(request)
        response.headers["Cache-Control"] = "no-store"
        return safe(lambda: runtime.identity_links.create(principal, body.application_id))

    @router.post("/identity-links/{identifier}/confirm")
    def confirm(identifier: str, body: LinkConfirmation, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.identity_links.confirm(identifier, principal, body.code))

    @router.post("/identity-links/{identifier}/cancel")
    def cancel(identifier: str, request: Request):
        principal = owned(request)
        return safe(lambda: runtime.identity_links.cancel(identifier, principal))
