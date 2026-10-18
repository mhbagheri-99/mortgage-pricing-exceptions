"""Who is calling: the demo identity stand-in (design doc D6).

`X-User-Id` names a seeded user, and it works only when DEMO_IDENTITY is on. Off, every call is 401, so the
stand-in fails closed. In production an OIDC token check replaces this one dependency: the identity seam.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

from pricing_exceptions.domain.model import Actor
from pricing_exceptions.service import Engine, queries

# auto_error=False, so a missing header reaches us and gets our own 401; Swagger shows an Authorize button.
USER_HEADER = APIKeyHeader(
    name="X-User-Id",
    auto_error=False,
    scheme_name="Demo identity",
    description="A seeded user id, such as rm.anna. Works only when DEMO_IDENTITY=true.",
)
CHALLENGE = {"WWW-Authenticate": 'X-User-Id realm="pricing-exceptions"'}  # every 401 must name a challenge


def unauthenticated(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers=CHALLENGE)


def caller_dependency(engine: Engine, *, demo_identity: bool) -> Callable[..., Actor]:
    def caller(request: Request, user_id: Annotated[str | None, Security(USER_HEADER)]) -> Actor:
        if not demo_identity:
            raise unauthenticated("The demo identity is switched off, so no caller can be identified.")
        actor = queries.get_actor(engine, user_id) if user_id else None
        if actor is None:
            raise unauthenticated("Send X-User-Id with a seeded user id, such as rm.anna.")
        request.state.actor = actor  # read by the refused-write log line; set only once verified
        return actor

    return caller
