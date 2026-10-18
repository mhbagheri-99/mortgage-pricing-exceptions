"""Request and response bodies (design doc 5.1 to 5.6).

Input is strict where it matters (1.4): `StrictInt` and `StrictStr` refuse "25", 25.0 and true, and
`extra="forbid"` refuses fields the client may not set, such as status or decided_by. These models check shape
only; the policy limits are the domain's value objects (5.5 step 4).
"""

from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, StringConstraints

from pricing_exceptions.domain.errors import DomainError
from pricing_exceptions.domain.model import (
    Actor,
    EventType,
    Outcome,
    PricingExceptionRequest,
    RequestEvent,
    RequestRecord,
    Role,
    Status,
    User,
)
from pricing_exceptions.domain.rules import Action, latest_decision, permissions

# 1 to 64 letters, digits, dot, underscore or hyphen, checked before any lookup: a NUL (%00) or a newline
# never reaches PostgreSQL, whose text type cannot hold a NUL. The pattern runs on Rust's regex engine, where
# $ is the very end of the text.
APPLICATION_ID_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
ApplicationId = Annotated[str, StringConstraints(strict=True, pattern=APPLICATION_ID_PATTERN)]


class CreateRequest(BaseModel):
    """What a relationship manager sends to create a request."""

    model_config = ConfigDict(extra="forbid")

    application_id: ApplicationId
    discount_bps: StrictInt
    reason: StrictStr


# The revision the caller saw. Below 1 can never exist, so it is wrong on its own: 422. Any other revision
# that is not the current one is 409 stale-revision (P4 decision 2).
Revision = Annotated[StrictInt, Field(ge=1)]


class ReviseRequest(BaseModel):
    """What the requester sends to change the terms: the revision they saw and both terms, so every revision
    is complete on its own (design doc 5.3). The application id is not here: another application is another
    request (D2)."""

    model_config = ConfigDict(extra="forbid")

    revision: Revision
    discount_bps: StrictInt
    reason: StrictStr


class DecideRequest(BaseModel):
    """What a reviewer sends: the revision they reviewed, the outcome, and a comment, which a decline needs
    (design doc 5.3)."""

    model_config = ConfigDict(extra="forbid")

    revision: Revision
    outcome: Outcome
    comment: StrictStr | None = None


class DecisionOut(BaseModel):
    outcome: Outcome
    revision: int
    decided_by: str
    decided_at: datetime
    comment: str | None

    @classmethod
    def of(cls, event: RequestEvent) -> Self:
        return cls(
            outcome=Outcome(event.event_type),
            revision=event.revision,
            decided_by=event.actor_id,
            decided_at=event.occurred_at,
            comment=event.comment,
        )


class EventOut(BaseModel):
    event_type: EventType
    revision: int
    actor: str
    occurred_at: datetime
    discount_bps: int
    reason: str
    comment: str | None

    @classmethod
    def of(cls, event: RequestEvent) -> Self:
        return cls(
            event_type=event.event_type,
            revision=event.revision,
            actor=event.actor_id,
            occurred_at=event.occurred_at,
            discount_bps=event.discount_bps,
            reason=event.reason,
            comment=event.comment,
        )


class RequestSummary(BaseModel):
    """One row of the list: the fields a queue needs. History stays in GET (design doc 5.6)."""

    id: UUID
    application_id: str
    status: Status
    revision: int
    discount_bps: int
    requested_by: str
    created_at: datetime

    @classmethod
    def of(cls, request: PricingExceptionRequest) -> Self:
        return cls(
            id=request.id,
            application_id=request.application_id,
            status=request.status,
            revision=request.revision,
            discount_bps=request.discount_bps,
            requested_by=request.requested_by,
            created_at=request.created_at,
        )


class RequestCurrent(RequestSummary):
    """The request as it is now, without its history: the `current` every 409 carries (design doc 5.3).
    `latest_decision` comes from history, so a resubmitted request still shows the decline it answers."""

    reason: str
    latest_decision: DecisionOut | None

    @classmethod
    def of_record(cls, record: RequestRecord) -> Self:
        decision = latest_decision(record.history)
        return cls(
            **RequestSummary.of(record.request).model_dump(),
            reason=record.request.reason,
            latest_decision=DecisionOut.of(decision) if decision else None,
        )


class PermissionOut(BaseModel):
    """Whether the caller may take one action now. If not, the problem type and the detail the action would
    be refused with, so a client can say why instead of offering a button that can only fail (P7)."""

    allowed: bool
    problem: str | None
    detail: str | None

    @classmethod
    def of(cls, refusal: DomainError | None) -> Self:
        if refusal is None:
            return cls(allowed=True, problem=None, detail=None)
        return cls(allowed=False, problem=refusal.code, detail=str(refusal))


class RequestOut(RequestCurrent):
    """The request with its whole history, oldest event first: the body of GET, create and its replay,
    revise and decide. `permissions` is what the caller may do to it now; the domain's own checks decide
    it, and the actions still run them when tried."""

    history: list[EventOut]
    permissions: dict[Action, PermissionOut]

    @classmethod
    def for_caller(cls, record: RequestRecord, caller: Actor) -> Self:
        return cls(
            **RequestCurrent.of_record(record).model_dump(),
            history=[EventOut.of(event) for event in record.history],
            permissions={
                action: PermissionOut.of(refusal)
                for action, refusal in permissions(record.request, caller).items()
            },
        )


class PageOut(BaseModel):
    """One page of the list. Follow `next_cursor` until it is null to see every request (design doc 5.6)."""

    items: list[RequestSummary]
    next_cursor: str | None


class ApprovedDiscount(BaseModel):
    """What the mortgage process reads once a request is approved (design doc 5.4)."""

    application_id: str
    status: Literal["APPROVED"]
    discount_bps: int
    request_id: UUID
    revision: int
    approved_by: str
    approved_at: datetime

    @classmethod
    def of(cls, request: PricingExceptionRequest) -> Self:
        if request.decision is None:
            raise ValueError("an approved request always has its decision")
        return cls(
            application_id=request.application_id,
            status="APPROVED",
            discount_bps=request.discount_bps,
            request_id=request.id,
            revision=request.revision,
            approved_by=request.decision.decided_by,
            approved_at=request.decision.decided_at,
        )


class DemoUser(BaseModel):
    """One entry in the demo's acting-as switcher; the roles in a fixed order, so the list never shuffles."""

    id: str
    display_name: str
    roles: list[Role]

    @classmethod
    def of(cls, user: User) -> Self:
        return cls(id=user.id, display_name=user.display_name, roles=sorted(user.roles))


class NoDiscount(BaseModel):
    """Before an approval. There is no discount_bps key at all, so a consumer cannot read a null as zero."""

    application_id: str
    status: Literal["NONE"]
