"""Races and time limits (design doc 7.3, 7.4 and 7.10), through the real app on a real PostgreSQL.

The deterministic tests follow 7.10. Connection A, the test's own transaction, takes the row lock or inserts
the duplicate, and records its process id. A thread sends the real API call, which blocks behind A. The
barrier waits until PostgreSQL shows that call waiting on A. Then the test makes the winning change in A,
commits or rolls back, and asserts the exact answer the waiting call got. A takes its lock with its own SQL,
so taking the lock out of the service (the P5 red run) cannot take it out of A as well.

The two 8-thread bursts at the end are smoke tests: they assert counts only, never timing, and carry no
requirement id. The seeded requests (section 10): MA-1002 is rm.anna's, pending at r1 at 20 bps; MA-1001 is
free.
"""

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID, uuid4

import pytest
from calls import (
    NEW_REASON,
    PROBLEMS,
    REASON,
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
from races import wait_for, wait_until_blocked
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db import requests
from pricing_exceptions.domain import rules
from pricing_exceptions.domain.model import Actor, Outcome, PricingExceptionRequest, RequestEvent, Role

SEEDED = (4, 7)  # requests and history events after the seed
RESULT_SECONDS = 10  # how long a test waits for a thread's answer: past the 8 s budget, so N9 fails first
BUDGET_SECONDS = 8  # N9: no call takes longer (design doc 7.3)
POOL_CONNECTIONS = 15  # every connection the engine's pool can give: pool_size 5 plus max_overflow 10

ANNA = Actor("rm.anna", frozenset({Role.RELATIONSHIP_MANAGER}))
DANA = Actor("rv.dana", frozenset({Role.REVIEWER}))
ERIK = Actor("rv.erik", frozenset({Role.REVIEWER}))
MA_1002 = UUID(seeded_id(1002))
TERMS = rules.Terms(25, REASON)  # the terms create_body() sends
APPROVE = rules.Verdict(Outcome.APPROVED)
DECLINE = "Up to 15 bps acceptable"

LOCK = text("SELECT id FROM pricing_exception_requests WHERE id = :id FOR NO KEY UPDATE")
PROBE = text("SELECT 1 FROM pricing_exception_requests WHERE id = :id FOR NO KEY UPDATE NOWAIT")

type Rule = Callable[[PricingExceptionRequest, datetime], tuple[PricingExceptionRequest, RequestEvent]]


class ConnectionA:
    """The test's own transaction (design doc 7.10, step 1). The test decides when it commits or rolls back.
    Leaving the `with` block rolls back whatever is still open, so a failing test never leaves a call blocked
    behind A."""

    def __init__(self, engine: Engine) -> None:
        self.conn = engine.connect()
        self.transaction = self.conn.begin()
        self.pid: int = self.conn.execute(text("SELECT pg_backend_pid()")).scalar_one()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, error: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self.transaction.is_active:
            self.transaction.rollback()
        self.conn.close()

    def lock(self, request_id: UUID) -> None:
        self.conn.execute(LOCK, {"id": request_id})

    def change(self, request_id: UUID, rule: Rule, now: datetime) -> None:
        """The winning change, made as the service makes it: the pure rule on the request as A holds it, then
        the row and its one event, written with the repository's own functions."""
        record = requests.read_request(self.conn, request_id)
        assert record is not None
        changed, event = rule(record.request, now)
        requests.update_request(self.conn, changed)
        requests.append_event(self.conn, event)

    def submit(self, actor: Actor, application_id: str, terms: rules.Terms, now: datetime) -> UUID:
        """The first of two identical creates: the service's own rule and insert, not yet committed."""
        request, event = rules.submit(actor, application_id, terms, uuid4(), now)
        assert requests.insert_request(self.conn, request)
        requests.append_event(self.conn, event)
        return request.id

    def commit(self) -> None:
        self.transaction.commit()

    def rollback(self) -> None:
        self.transaction.rollback()


def call(client: TestClient, path: str, body: dict, user: str) -> Any:
    """One API call on its own TestClient, so calls from several threads share nothing but the app."""
    return TestClient(client.app).post(path, json=body, headers=as_user(user))


def history(engine: Engine, request_id: UUID) -> list[tuple[str, int, str]]:
    """The request's history as (event type, revision, actor), oldest first, read straight from the table."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT event_type, revision_no, actor_id FROM pricing_exception_events "
                "WHERE request_id = :id ORDER BY id"
            ),
            {"id": request_id},
        ).all()
    return [(row[0], row[1], row[2]) for row in rows]


def warning_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("pricing_exceptions") and record.levelno == logging.WARNING
    ]


@pytest.mark.req(id="N2")
def test_identical_creates_at_the_same_moment_give_one_request(
    client: TestClient, engine: Engine, clock: TickingClock
) -> None:
    """Design doc D3: rm.anna's create waits on A's identical insert of MA-1001, then sees it commit. It
    inserts nothing and replays A's request with 201."""
    with ThreadPoolExecutor(1) as pool, ConnectionA(engine) as a:
        first = a.submit(ANNA, "MA-1001", TERMS, clock())
        waiting = pool.submit(call, client, REQUESTS, create_body(), "rm.anna")
        wait_until_blocked(engine, a.pid)
        a.commit()
        response = waiting.result(timeout=RESULT_SECONDS)
    assert (response.status_code, response.headers.get("Idempotent-Replayed")) == (201, "true")
    assert response.json()["id"] == str(first)
    assert stored(engine) == (5, 8)


@pytest.mark.req(id="N2")
def test_a_create_waiting_on_one_that_rolls_back_creates_its_own(
    client: TestClient, engine: Engine, clock: TickingClock
) -> None:
    """Design doc 7.4: when the first create rolls back, the waiting one proceeds normally and inserts its own
    request: 201, not a replay, and still one request for MA-1001."""
    with ThreadPoolExecutor(1) as pool, ConnectionA(engine) as a:
        first = a.submit(ANNA, "MA-1001", TERMS, clock())
        waiting = pool.submit(call, client, REQUESTS, create_body(), "rm.anna")
        wait_until_blocked(engine, a.pid)
        a.rollback()
        response = waiting.result(timeout=RESULT_SECONDS)
    assert response.status_code == 201
    assert "Idempotent-Replayed" not in response.headers
    assert response.json()["id"] != str(first)
    assert stored(engine) == (5, 8)


@pytest.mark.req(id="N3")
def test_two_reviewers_decide_at_once(client: TestClient, engine: Engine, clock: TickingClock) -> None:
    """Design doc 7.4: rv.dana's approval holds MA-1002's lock (A) while rv.erik's decline waits. When A
    commits, the decline runs the same rule on the newest row: 409 invalid-state showing rv.dana's decision,
    and exactly one decision is written."""
    with ThreadPoolExecutor(1) as pool, ConnectionA(engine) as a:
        a.lock(MA_1002)
        waiting = pool.submit(call, client, decisions(1002), decide_body(1, "DECLINED", DECLINE), "rv.erik")
        wait_until_blocked(engine, a.pid)
        a.change(MA_1002, lambda request, now: rules.decide(request, DANA, 1, APPROVE, now), clock())
        a.commit()
        response = waiting.result(timeout=RESULT_SECONDS)
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == PROBLEMS + "invalid-state"
    decision = problem["current"]["latest_decision"]
    assert (problem["current"]["status"], decision["outcome"], decision["decided_by"]) == (
        "APPROVED",
        "APPROVED",
        "rv.dana",
    )
    assert history(engine, MA_1002) == [("SUBMITTED", 1, "rm.anna"), ("APPROVED", 1, "rv.dana")]


@pytest.mark.req(id="N3")
def test_an_approval_waiting_on_an_amendment_is_stale(
    client: TestClient, engine: Engine, clock: TickingClock
) -> None:
    """Design doc 7.4: rm.anna's change to revision 2 holds the lock (A) while rv.dana approves revision 1,
    the one she read. The approval waits, then finds revision 2: 409 stale-revision, and no decision is
    written."""
    amend = rules.Terms(30, NEW_REASON)
    with ThreadPoolExecutor(1) as pool, ConnectionA(engine) as a:
        a.lock(MA_1002)
        waiting = pool.submit(call, client, decisions(1002), decide_body(1), "rv.dana")
        wait_until_blocked(engine, a.pid)
        a.change(MA_1002, lambda request, now: rules.revise(request, ANNA, 1, amend, now), clock())
        a.commit()
        response = waiting.result(timeout=RESULT_SECONDS)
    assert response.status_code == 409
    problem = response.json()
    assert (problem["type"], problem["sent_revision"]) == (PROBLEMS + "stale-revision", 1)
    assert (problem["current"]["revision"], problem["current"]["discount_bps"]) == (2, 30)
    assert history(engine, MA_1002) == [("SUBMITTED", 1, "rm.anna"), ("AMENDED", 2, "rm.anna")]


@pytest.mark.req(id="N3")
def test_an_amendment_waiting_on_an_approval_is_refused(
    client: TestClient, engine: Engine, clock: TickingClock
) -> None:
    """Design doc 7.4: rv.dana's approval of revision 1 holds the lock (A) while rm.anna changes revision 1.
    The change waits, then finds the request approved: 409 invalid-state. The approval stays bound to revision
    1 at 20 bps, which is what the mortgage process reads."""
    with ThreadPoolExecutor(1) as pool, ConnectionA(engine) as a:
        a.lock(MA_1002)
        waiting = pool.submit(call, client, revisions(1002), revise_body(1, 30), "rm.anna")
        wait_until_blocked(engine, a.pid)
        a.change(MA_1002, lambda request, now: rules.decide(request, DANA, 1, APPROVE, now), clock())
        a.commit()
        response = waiting.result(timeout=RESULT_SECONDS)
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == PROBLEMS + "invalid-state"
    assert (problem["current"]["status"], problem["current"]["revision"]) == ("APPROVED", 1)
    approved = client.get(
        "/api/v1/mortgage-applications/MA-1002/approved-discount", headers=as_user("svc.mortgage")
    ).json()
    assert (approved["discount_bps"], approved["revision"], approved["approved_by"]) == (20, 1, "rv.dana")
    assert history(engine, MA_1002) == [("SUBMITTED", 1, "rm.anna"), ("APPROVED", 1, "rv.dana")]


@pytest.mark.req(id="N3")
def test_a_change_waiting_on_a_decline_lands_as_a_resubmission(
    client: TestClient, engine: Engine, clock: TickingClock
) -> None:
    """Design doc 7.4 and P4 decision 3: rv.erik's decline holds the lock (A) while rm.anna, still typing,
    sends a change to revision 1. Her change waits, then lands on the declined request as a resubmission at
    revision 2, and the decline stays in history. A read the clock after her call had started waiting; she
    reads it only once she holds the lock, so her time is later than the decline's."""
    decline = rules.Verdict(Outcome.DECLINED, DECLINE)
    with ThreadPoolExecutor(1) as pool, ConnectionA(engine) as a:
        a.lock(MA_1002)
        waiting = pool.submit(call, client, revisions(1002), revise_body(1, 15), "rm.anna")
        wait_until_blocked(engine, a.pid)
        a.change(MA_1002, lambda request, now: rules.decide(request, ERIK, 1, decline, now), clock())
        a.commit()
        response = waiting.result(timeout=RESULT_SECONDS)
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["revision"], body["discount_bps"]) == ("PENDING_REVIEW", 2, 15)
    events = [(event["event_type"], event["revision"], event["actor"]) for event in body["history"]]
    assert events == [("SUBMITTED", 1, "rm.anna"), ("DECLINED", 1, "rv.erik"), ("RESUBMITTED", 2, "rm.anna")]
    declined, resubmitted = (event["occurred_at"] for event in body["history"][1:])
    assert resubmitted > declined


