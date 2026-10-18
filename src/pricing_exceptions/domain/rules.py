"""Every business rule: the policy limits, the value objects that enforce them, and the transitions.

The limits are our assumptions (design doc 3.4): the brief sets none, so each is one constant here.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pricing_exceptions.domain.errors import (
    DomainError,
    Forbidden,
    FourEyesViolation,
    InvalidState,
    StaleRevision,
    ValidationFailed,
)
from pricing_exceptions.domain.model import (
    Actor,
    Decision,
    EventType,
    Outcome,
    PricingExceptionRequest,
    RequestEvent,
    RequestRecord,
    Role,
    Status,
)

MIN_DISCOUNT_BPS = 1
MAX_DISCOUNT_BPS = 100
MAX_TEXT_CHARS = 1_000


def _text_problem(text: str) -> str | None:
    """What is wrong with a reason or comment, or None. The text itself is never trimmed or rewritten."""
    if not text.strip():
        return "must not be blank"
    if "\x00" in text:  # PostgreSQL text cannot hold NUL; without this check the insert fails as a 500
        return "must not contain the NUL character"
    if not text.isascii():  # JSON can carry half a surrogate pair, which UTF-8 cannot encode: the same 500
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            return "must be valid Unicode text"
    if len(text) > MAX_TEXT_CHARS:
        return f"must be at most {MAX_TEXT_CHARS} characters"
    return None


@dataclass(frozen=True, slots=True)
class Terms:
    """What a requester asks for: a discount in basis points and a reason. Checks itself when built."""

    discount_bps: int
    reason: str = field(repr=False)

    def __post_init__(self) -> None:
        problems: dict[str, str] = {}
        # `type(...) is int`, not isinstance: True is an int in Python, and neither it nor 25.0 is a discount.
        if type(self.discount_bps) is not int or not (
            MIN_DISCOUNT_BPS <= self.discount_bps <= MAX_DISCOUNT_BPS
        ):
            problems["discount_bps"] = (
                f"must be a whole number from {MIN_DISCOUNT_BPS} to {MAX_DISCOUNT_BPS} basis points"
            )
        if (problem := _text_problem(self.reason)) is not None:
            problems["reason"] = problem
        if problems:
            raise ValidationFailed(problems)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What a reviewer decides: the outcome, and a comment every decline needs. Checks itself when built."""

    outcome: Outcome
    comment: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.comment is None:
            if self.outcome is Outcome.DECLINED:
                raise ValidationFailed({"comment": "is required to decline"})
        elif (problem := _text_problem(self.comment)) is not None:
            raise ValidationFailed({"comment": problem})


_STATUS_AFTER = {Outcome.APPROVED: Status.APPROVED, Outcome.DECLINED: Status.DECLINED}
_EVENT_FOR = {Outcome.APPROVED: EventType.APPROVED, Outcome.DECLINED: EventType.DECLINED}


def check_role_to_decide(actor: Actor) -> None:
    """403 unless the actor is a reviewer. It depends only on who asks, so the service runs it before it
    locks the request; decide() runs it first too, so the rule is complete on its own (P4 decision 1)."""
    if Role.REVIEWER not in actor.roles:
        raise Forbidden("Only a reviewer can decide a request.")


def check_role_to_revise(actor: Actor) -> None:
    """403 unless the actor is a relationship manager; run before the lock, like the reviewer role."""
    if Role.RELATIONSHIP_MANAGER not in actor.roles:
        raise Forbidden("Only a relationship manager can revise a request.")


def check_decide(request: PricingExceptionRequest, actor: Actor) -> None:
    """Whether the actor may decide the request as it is now: every guard of decide() but the revision."""
    check_role_to_decide(actor)
    if actor.id == request.requested_by:  # four-eyes: holding the reviewer role is not enough
        raise FourEyesViolation("You cannot decide a request you made.")
    if request.status is not Status.PENDING_REVIEW:  # approved is final; a declined one is resubmitted first
        raise InvalidState(request.status, "decided")


def decide(
    request: PricingExceptionRequest, actor: Actor, revision: int, verdict: Verdict, now: datetime
) -> tuple[PricingExceptionRequest, RequestEvent]:
    """Approve or decline one revision, and return the decided request with the one event to append.

    The guards run in a fixed order (design doc 5.5): who may act (403), then the status (409 invalid-state),
    then the revision (409 stale-revision). The first that fails wins, so the same input always gets the same
    answer, even during a race. The 403 checks read only what never changes on a request.
    """
    check_decide(request, actor)
    if revision != request.revision:  # the decision binds to the revision the reviewer saw (N1)
        raise StaleRevision(sent=revision, current=request.revision)
    decided = replace(
        request,
        status=_STATUS_AFTER[verdict.outcome],
        decision=Decision(decided_by=actor.id, decided_at=now, comment=verdict.comment),
    )
    event = RequestEvent(
        request_id=request.id,
        event_type=_EVENT_FOR[verdict.outcome],
        revision=request.revision,
        actor_id=actor.id,
        occurred_at=now,
        discount_bps=request.discount_bps,
        reason=request.reason,
        comment=verdict.comment,
    )
    return decided, event


# What a revision writes, by the status it starts from. Approved is not here: it is final (design doc D1).
_REVISION_EVENT = {Status.PENDING_REVIEW: EventType.AMENDED, Status.DECLINED: EventType.RESUBMITTED}


