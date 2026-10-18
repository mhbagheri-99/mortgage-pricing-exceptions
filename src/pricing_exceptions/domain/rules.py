"""Every business rule: the policy limits, the value objects that enforce them, and the transitions.

The limits are our assumptions (design doc 3.4): the brief sets none, so each is one constant here.
"""

from dataclasses import dataclass, replace
from datetime import datetime

from pricing_exceptions.domain.errors import (
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
    if len(text) > MAX_TEXT_CHARS:
        return f"must be at most {MAX_TEXT_CHARS} characters"
    return None


@dataclass(frozen=True, slots=True)
class Terms:
    """What a requester asks for: a discount in basis points and a reason. Checks itself when built."""

    discount_bps: int
    reason: str

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
    comment: str | None = None

    def __post_init__(self) -> None:
        if self.comment is None:
            if self.outcome is Outcome.DECLINED:
                raise ValidationFailed({"comment": "is required to decline"})
        elif (problem := _text_problem(self.comment)) is not None:
            raise ValidationFailed({"comment": problem})


_STATUS_AFTER = {Outcome.APPROVED: Status.APPROVED, Outcome.DECLINED: Status.DECLINED}
_EVENT_FOR = {Outcome.APPROVED: EventType.APPROVED, Outcome.DECLINED: EventType.DECLINED}


def decide(
    request: PricingExceptionRequest, actor: Actor, revision: int, verdict: Verdict, now: datetime
) -> tuple[PricingExceptionRequest, RequestEvent]:
    """Approve or decline one revision, and return the decided request with the one event to append.

    The guards run in a fixed order (design doc 5.5): who may act (403), then the status (409 invalid-state),
    then the revision (409 stale-revision). The first that fails wins, so the same input always gets the same
    answer, even during a race. The 403 checks read only what never changes on a request.
    """
    if Role.REVIEWER not in actor.roles:
        raise Forbidden("Only a reviewer can decide a request.")
    if actor.id == request.requested_by:  # four-eyes: holding the reviewer role is not enough
        raise FourEyesViolation("You cannot decide a request you made.")
    if request.status is not Status.PENDING_REVIEW:  # approved is final; a declined one is resubmitted first
        raise InvalidState(request.status, "decided")
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