@pytest.mark.req(id="N8")
@pytest.mark.req(id="N9")
@pytest.mark.parametrize(
    ("hold", "user", "path", "body", "action"),
    [
        pytest.param(
            lambda a, now: a.lock(MA_1002),
            "rv.erik",
            decisions(1002),
            decide_body(1),
            "decide",
            id="a decision waits on a row lock",
        ),
        pytest.param(
            lambda a, now: a.submit(ANNA, "MA-1001", TERMS, now),
            "rm.anna",
            REQUESTS,
            create_body(),
            "create",
            id="a create waits on an uncommitted duplicate",
        ),
    ],
)
def test_a_wait_past_lock_timeout_is_503(
    client: TestClient,
    engine: Engine,
    clock: TickingClock,
    caplog: pytest.LogCaptureFixture,
    hold: Callable[[ConnectionA, datetime], object],
    user: str,
    path: str,
    body: dict,
    action: str,
) -> None:
    """Design doc 7.3: a call that waits more than 2 s for a lock (lock_timeout) gives up with SQLSTATE 55P03
    and answers 503 with Retry-After, well inside the 8 s budget. Nothing is written."""
    with ConnectionA(engine) as a:
        hold(a, clock())
        caplog.clear()
        caplog.set_level(logging.INFO, logger="pricing_exceptions")
        started = time.monotonic()
        response = client.post(path, json=body, headers=as_user(user))
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["type"] == PROBLEMS + "temporarily-unavailable"
    assert warning_lines(caplog) == [f"database unavailable: action={action} OperationalError sqlstate=55P03"]
    assert elapsed < BUDGET_SECONDS, f"took {elapsed:.1f} s"
    assert stored(engine) == SEEDED


