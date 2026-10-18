"""Database backstops (docs/architecture.md, "Data model"): each one refuses a bad row even when the code
is bypassed.

Every test writes straight into the tables with raw SQL, past the domain and the service, and expects the
named constraint to refuse it, so the domain's copy of a rule and the database's copy cannot drift apart. The
seeded requests: MA-1002 is rm.anna's, pending at r1; MA-1003 is rm.bram's, pending at r2;
MA-1004 is rm.bram's, approved at r1 by rv.dana; MA-1005 is rm.anna's, declined at r1 by rv.erik.
"""

import uuid
from datetime import UTC, datetime

import pytest
from calls import REASON, seeded_id
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError

from pricing_exceptions.db import requests
from pricing_exceptions.db.seed import seed

AT = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)

INSERT_REQUEST = text(
    "INSERT INTO pricing_exception_requests "
    "(id, application_id, requested_by, status, revision_no, discount_bps, reason, decided_by, decided_at, "
    "decision_comment, created_at) "
    "VALUES (:id, :application_id, :requested_by, :status, :revision_no, :discount_bps, :reason, "
    ":decided_by, :decided_at, :decision_comment, :created_at)"
)
# A valid new request on the free application MA-1008, until a test changes one column.
REQUEST = {
    "application_id": "MA-1008",
    "requested_by": "rm.anna",
    "status": "PENDING_REVIEW",
    "revision_no": 1,
    "discount_bps": 25,
    "reason": REASON,
    "decided_by": None,
    "decided_at": None,
    "decision_comment": None,
    "created_at": AT,
}

INSERT_EVENT = text(
    "INSERT INTO pricing_exception_events "
    "(request_id, event_type, revision_no, actor_id, occurred_at, discount_bps, reason, comment) "
    "VALUES (:request_id, :event_type, :revision_no, :actor_id, :occurred_at, :discount_bps, :reason, "
    ":comment)"
)
# A valid event that no seeded history holds yet (AMENDED at revision 9), until a test changes one column.
EVENT = {
    "event_type": "AMENDED",
    "revision_no": 9,
    "actor_id": "rm.anna",
    "occurred_at": AT,
    "discount_bps": 25,
    "reason": REASON,
    "comment": None,
}

# Every column a decision sets. A test fills all five, so the row breaks only the rule it means to break.
SET_DECISION = text(
    "UPDATE pricing_exception_requests SET status = :status, revision_no = :revision_no, "
    "decided_by = :decided_by, decided_at = :decided_at, decision_comment = :decision_comment WHERE id = :id"
)


def insert_request(conn: Connection, **changes: object) -> None:
    conn.execute(INSERT_REQUEST, {**REQUEST, "id": uuid.uuid4(), **changes})


def insert_event(conn: Connection, request_number: int, **changes: object) -> None:
    """Append an event straight to a seeded request's history."""
    conn.execute(INSERT_EVENT, {**EVENT, "request_id": seeded_id(request_number), **changes})


@pytest.mark.req(id="N1")
def test_an_application_holds_at_most_one_request(engine: Engine) -> None:
    """A plain insert of a second request for MA-1002, without the create's ON CONFLICT, is refused."""
    with pytest.raises(IntegrityError, match="uq_request_application"), engine.begin() as conn:
        insert_request(conn, application_id="MA-1002", requested_by="rm.bram")


@pytest.mark.req(id="N2")
def test_the_create_conflict_target_stays_a_plain_unique_constraint(engine: Engine) -> None:
    """ADR 0002: the create's ON CONFLICT names uq_request_application, and the replay is
    correct only while that is one unique constraint on the application id alone, over every row. A partial
    index, one with a WHERE clause, would let a second request in for every row it leaves out."""
    with engine.connect() as conn:
        found = conn.execute(
            text(
                "SELECT c.contype, pg_get_constraintdef(c.oid), i.indpred IS NULL "
                "FROM pg_constraint AS c JOIN pg_index AS i ON i.indexrelid = c.conindid "
                "WHERE c.conrelid = 'pricing_exception_requests'::regclass "
                "AND c.conname = 'uq_request_application'"
            )
        ).all()
    assert [tuple(row) for row in found] == [("u", "UNIQUE (application_id)", True)]
    # And the create's insert still names it as its conflict target.
    create_insert = " ".join(str(requests.INSERT_REQUEST).split())
    assert "ON CONFLICT ON CONSTRAINT uq_request_application DO NOTHING" in create_insert


@pytest.mark.req(id="N1")
@pytest.mark.parametrize("event_type", ["APPROVED", "DECLINED"])
def test_a_revision_takes_at_most_one_decision(engine: Engine, event_type: str) -> None:
    """MA-1004 is approved at revision 1. A second decision on revision 1, written straight into its history,
    is refused, even by a future code path that forgets the row lock."""
    with pytest.raises(IntegrityError, match="ux_event_one_decision_per_revision"), engine.begin() as conn:
        insert_event(
            conn, 1004, event_type=event_type, revision_no=1, actor_id="rv.erik", comment="Up to 10 bps"
        )


