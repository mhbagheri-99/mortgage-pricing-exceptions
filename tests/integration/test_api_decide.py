"""Decide (FR-3), the guarantees around it (N1, N4, N5) and the stakeholder story (design doc D1, D4, 5.3,
6.5), through the real app on a real PostgreSQL.

The seeded requests (section 10): MA-1002 is rm.anna's, pending at r1; MA-1003 is rm.bram's, pending at r2;
MA-1004 is rm.bram's, approved at r1 by rv.dana; MA-1005 is rm.anna's, declined at r1 by rv.erik.
"""

import logging
from datetime import UTC, datetime

import pytest
from calls import (
    NEW_REASON,
    PROBLEMS,
    REQUESTS,
    TickingClock,
    as_user,
    create_body,
    decide_body,
    decisions,
    revise_body,
    revisions,
    seeded_id,
    stored,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from pricing_exceptions.api.app import create_app

SEEDED = (4, 7)  # requests and history events after the seed
CONFIRMED = "Savings transfer confirmed; matching the competing offer"  # MA-1003's reason at r2


class LockProbeClock:
    """A test clock that, each time it is read, asks a second connection whether the request's row is locked.
    FOR NO KEY UPDATE NOWAIT fails at once, with SQLSTATE 55P03, while another transaction holds the lock."""

    def __init__(self, engine: Engine, request_id: str) -> None:
        self.engine = engine
        self.request_id = request_id
        self.locked: list[bool] = []
        self.ticks = TickingClock(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        with self.engine.connect() as conn:  # leaving the block rolls back, so the probe never keeps a lock
            try:
                conn.execute(
                    text("SELECT 1 FROM pricing_exception_requests WHERE id = :id FOR NO KEY UPDATE NOWAIT"),
                    {"id": self.request_id},
                )
            except OperationalError as error:
                sqlstate = getattr(error.orig, "sqlstate", None)
            else:
                sqlstate = None  # the probe got the lock, so nobody else held it
        assert sqlstate in (None, "55P03"), f"the probe failed for another reason: {sqlstate}"
        self.locked.append(sqlstate == "55P03")
        return self.ticks()


def kinds(body: dict) -> list[tuple[str, int]]:
    """A response's history as (event type, revision) pairs, oldest first."""
    return [(event["event_type"], event["revision"]) for event in body["history"]]


def approved_discount(client: TestClient, application_id: str) -> dict:
    path = f"/api/v1/mortgage-applications/{application_id}/approved-discount"
    response = client.get(path, headers=as_user("svc.mortgage"))
    assert response.status_code == 200
    return dict(response.json())


@pytest.mark.req(id="N1")
def test_approval_binds_to_reviewed_revision(client: TestClient, engine: Engine) -> None:
    """rv.dana read MA-1003 at revision 1; it is now at revision 2. Her approval of revision 1 cannot attach
    to the terms of revision 2, and nothing is written. Approving revision 2 records who and when."""
    stale = client.post(decisions(1003), json=decide_body(1), headers=as_user("rv.dana"))
    assert stale.status_code == 409
    problem = stale.json()
    assert problem["type"] == PROBLEMS + "stale-revision"
    assert problem["sent_revision"] == 1
    assert (problem["current"]["status"], problem["current"]["revision"]) == ("PENDING_REVIEW", 2)
    assert stored(engine) == SEEDED

    approved = client.post(
        decisions(1003), json=decide_body(2, comment="Within retention policy"), headers=as_user("rv.dana")
    )
    assert approved.status_code == 200
    body = approved.json()
    assert (body["status"], body["revision"], body["discount_bps"]) == ("APPROVED", 2, 20)
    newest = body["history"][-1]
    assert newest == {
        "event_type": "APPROVED",
        "revision": 2,
        "actor": "rv.dana",
        "occurred_at": newest["occurred_at"],
        "discount_bps": 20,
        "reason": CONFIRMED,
        "comment": "Within retention policy",
    }
    assert body["latest_decision"] == {
        "outcome": "APPROVED",
        "revision": 2,
        "decided_by": "rv.dana",
        "decided_at": newest["occurred_at"],
        "comment": "Within retention policy",
    }
    assert newest["occurred_at"] > body["history"][-2]["occurred_at"]
    assert stored(engine) == (4, 8)


@pytest.mark.req(id="N5")
def test_requester_cannot_decide_own_request(client: TestClient, engine: Engine) -> None:
    """Holding the reviewer role is not enough: lead.fatima holds both roles and made the request. rm.anna has
    no reviewer role, so the role check answers first (design doc 5.5, P2 decision 1c)."""
    created = client.post(REQUESTS, json=create_body("MA-1006"), headers=as_user("lead.fatima"))
    assert created.status_code == 201
    request_id = created.json()["id"]

    own = client.post(decisions(request_id), json=decide_body(1), headers=as_user("lead.fatima"))
    assert own.status_code == 403
    assert own.json()["type"] == PROBLEMS + "four-eyes-violation"
    no_role = client.post(decisions(1002), json=decide_body(1), headers=as_user("rm.anna"))
    assert no_role.status_code == 403
    assert no_role.json()["type"] == PROBLEMS + "forbidden"
    assert stored(engine) == (5, 8)

    other = client.post(decisions(request_id), json=decide_body(1), headers=as_user("rv.dana"))
    assert other.status_code == 200
    assert other.json()["latest_decision"]["decided_by"] == "rv.dana"


@pytest.mark.req(id="N1")
@pytest.mark.parametrize(
    ("user", "path", "body"),
    [
        pytest.param("rm.bram", revisions(1004), revise_body(1), id="the requester revises"),
        pytest.param(
            "rv.erik", decisions(1004), decide_body(1, "DECLINED", "Too high"), id="a reviewer declines"
        ),
        pytest.param("rv.dana", decisions(1004), decide_body(1), id="the approver approves again"),
    ],
)
def test_approved_is_final(client: TestClient, engine: Engine, user: str, path: str, body: dict) -> None:
    """Approved never changes, so the discount the mortgage process read stays the discount (design doc
    D1)."""
    response = client.post(path, json=body, headers=as_user(user))
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == PROBLEMS + "invalid-state"
    assert problem["current"]["status"] == "APPROVED"
    assert problem["current"]["latest_decision"]["decided_by"] == "rv.dana"
    assert stored(engine) == SEEDED
    assert approved_discount(client, "MA-1004")["discount_bps"] == 15


@pytest.mark.req(id="FR-3")
def test_a_declined_request_is_resubmitted_before_it_is_decided_again(client: TestClient) -> None:
    """A decline is answered by the requester's new terms (P2 decision 1a). Every decision stays in history,
    so the brief's previous decisions all show (FR-5)."""
    again = client.post(decisions(1005), json=decide_body(1), headers=as_user("rv.dana"))
    assert again.status_code == 409
    assert again.json()["type"] == PROBLEMS + "invalid-state"

    resubmitted = client.post(revisions(1005), json=revise_body(1, 30), headers=as_user("rm.anna"))
    assert resubmitted.status_code == 200
    approved = client.post(decisions(1005), json=decide_body(2), headers=as_user("rv.dana"))
    assert approved.status_code == 200
    body = approved.json()
    assert kinds(body) == [("SUBMITTED", 1), ("DECLINED", 1), ("RESUBMITTED", 2), ("APPROVED", 2)]
    assert (body["latest_decision"]["outcome"], body["latest_decision"]["revision"]) == ("APPROVED", 2)
    assert approved_discount(client, "MA-1005")["discount_bps"] == 30


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        pytest.param({"revision": 1, "outcome": "DECLINED"}, "#/comment", id="decline without a comment"),
        pytest.param(decide_body(1, "DECLINED") | {"comment": None}, "#/comment", id="decline, comment null"),
        pytest.param(decide_body(1, "DECLINED", " \n"), "#/comment", id="decline, blank comment"),
        pytest.param(decide_body(1, "APPROVED", ""), "#/comment", id="approve, empty comment sent"),
        pytest.param(decide_body(1, "DECLINED", 5), "#/comment", id="comment as a number"),
        pytest.param(decide_body(1, "MAYBE"), "#/outcome", id="an outcome that does not exist"),
        pytest.param(decide_body(1, "approved"), "#/outcome", id="outcome in lower case"),
        pytest.param(decide_body("1"), "#/revision", id="revision as a string"),
        pytest.param(decide_body(0), "#/revision", id="revision 0"),
        pytest.param({"outcome": "APPROVED"}, "#/revision", id="no revision"),
        pytest.param({**decide_body(1), "decided_by": "rv.erik"}, "#/decided_by", id="decided_by, set by us"),
    ],
)
def test_the_decide_body_is_strict(client: TestClient, engine: Engine, body: dict, pointer: str) -> None:
    response = client.post(decisions(1002), json=body, headers=as_user("rv.dana"))
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation-failed"
    assert [error["pointer"] for error in problem["errors"]] == [pointer]
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("user", "path", "body", "status", "problem"),
    [
        pytest.param(
            None, decisions(1002), decide_body("1"), 401, "unauthenticated", id="identity before shape"
        ),
        pytest.param(
            "rm.anna", decisions(1002), decide_body("1"), 422, "validation-failed", id="shape before role"
        ),
        pytest.param(
            "rm.anna",
            decisions(1002),
            {"revision": 1, "outcome": "DECLINED"},
            422,
            "validation-failed",
            id="values before role",
        ),
        pytest.param(
            "svc.mortgage",
            decisions(seeded_id(9999)),
            decide_body(1),
            403,
            "forbidden",
            id="role before the lock",
        ),
        pytest.param(
            "rv.dana", decisions(seeded_id(9999)), decide_body(1), 404, "", id="the lock before status"
        ),
        pytest.param(
            "rv.erik", decisions(1004), decide_body(99), 409, "invalid-state", id="status before revision"
        ),
        pytest.param(
            "rv.erik",
            decisions(1004),
            {"revision": 1, "outcome": "DECLINED"},
            422,
            "validation-failed",
            id="values before status",
        ),
    ],
)
def test_decide_checks_run_in_order(
    client: TestClient, engine: Engine, user: str | None, path: str, body: dict, status: int, problem: str
) -> None:
    """Design doc 5.5 and P4 decision 1: identity (401), shape and values (422), the reviewer role (403), the
    lock (404), four-eyes (403), the status (409), then the revision (409). The first check that fails
    answers. A 404 has no problem type of its own: `about:blank`."""
    response = client.post(path, json=body, headers=as_user(user) if user else {})
    expected_type = PROBLEMS + problem if problem else "about:blank"
    assert (response.status_code, response.json()["type"]) == (status, expected_type)
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-5")
def test_write_response_matches_get(client: TestClient) -> None:
    """One read builds every response that shows a request (design doc 6.5): a write's answer is what a GET
    right after it shows, to the same caller, because the permissions in it are the caller's (P7)."""
    revised = client.post(revisions(1003), json=revise_body(2, 25), headers=as_user("rm.bram"))
    assert revised.status_code == 200
    assert revised.json() == client.get(f"{REQUESTS}/{seeded_id(1003)}", headers=as_user("rm.bram")).json()
    decided = client.post(
        decisions(1003), json=decide_body(3, "DECLINED", "Up to 20 bps"), headers=as_user("rv.dana")
    )
    assert decided.status_code == 200
    assert decided.json() == client.get(f"{REQUESTS}/{seeded_id(1003)}", headers=as_user("rv.dana")).json()
    assert kinds(decided.json()) == [("SUBMITTED", 1), ("AMENDED", 2), ("AMENDED", 3), ("DECLINED", 3)]
    assert decided.json()["latest_decision"]["comment"] == "Up to 20 bps"