@pytest.mark.req(id="N8")
@pytest.mark.req(id="N9")
def test_a_wait_for_a_pooled_connection_past_pool_timeout_is_503(
    client: TestClient, engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Design doc 7.3: with every connection the pool can give in use, a write waits 3 s (pool_timeout) for
    one, gives up with the pool's TimeoutError and is refused with 503 and Retry-After, well inside the 8 s
    budget, with nothing written. The connections are held in an ExitStack, so a failing test still hands them
    all back to the next test."""
    with ExitStack() as held:
        for _ in range(POOL_CONNECTIONS):
            held.enter_context(engine.connect())
        caplog.clear()
        caplog.set_level(logging.INFO, logger="pricing_exceptions")
        started = time.monotonic()
        response = client.post(decisions(1002), json=decide_body(1), headers=as_user("rv.dana"))
        elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["type"] == PROBLEMS + "temporarily-unavailable"
    assert warning_lines(caplog) == ["database unavailable: action=decide TimeoutError sqlstate=-"]
    assert elapsed < BUDGET_SECONDS, f"took {elapsed:.1f} s"
    assert stored(engine) == SEEDED


class StuckClock:
    """A worker stuck inside its transaction. The service reads the clock while it holds the row lock, so this
    clock first notes that the row is locked, then returns only once the row is free again. That happens only
    when PostgreSQL ends the transaction holding it, at 5 s (transaction_timeout)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.locked_when_read: bool | None = None

    def __call__(self) -> datetime:
        self.locked_when_read = not self.row_is_free()
        wait_for(self.row_is_free, "PostgreSQL to end the stuck transaction", deadline=BUDGET_SECONDS)
        return datetime(2026, 10, 18, 9, 0, tzinfo=UTC)

    def row_is_free(self) -> bool:
        with self.engine.connect() as conn:  # leaving the block rolls back, so the probe never keeps the lock
            try:
                conn.execute(PROBE, {"id": MA_1002})
            except OperationalError as error:
                if getattr(error.orig, "sqlstate", None) != "55P03":
                    raise
                return False
        return True


@pytest.mark.req(id="N8")
@pytest.mark.req(id="N9")
def test_a_transaction_past_transaction_timeout_is_503(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    """Design doc 7.3: the server ends a transaction that runs past 5 s (transaction_timeout), which frees the
    row for every other writer. The stuck call then answers 503 with Retry-After, inside the 8 s budget, and
    nothing is written."""
    clock = StuckClock(engine)
    client = TestClient(create_app(engine, clock, demo_identity=True))
    caplog.clear()
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    started = time.monotonic()
    response = client.post(decisions(1002), json=decide_body(1), headers=as_user("rv.erik"))
    elapsed = time.monotonic() - started
    assert clock.locked_when_read is True
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    lines = warning_lines(caplog)
    assert len(lines) == 1
    assert lines[0].startswith("database unavailable: action=decide ")
    assert elapsed < BUDGET_SECONDS, f"took {elapsed:.1f} s"
    assert stored(engine) == SEEDED
    assert history(engine, MA_1002) == [("SUBMITTED", 1, "rm.anna")]


def all_at_once(client: TestClient, sends: list[tuple[str, dict, str]]) -> list[Any]:
    """Send each call from its own thread. A threading.Barrier releases them together: a start line for the
    threads, not a wait for anything in the database."""
    start = threading.Barrier(len(sends))

    def send(path: str, body: dict, user: str) -> Any:
        start.wait(timeout=RESULT_SECONDS)
        return call(client, path, body, user)

    with ThreadPoolExecutor(len(sends)) as pool:
        futures = [pool.submit(send, *one) for one in sends]
        return [future.result(timeout=RESULT_SECONDS) for future in futures]


def test_smoke_eight_identical_creates_at_once(client: TestClient, engine: Engine) -> None:
    """A smoke test, not proof (the deterministic create test above is the proof): eight identical creates
    sent together give eight 201s for one request, seven of them replays. Counts only, never timing."""
    responses = all_at_once(client, [(REQUESTS, create_body(), "rm.anna")] * 8)
    assert [response.status_code for response in responses] == [201] * 8
    assert len({response.json()["id"] for response in responses}) == 1
    assert sum(response.headers.get("Idempotent-Replayed") == "true" for response in responses) == 7
    assert stored(engine) == (5, 8)


def test_smoke_eight_decisions_at_once(client: TestClient, engine: Engine) -> None:
    """A smoke test: eight decisions on revision 1 of MA-1002 sent together, approvals and declines from three
    reviewers. One wins with 200; the other seven get 409 invalid-state showing the winner's decision; exactly
    one decision is written. Counts only, never timing."""
    reviewers = (["rv.dana", "rv.erik", "lead.fatima"] * 3)[:8]
    bodies = [decide_body(1) if n % 2 else decide_body(1, "DECLINED", DECLINE) for n in range(8)]
    responses = all_at_once(client, [(decisions(1002), b, u) for b, u in zip(bodies, reviewers, strict=True)])
    winners = [response.json() for response in responses if response.status_code == 200]
    assert len(winners) == 1
    winner = winners[0]["latest_decision"]
    losers = [response.json() for response in responses if response.status_code != 200]
    refused = (409, PROBLEMS + "invalid-state")
    assert [(problem["status"], problem["type"]) for problem in losers] == [refused] * 7
    assert [problem["current"]["latest_decision"] for problem in losers] == [winner] * 7
    decided = [event for event in history(engine, MA_1002) if event[0] in ("APPROVED", "DECLINED")]
    assert decided == [(winner["outcome"], 1, winner["decided_by"])]
