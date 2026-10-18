"""rules.decide(): approve or decline one revision (design doc 5.5 and 6.3, agreed on 18 October 2026).

The transition table below is the spec, written out cell by cell; `uv run pytest tests/domain -v` prints one
line per cell. The actors are seeded users: rv.dana is a reviewer, lead.fatima holds both roles and made the
request, rm.bram has no reviewer role.
"""

import itertools
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pricing_exceptions.domain.errors import (
    DomainError,
    Forbidden,
    FourEyesViolation,
    InvalidState,
    StaleRevision,
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
from pricing_exceptions.domain.rules import Verdict, decide

REQUEST_ID = uuid.UUID("3f2c9a1e-6b0d-4c55-9e1a-2d7f0b8c4a10")
REASON = "Retention: competitor offer 30 bps below standard"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
EARLIER = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)  # when rv.erik decided the requests that start decided
NOW = datetime(2026, 10, 15, 10, 2, 44, tzinfo=UTC)

RV_DANA = Actor("rv.dana", frozenset({Role.REVIEWER}))
RV_ERIK = Actor("rv.erik", frozenset({Role.REVIEWER}))
LEAD_FATIMA = Actor("lead.fatima", frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER}))
RM_ANNA = Actor("rm.anna", frozenset({Role.RELATIONSHIP_MANAGER}))
RM_BRAM = Actor("rm.bram", frozenset({Role.RELATIONSHIP_MANAGER}))
SVC_MORTGAGE = Actor("svc.mortgage", frozenset({Role.MORTGAGE_PROCESS}))


def fixed_clock() -> datetime:
    """The injected clock, pinned: every decision in these tests happens at NOW."""
    return NOW


def a_request(status: Status, requested_by: str = "lead.fatima") -> PricingExceptionRequest:
    """A request at revision 2, so revision 1 is stale. A decided one was decided by rv.erik."""
    decision = None
    if status is not Status.PENDING_REVIEW:
        comment = "Up to 20 bps acceptable" if status is Status.DECLINED else None
        decision = Decision(decided_by="rv.erik", decided_at=EARLIER, comment=comment)
    return PricingExceptionRequest(
        id=REQUEST_ID,
        application_id="MA-1006",
        requested_by=requested_by,
        status=status,
        revision=2,
        discount_bps=30,
        reason=REASON,
        created_at=CREATED,
        decision=decision,
    )


def a_verdict(outcome: Outcome) -> Verdict:
    return Verdict(outcome, "Within policy" if outcome is Outcome.APPROVED else "Up to 20 bps acceptable")


# ---- The transition table: decide rows ------------------------------------------------------------------
# A success cell holds the status the request moves to; a refusal cell holds the error it raises.
FORBIDDEN, FOUR_EYES, INVALID_STATE, STALE = Forbidden, FourEyesViolation, InvalidState, StaleRevision
APPROVED, DECLINED = Status.APPROVED, Status.DECLINED

COLUMNS = list(itertools.product((RV_DANA, LEAD_FATIMA, RM_BRAM), (Outcome.APPROVED, Outcome.DECLINED)))
TABLE: dict[tuple[Status, int], tuple[Status | type[DomainError], ...]] = {
    # (status, revision sent): rv.dana approves, declines | lead.fatima the same | rm.bram the same
    (Status.PENDING_REVIEW, 2): (APPROVED, DECLINED, FOUR_EYES, FOUR_EYES, FORBIDDEN, FORBIDDEN),
    (Status.PENDING_REVIEW, 1): (STALE, STALE, FOUR_EYES, FOUR_EYES, FORBIDDEN, FORBIDDEN),
    (Status.APPROVED, 2): (INVALID_STATE, INVALID_STATE, FOUR_EYES, FOUR_EYES, FORBIDDEN, FORBIDDEN),
    (Status.APPROVED, 1): (INVALID_STATE, INVALID_STATE, FOUR_EYES, FOUR_EYES, FORBIDDEN, FORBIDDEN),
    (Status.DECLINED, 2): (INVALID_STATE, INVALID_STATE, FOUR_EYES, FOUR_EYES, FORBIDDEN, FORBIDDEN),
    (Status.DECLINED, 1): (INVALID_STATE, INVALID_STATE, FOUR_EYES, FOUR_EYES, FORBIDDEN, FORBIDDEN),
}


VERB = {Outcome.APPROVED: "approves", Outcome.DECLINED: "declines"}


def name(expected: Status | type[DomainError]) -> str:
    return expected if isinstance(expected, Status) else expected.code


