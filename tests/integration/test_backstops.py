"""Database backstops (design doc 4.2 and 7.4): each one refuses a bad row even when the code is bypassed.

P5 adds the rest; this one came forward in P1, when the review found the reason check let tabs through.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from pricing_exceptions.db.seed import seed

INSERT_REQUEST = text(
    "INSERT INTO pricing_exception_requests "
    "(id, application_id, requested_by, status, revision_no, discount_bps, reason, created_at) "
    "VALUES (:id, 'MA-1008', 'rm.anna', 'PENDING_REVIEW', 1, 25, :reason, :created_at)"
)


@pytest.mark.parametrize("reason", [" ", "\t", "\n", " \t\r\n "], ids=["space", "tab", "newline", "mixed"])
def test_a_blank_reason_is_refused_by_the_database(engine: Engine, reason: str) -> None:
    seed(engine)
    with pytest.raises(IntegrityError, match="ck_request_reason"), engine.begin() as conn:
        conn.execute(INSERT_REQUEST, {"id": uuid.uuid4(), "reason": reason, "created_at": datetime.now(UTC)})