@pytest.mark.req(id="N3")
@pytest.mark.parametrize(("event_type", "revision"), [("SUBMITTED", 1), ("AMENDED", 2), ("RESUBMITTED", 2)])
def test_a_revision_number_is_created_once(engine: Engine, event_type: str, revision: int) -> None:
    """MA-1003's history holds SUBMITTED at revision 1 and AMENDED at revision 2. Creating either revision
    again, with any of the three events that create one, is refused: two writers can never both create
    revision n + 1, so a lost update fails loudly instead of rewriting history."""
    with pytest.raises(IntegrityError, match="ux_event_one_revision_event"), engine.begin() as conn:
        insert_event(conn, 1003, event_type=event_type, revision_no=revision, actor_id="rm.bram")


@pytest.mark.req(id="N5")
def test_a_requester_never_decides_their_own_request(engine: Engine) -> None:
    """rm.anna made MA-1002. Recording her as its approver, straight in the table, is refused."""
    with pytest.raises(IntegrityError, match="ck_request_four_eyes"), engine.begin() as conn:
        conn.execute(
            SET_DECISION,
            {
                "id": seeded_id(1002),
                "status": "APPROVED",
                "revision_no": 1,
                "decided_by": "rm.anna",
                "decided_at": AT,
                "decision_comment": None,
            },
        )


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("request_number", "status", "revision", "decided_by", "decided_at", "comment"),
    [
        pytest.param(
            1005,
            "PENDING_REVIEW",
            2,
            "rv.erik",
            AT,
            "Up to 30 bps acceptable",
            id="a resubmission that forgets to clear the decline",
        ),
        pytest.param(1002, "PENDING_REVIEW", 1, None, None, "Looks fine", id="pending with a comment"),
        pytest.param(1002, "APPROVED", 1, None, AT, None, id="approved by nobody"),
        pytest.param(1002, "APPROVED", 1, "rv.dana", None, None, id="approved at no time"),
        pytest.param(1002, "DECLINED", 1, "rv.dana", AT, None, id="declined without a comment"),
    ],
)
def test_the_decision_columns_match_the_status(
    engine: Engine,
    request_number: int,
    status: str,
    revision: int,
    decided_by: str | None,
    decided_at: datetime | None,
    comment: str | None,
) -> None:
    """A decided request records who decided and when, and a decline its comment; a pending request records no
    decision at all. "Approved by nobody" is the case four-eyes alone would let through, because a comparison
    with NULL never fails a CHECK."""
    with pytest.raises(IntegrityError, match="ck_request_decision_matches_status"), engine.begin() as conn:
        conn.execute(
            SET_DECISION,
            {
                "id": seeded_id(request_number),
                "status": status,
                "revision_no": revision,
                "decided_by": decided_by,
                "decided_at": decided_at,
                "decision_comment": comment,
            },
        )


@pytest.mark.parametrize("reason", [" ", "\t", "\n", " \t\r\n "], ids=["space", "tab", "newline", "mixed"])
def test_a_blank_reason_is_refused_by_the_database(engine: Engine, reason: str) -> None:
    seed(engine)
    with pytest.raises(IntegrityError, match="ck_request_reason"), engine.begin() as conn:
        insert_request(conn, reason=reason)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        pytest.param("discount_bps", 0, "ck_request_discount", id="discount 0"),
        pytest.param("discount_bps", -25, "ck_request_discount", id="discount below 0"),
        pytest.param("revision_no", 0, "ck_request_revision", id="revision 0"),
        # PostgreSQL checks constraints in name order, and the decision check also lists the three statuses,
        # so it refuses an unknown status before ck_request_status is reached.
        pytest.param("status", "WITHDRAWN", "ck_request_decision_matches_status", id="unknown status"),
        pytest.param("status", "approved", "ck_request_decision_matches_status", id="status in lower case"),
    ],
)
def test_a_request_of_the_wrong_shape_is_refused(
    engine: Engine, column: str, value: object, constraint: str
) -> None:
    """The shape checks: the policy limits (1 to 100 bps, text up to 1,000 characters) live in the domain
    only, so the database refuses only values no policy could allow."""
    with pytest.raises(IntegrityError, match=constraint), engine.begin() as conn:
        insert_request(conn, **{column: value})


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        pytest.param("discount_bps", 0, "ck_event_discount", id="discount 0"),
        pytest.param("revision_no", 0, "ck_event_revision", id="revision 0"),
        pytest.param("event_type", "WITHDRAWN", "ck_event_type", id="an event type that does not exist"),
        pytest.param("reason", "\t", "ck_event_reason", id="a blank reason"),
    ],
)
def test_an_event_of_the_wrong_shape_is_refused(
    engine: Engine, column: str, value: object, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint), engine.begin() as conn:
        insert_event(conn, 1002, **{column: value})
