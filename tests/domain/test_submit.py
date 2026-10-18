"""Create and read, in the domain: who may create and read, what a new request looks like, what counts as a
retry of the create that made a request (ADR 0002), and which decision is the latest."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from pricing_exceptions.domain.errors import Forbidden
from pricing_exceptions.domain.model import (
    Actor,
    EventType,
    PricingExceptionRequest,
    RequestEvent,
    RequestRecord,
    Role,
    Status,
)
from pricing_exceptions.domain.rules import Terms, is_retry, latest_decision, require_staff, submit

NOW = datetime(2026, 10, 18, 9, 12, 3, 120000, tzinfo=UTC)
REQUEST_ID = UUID("3f2c0000-0000-4000-8000-000000000001")
REASON = "Retention: competitor offer 25 bps below standard"
NEW_REASON = "Competitor improved their offer"  # what rm.anna changed the reason to, along with the discount

USERS = {
    "rm.anna": Actor("rm.anna", frozenset({Role.RELATIONSHIP_MANAGER})),
    "rm.bram": Actor("rm.bram", frozenset({Role.RELATIONSHIP_MANAGER})),
    "rv.dana": Actor("rv.dana", frozenset({Role.REVIEWER})),
    "rv.erik": Actor("rv.erik", frozenset({Role.REVIEWER})),
    "lead.fatima": Actor("lead.fatima", frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER})),
    "svc.mortgage": Actor("svc.mortgage", frozenset({Role.MORTGAGE_PROCESS})),
}


@pytest.mark.req(id="FR-1")
def test_submit_makes_revision_one_and_its_submitted_event() -> None:
    request, event = submit(USERS["rm.anna"], "MA-1001", Terms(25, REASON), REQUEST_ID, NOW)
    assert request == PricingExceptionRequest(
        id=REQUEST_ID,
        application_id="MA-1001",
        requested_by="rm.anna",
        status=Status.PENDING_REVIEW,
        revision=1,
        discount_bps=25,
        reason=REASON,
        created_at=NOW,
        decision=None,
    )
    assert event == RequestEvent(
        request_id=REQUEST_ID,
        event_type=EventType.SUBMITTED,
        revision=1,
        actor_id="rm.anna",
        occurred_at=NOW,
        discount_bps=25,
        reason=REASON,
        comment=None,
    )


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "allowed"),
    [
        ("rm.anna", True),
        ("rm.bram", True),
        ("lead.fatima", True),
        ("rv.dana", False),
        ("svc.mortgage", False),
    ],
)
def test_only_a_relationship_manager_submits(user: str, allowed: bool) -> None:
    if allowed:
        assert submit(USERS[user], "MA-1001", Terms(25, REASON), REQUEST_ID, NOW)[0].requested_by == user
    else:
        with pytest.raises(Forbidden):
            submit(USERS[user], "MA-1001", Terms(25, REASON), REQUEST_ID, NOW)


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "allowed"),
    [("rm.anna", True), ("rv.dana", True), ("lead.fatima", True), ("svc.mortgage", False)],
)
def test_reading_requests_is_for_staff(user: str, allowed: bool) -> None:
    """Relationship managers and reviewers read every request; the mortgage process reads only discounts."""
    if allowed:
        require_staff(USERS[user])
    else:
        with pytest.raises(Forbidden):
            require_staff(USERS[user])


def submitted_by_anna() -> RequestRecord:
    request, event = submit(USERS["rm.anna"], "MA-1001", Terms(25, REASON), REQUEST_ID, NOW)
    return RequestRecord(request, (event,))


def amended_to_30() -> RequestRecord:
    """rm.anna's request after she changed it to 30 bps and a new reason: the row and the AMENDED event moved
    on, the SUBMITTED event did not."""
    record = submitted_by_anna()
    amended = replace(record.request, revision=2, discount_bps=30, reason=NEW_REASON)
    amendment = replace(
        record.history[0], event_type=EventType.AMENDED, revision=2, discount_bps=30, reason=NEW_REASON
    )
    return RequestRecord(amended, (*record.history, amendment))


def never_submitted() -> RequestRecord:
    """A record whose history has no SUBMITTED event: nothing says what the first create sent."""
    return RequestRecord(submitted_by_anna().request, ())


@pytest.mark.req(id="FR-6")
@pytest.mark.parametrize(
    ("existing", "user", "terms", "retry"),
    [
        pytest.param(submitted_by_anna, "rm.anna", Terms(25, REASON), True, id="the same create again"),
        pytest.param(
            amended_to_30, "rm.anna", Terms(25, REASON), True, id="the original create, after a change"
        ),
        pytest.param(
            amended_to_30, "rm.anna", Terms(30, NEW_REASON), False, id="the current terms, not the first"
        ),
        pytest.param(submitted_by_anna, "rm.anna", Terms(30, REASON), False, id="another discount"),
        pytest.param(never_submitted, "rm.anna", Terms(25, REASON), False, id="no SUBMITTED event to repeat"),
        pytest.param(
            submitted_by_anna, "rm.anna", Terms(25, REASON + " "), False, id="a reason one space longer"
        ),
        pytest.param(submitted_by_anna, "rm.bram", Terms(25, REASON), False, id="another requester"),
        pytest.param(
            submitted_by_anna, "lead.fatima", Terms(25, REASON), False, id="another requester, both roles"
        ),
    ],
)
def test_a_retry_matches_the_requester_and_the_submitted_terms(
    existing: Callable[[], RequestRecord], user: str, terms: Terms, retry: bool
) -> None:
    """It reads the SUBMITTED event, which never changes, and compares the text exactly as sent."""
    assert is_retry(existing(), USERS[user], terms) is retry


@pytest.mark.req(id="FR-5")
def test_latest_decision_is_the_newest_decision_event() -> None:
    def event(event_type: EventType, revision: int, at_hour: int) -> RequestEvent:
        return RequestEvent(
            REQUEST_ID, event_type, revision, "rv.erik", NOW.replace(hour=at_hour), 40, REASON
        )

    declined_once = (event(EventType.SUBMITTED, 1, 9), event(EventType.DECLINED, 1, 10))
    resubmitted = (*declined_once, event(EventType.RESUBMITTED, 2, 11))
    approved = (*resubmitted, event(EventType.APPROVED, 2, 12))
    assert latest_decision(declined_once[:1]) is None
    assert latest_decision(declined_once) == declined_once[1]
    assert latest_decision(resubmitted) == declined_once[1], "a resubmission shows the decline it answers"
    assert latest_decision(approved) == approved[3]