@pytest.mark.req(id="N4")
def test_every_change_appends_exactly_one_event_in_order(client: TestClient, engine: Engine) -> None:
    """History is only ever added to: each change keeps every earlier event as it was and adds exactly one,
    and a refused change adds none. Times follow the order of history."""
    steps = [
        ("rm.anna", revisions(1002), revise_body(1, 25), 200),
        ("rv.dana", decisions(1002), decide_body(1), 409),  # stale: the request is at revision 2 now
        ("rv.dana", decisions(1002), decide_body(2, "DECLINED", "Up to 20 bps acceptable"), 200),
        ("rm.anna", revisions(1002), revise_body(2, 20), 200),
        ("rm.anna", revisions(1002), revise_body(3, 15), 200),
        ("rv.erik", decisions(1002), decide_body(4), 200),
        ("rm.anna", revisions(1002), revise_body(4, 10), 409),  # approved is final
    ]
    history = client.get(f"{REQUESTS}/{seeded_id(1002)}", headers=as_user("rv.dana")).json()["history"]
    events = stored(engine)[1]
    for user, path, body, status in steps:
        response = client.post(path, json=body, headers=as_user(user))
        assert response.status_code == status
        added = 1 if status == 200 else 0
        assert stored(engine)[1] == events + added
        now = client.get(f"{REQUESTS}/{seeded_id(1002)}", headers=as_user("rv.dana")).json()["history"]
        assert now[: len(history)] == history
        assert len(now) == len(history) + added
        history, events = now, events + added
    assert [(event["event_type"], event["revision"]) for event in history] == [
        ("SUBMITTED", 1),
        ("AMENDED", 2),
        ("DECLINED", 2),
        ("RESUBMITTED", 3),
        ("AMENDED", 4),
        ("APPROVED", 4),
    ]
    times = [event["occurred_at"] for event in history]
    assert times == sorted(times)
    assert len(set(times)) == len(times)