def check_revise(request: PricingExceptionRequest, actor: Actor) -> EventType:
    """Whether the actor may revise the request as it is now: every guard of revise() but the revision. It
    returns the event a revision would write."""
    check_role_to_revise(actor)
    if actor.id != request.requested_by:  # one accountable maker: four-eyes compares against this person
        raise Forbidden("Only the relationship manager who made the request can revise it.")
    event_type = _REVISION_EVENT.get(request.status)
    if event_type is None:  # approved, or any status that does not take a revision
        raise InvalidState(request.status, "revised")
    return event_type


def revise(
    request: PricingExceptionRequest, actor: Actor, revision: int, terms: Terms, now: datetime
) -> tuple[PricingExceptionRequest, RequestEvent]:
    """Change the terms as the next revision, and return the revised request with the one event to append.

    The guards run in the same fixed order as decide() (design doc 5.5): who may act (403), then the status
    (409 invalid-state), then the revision (409 stale-revision). Pending writes AMENDED. Declined writes
    RESUBMITTED and goes back to pending, which clears the row's decision; the decline stays in history (D2,
    4.2). Both terms are replaced every time, and terms identical to the current ones are allowed (D2).
    """
    event_type = check_revise(request, actor)
    if revision != request.revision:  # the change applies to the revision the requester saw
        raise StaleRevision(sent=revision, current=request.revision)
    revised = replace(
        request,
        status=Status.PENDING_REVIEW,
        revision=request.revision + 1,
        discount_bps=terms.discount_bps,
        reason=terms.reason,
        decision=None,  # a resubmission clears the decline from the row
    )
    event = RequestEvent(
        request_id=request.id,
        event_type=event_type,
        revision=revised.revision,
        actor_id=actor.id,
        occurred_at=now,
        discount_bps=terms.discount_bps,
        reason=terms.reason,
    )
    return revised, event


def submit(
    actor: Actor, application_id: str, terms: Terms, request_id: UUID, now: datetime
) -> tuple[PricingExceptionRequest, RequestEvent]:
    """Create a request at revision 1, and return it with its SUBMITTED event.

    Only the role is checked here (403). Whether the application exists is reference data the service looks
    up; one request per application is the database's unique constraint, the one rule that spans rows
    (design doc 4.1).
    """
    if Role.RELATIONSHIP_MANAGER not in actor.roles:
        raise Forbidden("Only a relationship manager can create a request.")
    request = PricingExceptionRequest(
        id=request_id,
        application_id=application_id,
        requested_by=actor.id,
        status=Status.PENDING_REVIEW,
        revision=1,
        discount_bps=terms.discount_bps,
        reason=terms.reason,
        created_at=now,
    )
    event = RequestEvent(
        request_id=request_id,
        event_type=EventType.SUBMITTED,
        revision=1,
        actor_id=actor.id,
        occurred_at=now,
        discount_bps=terms.discount_bps,
        reason=terms.reason,
    )
    return request, event


def is_retry(existing: RequestRecord, actor: Actor, terms: Terms) -> bool:
    """True when this create repeats the one that made `existing`: the same requester and the same terms,
    exactly as sent (design doc D3).

    It compares with the SUBMITTED event, which never changes, not with the current row, so a retry that
    arrives after an amendment or a decision still replays. Anything else is a different create.
    """
    submitted = next((e for e in existing.history if e.event_type is EventType.SUBMITTED), None)
    if submitted is None:
        return False
    return (submitted.actor_id, submitted.discount_bps, submitted.reason) == (
        actor.id,
        terms.discount_bps,
        terms.reason,
    )


class Action(StrEnum):
    """What a caller can do to an existing request."""

    DECIDE = "decide"
    REVISE = "revise"


_CHECKS: dict[Action, Callable[[PricingExceptionRequest, Actor], object]] = {
    Action.DECIDE: check_decide,
    Action.REVISE: check_revise,
}


def permissions(request: PricingExceptionRequest, actor: Actor) -> dict[Action, DomainError | None]:
    """What the actor may do to the request as it is now: for each action, None when it would go through at
    the current revision, else the error it would raise (P7, design doc 10.2).

    It runs the actions' own checks, in their own order, so a permission and the action can never disagree.
    The revision is left out because a caller acts on the current one; the action still checks it.
    """
    allowed: dict[Action, DomainError | None] = {}
    for action, check in _CHECKS.items():
        try:
            check(request, actor)
        except DomainError as refusal:
            allowed[action] = refusal
        else:
            allowed[action] = None
    return allowed


_STAFF = frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER})


def require_staff(actor: Actor) -> None:
    """Reading requests is for staff: relationship managers and reviewers. The mortgage process reads only
    approved discounts (design doc 5.1)."""
    if not actor.roles & _STAFF:
        raise Forbidden("Only relationship managers and reviewers can read requests.")


_DECISION_EVENTS = frozenset(_EVENT_FOR.values())


def latest_decision(history: tuple[RequestEvent, ...]) -> RequestEvent | None:
    """The newest decision in a request's history, or None. After a resubmission it is still the decline the
    resubmission answers, although the row's decision columns are empty (design doc 5.2)."""
    return next((e for e in reversed(history) if e.event_type in _DECISION_EVENTS), None)