# Every status times every revision, so a missing row in TABLE fails at collection with a KeyError.
ROWS = [
    pytest.param(
        status,
        sent,
        actor,
        outcome,
        TABLE[status, sent][column],
        id=f"{status} r2, sent r{sent}, {actor.id} {VERB[outcome]} -> {name(TABLE[status, sent][column])}",
    )
    for (status, sent), (column, (actor, outcome)) in itertools.product(
        itertools.product(Status, (2, 1)), enumerate(COLUMNS)
    )
]


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(("status", "sent", "actor", "outcome", "expected"), ROWS)
def test_decide_transition_table(
    status: Status, sent: int, actor: Actor, outcome: Outcome, expected: Status | type[DomainError]
) -> None:
    request = a_request(status)
    if isinstance(expected, Status):
        decided, event = decide(request, actor, sent, a_verdict(outcome), NOW)
        assert decided.status is expected
        assert event.event_type is EventType(expected)
    else:
        with pytest.raises(expected):
            decide(request, actor, sent, a_verdict(outcome), NOW)


# ---- The rules behind the table -------------------------------------------------------------------------
@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("actor", "requested_by", "status", "sent", "error"),
    [
        pytest.param(
            RM_BRAM, "lead.fatima", Status.APPROVED, 1, Forbidden, id="role before status and revision"
        ),
        pytest.param(RM_ANNA, "rm.anna", Status.PENDING_REVIEW, 2, Forbidden, id="role before four-eyes"),
        pytest.param(
            LEAD_FATIMA, "lead.fatima", Status.APPROVED, 1, FourEyesViolation, id="four-eyes before status"
        ),
        pytest.param(RV_DANA, "lead.fatima", Status.APPROVED, 1, InvalidState, id="status before revision"),
    ],
)
def test_decide_guard_order(
    actor: Actor, requested_by: str, status: Status, sent: int, error: type[DomainError]
) -> None:
    """When several guards fail at once, the first in the fixed order wins: 403, status, then revision."""
    with pytest.raises(error) as raised:
        decide(a_request(status, requested_by), actor, sent, Verdict(Outcome.APPROVED), NOW)
    assert type(raised.value) is error


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("actor", "requested_by", "error"),
    [
        pytest.param(RV_DANA, "rm.anna", None, id="rv.dana decides for rm.anna"),
        pytest.param(RV_ERIK, "rm.anna", None, id="rv.erik decides for rm.anna"),
        pytest.param(LEAD_FATIMA, "rm.anna", None, id="lead.fatima decides for rm.anna"),
        pytest.param(LEAD_FATIMA, "lead.fatima", FourEyesViolation, id="lead.fatima on own request"),
        pytest.param(RM_ANNA, "rm.anna", Forbidden, id="rm.anna on own request, no reviewer role"),
        pytest.param(RM_BRAM, "rm.anna", Forbidden, id="rm.bram, no reviewer role"),
        pytest.param(SVC_MORTGAGE, "rm.anna", Forbidden, id="svc.mortgage, no reviewer role"),
    ],
)
def test_only_a_reviewer_who_is_not_the_requester_decides(
    actor: Actor, requested_by: str, error: type[DomainError] | None
) -> None:
    """Each seeded user against a pending request: holding the reviewer role is needed, and not enough."""
    request = a_request(Status.PENDING_REVIEW, requested_by)
    if error is None:
        decided, _ = decide(request, actor, 2, Verdict(Outcome.APPROVED), NOW)
        assert decided.decision == Decision(decided_by=actor.id, decided_at=NOW, comment=None)
    else:
        with pytest.raises(error):
            decide(request, actor, 2, Verdict(Outcome.APPROVED), NOW)


@pytest.mark.req(id="N1")
@pytest.mark.parametrize("sent", [1, 3], ids=["an older revision", "a revision that does not exist yet"])
def test_a_decision_binds_only_to_the_current_revision(sent: int) -> None:
    request = a_request(Status.PENDING_REVIEW)
    with pytest.raises(StaleRevision) as raised:
        decide(request, RV_DANA, sent, Verdict(Outcome.APPROVED), NOW)
    assert (raised.value.sent_revision, raised.value.current_revision) == (sent, 2)
    assert str(raised.value) == f"You sent revision {sent}; the current revision is 2."
    decided, event = decide(request, RV_DANA, 2, Verdict(Outcome.APPROVED), NOW)
    assert (decided.revision, event.revision) == (2, 2)


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("outcome", "comment"),
    [(Outcome.APPROVED, None), (Outcome.DECLINED, "  Up to 20 bps acceptable\n")],
    ids=["approve without a comment", "decline, comment kept as sent"],
)
def test_a_decision_records_who_and_when(outcome: Outcome, comment: str | None) -> None:
    """The outcome names the reviewer and the time from the clock; the terms and revision stay as they are."""
    request = a_request(Status.PENDING_REVIEW, "rm.anna")
    decided, event = decide(request, RV_DANA, 2, Verdict(outcome, comment), fixed_clock())
    assert decided == replace(
        request,
        status=Status(outcome),
        decision=Decision(decided_by="rv.dana", decided_at=NOW, comment=comment),
    )
    assert event == RequestEvent(
        request_id=REQUEST_ID,
        event_type=EventType(outcome),
        revision=2,
        actor_id="rv.dana",
        occurred_at=NOW,
        discount_bps=30,
        reason=REASON,
        comment=comment,
    )