@pytest.mark.req(id="N4")
def test_a_change_and_its_event_commit_together_or_not_at_all(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """The row update and its event are one transaction. A stray AMENDED event already holds revision 2 of
    MA-1002, so the event of rm.anna's change breaks ux_event_one_revision_event: the call fails, and the
    row's own update must roll back with it."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO pricing_exception_events (request_id, event_type, revision_no, actor_id, "
                "occurred_at, discount_bps, reason) VALUES (:id, 'AMENDED', 2, 'rm.anna', "
                "'2026-10-15T09:05:00Z', 25, 'A stray event written around the service')"
            ),
            {"id": seeded_id(1002)},
        )
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    response = client.post(revisions(1002), json=revise_body(1, 30), headers=as_user("rm.anna"))
    assert response.status_code == 500
    assert response.json() == {"type": "about:blank", "title": "Internal Server Error", "status": 500}
    # The event's insert is what failed (23505, a unique violation), and no line claims the change happened.
    messages = [(record.levelno, record.getMessage()) for record in caplog.records]
    assert [message for level, message in messages if level >= logging.ERROR] == [
        "unexpected database error: action=revise IntegrityError sqlstate=23505"
    ]
    assert [message for level, message in messages if message.startswith("state change")] == []
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT revision_no, discount_bps FROM pricing_exception_requests WHERE id = :id"),
            {"id": seeded_id(1002)},
        ).one()
    assert tuple(row) == (1, 20)
    assert stored(engine) == (4, 8)


def test_the_clock_is_read_once_under_the_row_lock(engine: Engine) -> None:
    """P4 decision 3: a change reads the clock once, while it holds the request's row lock, so the times in
    history follow the order in which writers took the lock (P4 review finding 2)."""
    clock = LockProbeClock(engine, seeded_id(1003))
    client = TestClient(create_app(engine, clock, demo_identity=True))
    revised = client.post(revisions(1003), json=revise_body(2), headers=as_user("rm.bram"))
    decided = client.post(decisions(1003), json=decide_body(3), headers=as_user("rv.dana"))
    assert (revised.status_code, decided.status_code) == (200, 200)
    assert clock.locked == [True, True]


@pytest.mark.req(id="FR-2")
@pytest.mark.req(id="FR-4")
def test_the_stakeholder_story(client: TestClient) -> None:
    """Both stakeholders, in one story (design doc D1, the section 10 demo): the requester changes the
    discount after submitting it, and the approval applies to exactly the revision that was reviewed. The
    mortgage process then reads the approved 30 bps at revision 2."""
    fatima, dana = as_user("lead.fatima"), as_user("rv.dana")
    created = client.post(REQUESTS, json=create_body("MA-1006", 25), headers=fatima)
    assert created.status_code == 201
    request_id = created.json()["id"]
    seen = client.get(f"{REQUESTS}/{request_id}", headers=dana).json()
    assert (seen["revision"], seen["discount_bps"]) == (1, 25)

    amended = client.post(revisions(request_id), json=revise_body(1, 30), headers=fatima)
    assert amended.status_code == 200
    assert (amended.json()["revision"], amended.json()["discount_bps"]) == (2, 30)

    stale = client.post(decisions(request_id), json=decide_body(seen["revision"]), headers=dana)
    assert stale.status_code == 409
    assert (stale.json()["type"], stale.json()["sent_revision"]) == (PROBLEMS + "stale-revision", 1)
    assert stale.json()["current"]["discount_bps"] == 30

    approved = client.post(decisions(request_id), json=decide_body(2), headers=dana)
    assert approved.status_code == 200
    decision = approved.json()["latest_decision"]
    assert (decision["outcome"], decision["revision"], decision["decided_by"]) == ("APPROVED", 2, "rv.dana")

    again = client.post(revisions(request_id), json=revise_body(2, 35, NEW_REASON), headers=fatima)
    assert again.status_code == 409
    assert again.json()["type"] == PROBLEMS + "invalid-state"

    assert approved_discount(client, "MA-1006") == {
        "application_id": "MA-1006",
        "status": "APPROVED",
        "discount_bps": 30,
        "request_id": request_id,
        "revision": 2,
        "approved_by": "rv.dana",
        "approved_at": decision["decided_at"],
    }
    history = client.get(f"{REQUESTS}/{request_id}", headers=dana).json()
    assert kinds(history) == [("SUBMITTED", 1), ("AMENDED", 2), ("APPROVED", 2)]
