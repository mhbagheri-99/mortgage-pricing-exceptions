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
    DecideRequest,
    NoDiscount,
    PageOut,
    RequestOut,
    ReviseRequest,
)
from pricing_exceptions.domain.model import Actor, Clock, Status
from pricing_exceptions.domain.rules import Terms, Verdict
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
        return RequestOut.for_caller(created.record, actor)

    @router.get(REQUESTS, name="list", tags=["requests"])
    def list_requests(
        actor: Annotated[Actor, Depends(caller)],
        status: Status = Status.PENDING_REVIEW,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[str | None, Query(max_length=200)] = None,
    ) -> PageOut:
        """Requests in one status, oldest first, one page at a time. Follow `next_cursor` until it is null.
        `permissions` says whether the caller may create a request."""
        after = read_cursor(cursor) if cursor is not None else None
        page = queries.list_requests(engine, actor, status, after, limit)
        next_cursor = write_cursor(page.next_after) if page.next_after else None
        return PageOut.for_caller(page.items, next_cursor, actor)

    @router.get(REQUESTS + "/{request_id}", name="get", tags=["requests"])
    def get(request_id: UUID, actor: Annotated[Actor, Depends(caller)]) -> RequestOut:
        """The request as it is now, its latest decision, its whole history, and what the caller may do."""
        return RequestOut.for_caller(queries.get_request(engine, actor, request_id), actor)

    @router.post(REQUESTS + "/{request_id}/revisions", name="revise", tags=["requests"])
    def revise(request_id: UUID, body: ReviseRequest, actor: Annotated[Actor, Depends(caller)]) -> RequestOut:
        """Change the discount and the reason, as the next revision. Only the requester, while the request
        awaits review, or after a decline as a resubmission; approved is final. Send the revision you saw:
        if the request changed since, the answer is 409 stale-revision with the request as it is now."""
        terms = Terms(body.discount_bps, body.reason)
        revised = commands.revise(engine, clock, actor, request_id, body.revision, terms)
        return RequestOut.for_caller(revised, actor)

    @router.post(REQUESTS + "/{request_id}/decisions", name="decide", tags=["requests"])
    def decide(request_id: UUID, body: DecideRequest, actor: Annotated[Actor, Depends(caller)]) -> RequestOut:
        """Approve or decline the revision you reviewed. A reviewer who did not make the request; a decline
        needs a comment. The decision records who and when, and binds to that revision only."""
        verdict = Verdict(body.outcome, body.comment)
        decided = commands.decide(engine, clock, actor, request_id, body.revision, verdict)
        return RequestOut.for_caller(decided, actor)

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
