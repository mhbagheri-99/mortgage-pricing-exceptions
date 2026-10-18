"""rules.revise(): the requester changes the terms, as a new numbered revision (design doc D2, 5.5 and 6.3,
agreed on 18 October 2026).

The transition table below is the spec, written out cell by cell. The request was made by lead.fatima, who
holds both roles; rm.bram is another relationship manager, and rv.dana a reviewer without that role.
"""

import itertools
import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from pricing_exceptions.domain.errors import DomainError, Forbidden, InvalidState, StaleRevision
from pricing_exceptions.domain.model import (
    Actor,
    Decision,
    EventType,
    PricingExceptionRequest,
    RequestEvent,
    Role,
    Status,
)
from pricing_exceptions.domain.rules import Terms, check_role_to_decide, check_role_to_revise, revise

REQUEST_ID = uuid.UUID("3f2c9a1e-6b0d-4c55-9e1a-2d7f0b8c4a10")
REASON = "Retention: competitor offer 30 bps below standard"
NEW_REASON = "Competitor improved their offer"
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
EARLIER = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)  # when rv.erik decided the requests that start decided
NOW = datetime(2026, 10, 15, 10, 2, 44, tzinfo=UTC)
NEW_TERMS = Terms(35, NEW_REASON)

USERS = {
    "rm.anna": Actor("rm.anna", frozenset({Role.RELATIONSHIP_MANAGER})),
    "rm.bram": Actor("rm.bram", frozenset({Role.RELATIONSHIP_MANAGER})),
    "rv.dana": Actor("rv.dana", frozenset({Role.REVIEWER})),
    "rv.erik": Actor("rv.erik", frozenset({Role.REVIEWER})),
    "lead.fatima": Actor("lead.fatima", frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER})),
    "svc.mortgage": Actor("svc.mortgage", frozenset({Role.MORTGAGE_PROCESS})),
}
LEAD_FATIMA, RM_BRAM, RV_DANA = USERS["lead.fatima"], USERS["rm.bram"], USERS["rv.dana"]


def a_request(status: Status, requested_by: str = "lead.fatima") -> PricingExceptionRequest:
    """A request at revision 2 for 30 bps, so revision 1 is stale. A decided one was decided by rv.erik."""
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


# ---- The transition table: revise rows ------------------------------------------------------------------
# A success cell holds the event the revision writes; a refusal cell holds the error it raises.
FORBIDDEN, INVALID_STATE, STALE = Forbidden, InvalidState, StaleRevision
AMENDED, RESUBMITTED = EventType.AMENDED, EventType.RESUBMITTED

COLUMNS = (LEAD_FATIMA, RM_BRAM, RV_DANA)
TABLE: dict[tuple[Status, int], tuple[EventType | type[DomainError], ...]] = {
    # (status, revision sent): lead.fatima, the requester | rm.bram, another RM | rv.dana, a reviewer
    (Status.PENDING_REVIEW, 2): (AMENDED, FORBIDDEN, FORBIDDEN),
    (Status.PENDING_REVIEW, 1): (STALE, FORBIDDEN, FORBIDDEN),
    (Status.APPROVED, 2): (INVALID_STATE, FORBIDDEN, FORBIDDEN),
    (Status.APPROVED, 1): (INVALID_STATE, FORBIDDEN, FORBIDDEN),
    (Status.DECLINED, 2): (RESUBMITTED, FORBIDDEN, FORBIDDEN),
    (Status.DECLINED, 1): (STALE, FORBIDDEN, FORBIDDEN),
}


def name(expected: EventType | type[DomainError]) -> str:
    return expected if isinstance(expected, EventType) else expected.code


# Every status times every revision, so a missing row in TABLE fails at collection with a KeyError.
ROWS = [
    pytest.param(
        status,
        sent,
        actor,
        TABLE[status, sent][column],
        id=f"{status} r2, sent r{sent}, {actor.id} revises -> {name(TABLE[status, sent][column])}",
    )
    for (status, sent), (column, actor) in itertools.product(
        itertools.product(Status, (2, 1)), enumerate(COLUMNS)
    )
]


@pytest.mark.req(id="FR-2")
@pytest.mark.parametrize(("status", "sent", "actor", "expected"), ROWS)
def test_revise_transition_table(
    status: Status, sent: int, actor: Actor, expected: EventType | type[DomainError]
) -> None:
    request = a_request(status)
    if isinstance(expected, EventType):
        revised, event = revise(request, actor, sent, NEW_TERMS, NOW)
        assert (revised.status, revised.revision) == (Status.PENDING_REVIEW, 3)
        assert (event.event_type, event.revision) == (expected, 3)
    else:
        with pytest.raises(expected):
            revise(request, actor, sent, NEW_TERMS, NOW)


