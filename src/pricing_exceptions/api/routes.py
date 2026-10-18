"""The contract endpoints (design doc 5.1). HTTP only: each route reads its input, builds the domain's value
objects (which check themselves: 422), calls one service function and shapes the answer.

FastAPI runs the checks in the agreed order (5.5): it parses the JSON (400), resolves the caller (401), then
checks the shape of the body and parameters (422), before the route body runs.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Response

from pricing_exceptions.api.cursor import read_cursor, write_cursor
from pricing_exceptions.api.schemas import (
    APPLICATION_ID_PATTERN,
    ApprovedDiscount,
    CreateRequest,
    NoDiscount,
    PageOut,
    RequestOut,
    RequestSummary,
)
from pricing_exceptions.domain.model import Actor, Clock, Status
from pricing_exceptions.domain.rules import Terms
from pricing_exceptions.service import Engine, commands, queries

PREFIX = "/api/v1"
REQUESTS = "/pricing-exception-requests"


def build_router(engine: Engine, clock: Clock, caller: Callable[..., Actor]) -> APIRouter:
    router = APIRouter(prefix=PREFIX)

    @router.post(REQUESTS, status_code=201, name="create", tags=["requests"])
    def create(
        body: CreateRequest, actor: Annotated[Actor, Depends(caller)], response: Response
    ) -> RequestOut:
        """Create a request for a mortgage application. Safe to retry: the same call again answers 201 with
        the same request and `Idempotent-Replayed: true`; a different create for that application is 409."""
        terms = Terms(body.discount_bps, body.reason)
        created = commands.submit(engine, clock, actor, body.application_id, terms)
        response.headers["Location"] = f"{PREFIX}{REQUESTS}/{created.record.request.id}"
        if created.replayed:
            response.headers["Idempotent-Replayed"] = "true"
        return RequestOut.of_record(created.record)

    @router.get(REQUESTS, name="list", tags=["requests"])
    def list_requests(
        actor: Annotated[Actor, Depends(caller)],
        status: Status = Status.PENDING_REVIEW,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> PageOut:
        """Requests in one status, oldest first, one page at a time. Follow `next_cursor` until it is null."""
        after = read_cursor(cursor) if cursor is not None else None
        page = queries.list_requests(engine, actor, status, after, limit)
        next_cursor = write_cursor(page.next_after) if page.next_after else None
        return PageOut(items=[RequestSummary.of(item) for item in page.items], next_cursor=next_cursor)

    @router.get(REQUESTS + "/{request_id}", name="get", tags=["requests"])
    def get(request_id: UUID, actor: Annotated[Actor, Depends(caller)]) -> RequestOut:
        """The request as it is now, its latest decision, and its whole history."""
        return RequestOut.of_record(queries.get_request(engine, actor, request_id))

    @router.get(
        "/mortgage-applications/{application_id}/approved-discount",
        name="approved-discount",
        tags=["mortgage process"],
        dependencies=[Depends(caller)],  # every role may read it, but only a known caller
    )
    def approved_discount(
        application_id: Annotated[str, Path(pattern=APPLICATION_ID_PATTERN)],
    ) -> ApprovedDiscount | NoDiscount:
        """The approved discount for an application, or status NONE before an approval."""
        approved = queries.approved_discount(engine, application_id)
        if approved is None:
            return NoDiscount(application_id=application_id, status="NONE")
        return ApprovedDiscount.of(approved)

    return router
