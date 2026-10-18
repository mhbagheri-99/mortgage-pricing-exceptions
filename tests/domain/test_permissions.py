"""rules.permissions(): what a caller may do to a request as it is now, and rules.collection_permissions():
whether they may create one.

The page shows only the actions the server says a caller may take, and the server still enforces every rule
when the action is tried. So the permissions must never disagree with the actions: the second test runs the
real rule for every cell and checks that it does exactly what the permission said, with the same error.

The request was made by lead.fatima, who holds both roles; rm.bram is another relationship manager, rv.dana a
reviewer, and svc.mortgage the mortgage process.
"""

import itertools
import uuid
from datetime import UTC, datetime

import pytest

from pricing_exceptions.domain.errors import Forbidden, FourEyesViolation, InvalidState
from pricing_exceptions.domain.model import Actor, Decision, Outcome, PricingExceptionRequest, Role, Status
from pricing_exceptions.domain.rules import (
    Action,
    Terms,
    Verdict,
    collection_permissions,
    decide,
    permissions,
    require_staff,
    revise,
    submit,
)

REQUEST_ID = uuid.UUID("3f2c9a1e-6b0d-4c55-9e1a-2d7f0b8c4a10")
CREATED = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
EARLIER = datetime(2026, 10, 15, 9, 30, tzinfo=UTC)
NOW = datetime(2026, 10, 15, 10, 2, 44, tzinfo=UTC)

USERS = {
    "lead.fatima": Actor("lead.fatima", frozenset({Role.RELATIONSHIP_MANAGER, Role.REVIEWER})),
    "rm.bram": Actor("rm.bram", frozenset({Role.RELATIONSHIP_MANAGER})),
    "rv.dana": Actor("rv.dana", frozenset({Role.REVIEWER})),
    "svc.mortgage": Actor("svc.mortgage", frozenset({Role.MORTGAGE_PROCESS})),
}


def a_request(status: Status) -> PricingExceptionRequest:
    """lead.fatima's request at revision 2 for 30 bps. A decided one was decided by rv.erik."""
    decision = None
    if status is not Status.PENDING_REVIEW:
        comment = "Up to 20 bps acceptable" if status is Status.DECLINED else None
        decision = Decision(decided_by="rv.erik", decided_at=EARLIER, comment=comment)
    return PricingExceptionRequest(
        id=REQUEST_ID,
        application_id="MA-1006",
        requested_by="lead.fatima",
        status=status,
        revision=2,
        discount_bps=30,
        reason="Retention: competitor offer 30 bps below standard",
        created_at=CREATED,
        decision=decision,
    )


# Each cell is (decide, revise): None when the caller may, else the error the action would raise.
ALLOWED, FORBIDDEN, FOUR_EYES, INVALID_STATE = None, Forbidden, FourEyesViolation, InvalidState
COLUMNS = ("lead.fatima", "rm.bram", "rv.dana", "svc.mortgage")
TABLE = {
    Status.PENDING_REVIEW: [
        (FOUR_EYES, ALLOWED),
        (FORBIDDEN, FORBIDDEN),
        (ALLOWED, FORBIDDEN),
        (FORBIDDEN, FORBIDDEN),
    ],
    Status.DECLINED: [
        (FOUR_EYES, ALLOWED),
        (FORBIDDEN, FORBIDDEN),
        (INVALID_STATE, FORBIDDEN),
        (FORBIDDEN, FORBIDDEN),
    ],
    Status.APPROVED: [
        (FOUR_EYES, INVALID_STATE),
        (FORBIDDEN, FORBIDDEN),
        (INVALID_STATE, FORBIDDEN),
        (FORBIDDEN, FORBIDDEN),
    ],
}
CELLS = [
    pytest.param(status, USERS[user], cell, id=f"{status}-{user}")
    for status, row in TABLE.items()
    for user, cell in zip(COLUMNS, row, strict=True)
]


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(("status", "actor", "expected"), CELLS)
def test_permissions_say_who_may_act(status: Status, actor: Actor, expected: tuple[type | None, ...]) -> None:
    allowed = permissions(a_request(status), actor)
    assert list(allowed) == [Action.DECIDE, Action.REVISE]
    refusals = (allowed[Action.DECIDE], allowed[Action.REVISE])
    assert tuple(None if refusal is None else type(refusal) for refusal in refusals) == expected


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("status", "user"),
    [
        pytest.param(status, user, id=f"{status}-{user}")
        for status, user in itertools.product(Status, COLUMNS)
    ],
)
def test_permissions_never_disagree_with_the_actions(status: Status, user: str) -> None:
    request, actor = a_request(status), USERS[user]
    allowed = permissions(request, actor)
    attempts = {
        Action.DECIDE: lambda: decide(request, actor, request.revision, Verdict(Outcome.APPROVED), NOW),
        Action.REVISE: lambda: revise(request, actor, request.revision, Terms(35, "New terms"), NOW),
    }
    for action, attempt in attempts.items():
        refusal = allowed[action]
        if refusal is None:
            _, event = attempt()  # the action goes through, at the current revision
            assert event.actor_id == actor.id
        else:
            with pytest.raises(type(refusal)) as raised:
                attempt()
            assert str(raised.value) == str(refusal)


# Creating adds to the collection of requests, so the list answers it: relationship managers only.
# lead.fatima holds both roles, so she may.
@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "expected"),
    [
        pytest.param("lead.fatima", ALLOWED, id="both roles"),
        pytest.param("rm.bram", ALLOWED, id="relationship manager"),
        pytest.param("rv.dana", FORBIDDEN, id="reviewer"),
        pytest.param("svc.mortgage", FORBIDDEN, id="mortgage process"),
    ],
)
def test_only_relationship_managers_may_create(user: str, expected: type | None) -> None:
    allowed = collection_permissions(USERS[user])
    assert list(allowed) == [Action.CREATE]
    refusal = allowed[Action.CREATE]
    assert (None if refusal is None else type(refusal)) == expected


@pytest.mark.req(id="N5")
@pytest.mark.parametrize("user", COLUMNS)
def test_the_create_permission_never_disagrees_with_submit(user: str) -> None:
    actor = USERS[user]
    refusal = collection_permissions(actor)[Action.CREATE]

    def attempt() -> object:
        return submit(actor, "MA-1007", Terms(25, "New request"), REQUEST_ID, NOW)

    if refusal is None:
        created, _ = attempt()  # the create goes through
        assert created.requested_by == actor.id
    else:
        with pytest.raises(type(refusal)) as raised:
            attempt()
        assert str(raised.value) == str(refusal)


# The words of the two refusals the tests above never read. The page shows them as the reason, so they are
# pinned exactly (a mutation run found them untested).
@pytest.mark.req(id="N5")
def test_a_refused_create_says_why() -> None:
    with pytest.raises(Forbidden) as raised:
        submit(USERS["rv.dana"], "MA-1007", Terms(25, "New request"), REQUEST_ID, NOW)
    assert str(raised.value) == "Only a relationship manager can create a request."


@pytest.mark.req(id="N5")
def test_a_refused_read_says_why() -> None:
    """The mortgage process reads only approved discounts, never the requests themselves."""
    with pytest.raises(Forbidden) as raised:
        require_staff(USERS["svc.mortgage"])
    assert str(raised.value) == "Only relationship managers and reviewers can read requests."