# ---- The rules behind the table -------------------------------------------------------------------------
@pytest.mark.req(id="FR-2")
@pytest.mark.parametrize(
    ("actor", "status", "sent", "error"),
    [
        pytest.param(RV_DANA, Status.APPROVED, 1, Forbidden, id="role before status and revision"),
        pytest.param(RM_BRAM, Status.APPROVED, 1, Forbidden, id="owner before status and revision"),
        pytest.param(LEAD_FATIMA, Status.APPROVED, 1, InvalidState, id="status before revision"),
    ],
)
def test_revise_guard_order(actor: Actor, status: Status, sent: int, error: type[DomainError]) -> None:
    """When several guards fail at once, the first in the fixed order wins: 403, status, then revision."""
    with pytest.raises(error) as raised:
        revise(a_request(status), actor, sent, NEW_TERMS, NOW)
    assert type(raised.value) is error


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "allowed"),
    [
        pytest.param("rm.anna", True, id="rm.anna, the requester"),
        pytest.param("rm.bram", False, id="rm.bram, another relationship manager"),
        pytest.param("lead.fatima", False, id="lead.fatima, both roles but not the requester"),
        pytest.param("rv.dana", False, id="rv.dana, a reviewer"),
        pytest.param("rv.erik", False, id="rv.erik, a reviewer"),
        pytest.param("svc.mortgage", False, id="svc.mortgage, the mortgage process"),
    ],
)
def test_only_the_requester_revises(user: str, allowed: bool) -> None:
    """Each seeded user against rm.anna's pending request: one accountable maker (design doc D2)."""
    request = a_request(Status.PENDING_REVIEW, "rm.anna")
    if allowed:
        assert revise(request, USERS[user], 2, NEW_TERMS, NOW)[0].requested_by == "rm.anna"
    else:
        with pytest.raises(Forbidden):
            revise(request, USERS[user], 2, NEW_TERMS, NOW)


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "may_revise", "may_decide"),
    [
        ("rm.anna", True, False),
        ("rm.bram", True, False),
        ("rv.dana", False, True),
        ("rv.erik", False, True),
        ("lead.fatima", True, True),
        ("svc.mortgage", False, False),
    ],
)
def test_the_role_checks_that_run_before_the_lock(user: str, may_revise: bool, may_decide: bool) -> None:
    """Whether someone may revise or decide at all depends only on who asks, so the service checks it before
    it locks the request (P4 decision 1). Only staff, who may read any request, can then learn which ids
    exist."""
    for check, allowed in ((check_role_to_revise, may_revise), (check_role_to_decide, may_decide)):
        if allowed:
            check(USERS[user])  # passes without raising
        else:
            with pytest.raises(Forbidden):
                check(USERS[user])


@pytest.mark.req(id="FR-2")
def test_amend_makes_the_next_revision_with_the_new_terms() -> None:
    """While pending, a revision replaces both terms at revision n + 1. Who asked and when the request was
    created never change; the time of the change comes from the clock."""
    request = a_request(Status.PENDING_REVIEW)
    revised, event = revise(request, LEAD_FATIMA, 2, NEW_TERMS, NOW)
    assert revised == replace(request, revision=3, discount_bps=35, reason=NEW_REASON)
    assert event == RequestEvent(
        request_id=REQUEST_ID,
        event_type=EventType.AMENDED,
        revision=3,
        actor_id="lead.fatima",
        occurred_at=NOW,
        discount_bps=35,
        reason=NEW_REASON,
        comment=None,
    )


@pytest.mark.req(id="FR-2")
def test_resubmit_clears_the_decision() -> None:
    """After a decline, new terms go back for review: pending at revision n + 1, with the row's decision
    cleared. The decline itself stays in history (design doc 4.2)."""
    request = a_request(Status.DECLINED)
    revised, event = revise(request, LEAD_FATIMA, 2, NEW_TERMS, NOW)
    assert revised == replace(
        request,
        status=Status.PENDING_REVIEW,
        revision=3,
        discount_bps=35,
        reason=NEW_REASON,
        decision=None,
    )
    assert event == RequestEvent(
        request_id=REQUEST_ID,
        event_type=EventType.RESUBMITTED,
        revision=3,
        actor_id="lead.fatima",
        occurred_at=NOW,
        discount_bps=35,
        reason=NEW_REASON,
        comment=None,
    )


@pytest.mark.req(id="FR-2")
def test_the_same_terms_again_still_make_a_revision() -> None:
    """D2 cut the identical-terms rule: unchanged terms are allowed, and still get the next number."""
    revised, event = revise(a_request(Status.PENDING_REVIEW), LEAD_FATIMA, 2, Terms(30, REASON), NOW)
    assert (revised.revision, revised.discount_bps, revised.reason) == (3, 30, REASON)
    assert (event.event_type, event.revision) == (EventType.AMENDED, 3)


@pytest.mark.req(id="FR-2")
@pytest.mark.parametrize("sent", [1, 3], ids=["an older revision", "a revision that does not exist yet"])
def test_a_revision_applies_only_to_the_current_revision(sent: int) -> None:
    with pytest.raises(StaleRevision) as raised:
        revise(a_request(Status.PENDING_REVIEW), LEAD_FATIMA, sent, NEW_TERMS, NOW)
    assert (raised.value.sent_revision, raised.value.current_revision) == (sent, 2)
    assert str(raised.value) == f"You sent revision {sent}; the current revision is 2."
