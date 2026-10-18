"""The requests and their history: hand-written SQL through text(), always with bound parameters.

Repositories receive a connection and never commit; the service owns the transaction (design doc 7.4). History
is only ever appended to: there is no function that updates or deletes an event.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Connection, RowMapping, text

from pricing_exceptions.domain.model import (
    Decision,
    EventType,
    PricingExceptionRequest,
    RequestEvent,
    RequestRecord,
    Status,
)

type Position = tuple[datetime, UUID]  # where a page of the list ends: the last row's (created_at, id)

# The create's insert. The conflict target is the one plain unique constraint, so a duplicate for the same
# application inserts nothing and returns no row; at READ COMMITTED it first waits for a create of the same
# application that is still in flight, then sees its outcome (design doc D3, 7.4).
INSERT_REQUEST = text("""
    INSERT INTO pricing_exception_requests
        (id, application_id, requested_by, status, revision_no, discount_bps, reason,
         decided_by, decided_at, decision_comment, created_at)
    VALUES
        (:id, :application_id, :requested_by, :status, :revision_no, :discount_bps, :reason,
         :decided_by, :decided_at, :decision_comment, :created_at)
    ON CONFLICT ON CONSTRAINT uq_request_application DO NOTHING
    RETURNING id
""")

APPEND_EVENT = text("""
    INSERT INTO pricing_exception_events
        (request_id, event_type, revision_no, actor_id, occurred_at, discount_bps, reason, comment)
    VALUES
        (:request_id, :event_type, :revision_no, :actor_id, :occurred_at, :discount_bps, :reason, :comment)
""")

# One statement, so the request and its history come from one snapshot. The LEFT JOIN keeps a request visible
# even if its history were missing, rather than turning it into a 404.
READ_REQUEST = text("""
    SELECT r.id, r.application_id, r.requested_by, r.status, r.revision_no, r.discount_bps, r.reason,
           r.decided_by, r.decided_at, r.decision_comment, r.created_at,
           e.event_type, e.revision_no AS event_revision_no, e.actor_id, e.occurred_at,
           e.discount_bps AS event_discount_bps, e.reason AS event_reason, e.comment
      FROM pricing_exception_requests AS r
      LEFT JOIN pricing_exception_events AS e ON e.request_id = r.id
     WHERE r.id = :id
     ORDER BY e.id
""")

FIND_FOR_APPLICATION = text(
    "SELECT id FROM pricing_exception_requests WHERE application_id = :application_id"
)

FIND_APPROVED = text("""
    SELECT r.id, r.application_id, r.requested_by, r.status, r.revision_no, r.discount_bps, r.reason,
           r.decided_by, r.decided_at, r.decision_comment, r.created_at
      FROM pricing_exception_requests AS r
     WHERE r.application_id = :application_id AND r.status = 'APPROVED'
""")

# The review queue, oldest first. Every page is one range scan of ix_request_status_created; the row-value
# comparison continues strictly after the last row of the previous page (design doc 5.6).
FIRST_PAGE = text("""
    SELECT r.id, r.application_id, r.requested_by, r.status, r.revision_no, r.discount_bps, r.reason,
           r.decided_by, r.decided_at, r.decision_comment, r.created_at
      FROM pricing_exception_requests AS r
     WHERE r.status = :status
     ORDER BY r.created_at, r.id
     LIMIT :limit
""")

NEXT_PAGE = text("""
    SELECT r.id, r.application_id, r.requested_by, r.status, r.revision_no, r.discount_bps, r.reason,
           r.decided_by, r.decided_at, r.decision_comment, r.created_at
      FROM pricing_exception_requests AS r
     WHERE r.status = :status AND (r.created_at, r.id) > (:after_at, :after_id)
     ORDER BY r.created_at, r.id
     LIMIT :limit
""")


def insert_request(conn: Connection, request: PricingExceptionRequest) -> bool:
    """Insert the request; False when its application already has one, so nothing was written."""
    decision = request.decision
    inserted = conn.execute(
        INSERT_REQUEST,
        {
            "id": request.id,
            "application_id": request.application_id,
            "requested_by": request.requested_by,
            "status": request.status,
            "revision_no": request.revision,
            "discount_bps": request.discount_bps,
            "reason": request.reason,
            "decided_by": decision.decided_by if decision else None,
            "decided_at": decision.decided_at if decision else None,
            "decision_comment": decision.comment if decision else None,
            "created_at": request.created_at,
        },
    ).first()
    return inserted is not None


def append_event(conn: Connection, event: RequestEvent) -> None:
    conn.execute(
        APPEND_EVENT,
        {
            "request_id": event.request_id,
            "event_type": event.event_type,
            "revision_no": event.revision,
            "actor_id": event.actor_id,
            "occurred_at": event.occurred_at,
            "discount_bps": event.discount_bps,
            "reason": event.reason,
            "comment": event.comment,
        },
    )


def read_request(conn: Connection, request_id: UUID) -> RequestRecord | None:
    """The request with its whole history, oldest event first, or None. Every response that shows a request
    is built from this one function (design doc 6.5)."""
    rows = conn.execute(READ_REQUEST, {"id": request_id}).mappings().all()
    if not rows:
        return None
    history = tuple(_event(request_id, row) for row in rows if row["event_type"] is not None)
    return RequestRecord(_request(rows[0]), history)


def find_id_for_application(conn: Connection, application_id: str) -> UUID | None:
    found: UUID | None = conn.execute(FIND_FOR_APPLICATION, {"application_id": application_id}).scalar()
    return found


def find_approved(conn: Connection, application_id: str) -> PricingExceptionRequest | None:
    row = conn.execute(FIND_APPROVED, {"application_id": application_id}).mappings().first()
    return _request(row) if row else None


def list_requests(
    conn: Connection, status: Status, after: Position | None, limit: int
) -> list[PricingExceptionRequest]:
    """At most `limit` requests in `status`, oldest first, strictly after `after` when it is given."""
    if after is None:
        rows = conn.execute(FIRST_PAGE, {"status": status, "limit": limit}).mappings().all()
    else:
        after_at, after_id = after
        parameters = {"status": status, "after_at": after_at, "after_id": after_id, "limit": limit}
        rows = conn.execute(NEXT_PAGE, parameters).mappings().all()
    return [_request(row) for row in rows]


def _request(row: RowMapping) -> PricingExceptionRequest:
    decision = (
        Decision(row["decided_by"], row["decided_at"], row["decision_comment"])
        if row["decided_by"] is not None
        else None
    )
    return PricingExceptionRequest(
        id=row["id"],
        application_id=row["application_id"],
        requested_by=row["requested_by"],
        status=Status(row["status"]),
        revision=row["revision_no"],
        discount_bps=row["discount_bps"],
        reason=row["reason"],
        created_at=row["created_at"],
        decision=decision,
    )


def _event(request_id: UUID, row: RowMapping) -> RequestEvent:
    return RequestEvent(
        request_id=request_id,
        event_type=EventType(row["event_type"]),
        revision=row["event_revision_no"],
        actor_id=row["actor_id"],
        occurred_at=row["occurred_at"],
        discount_bps=row["event_discount_bps"],
        reason=row["event_reason"],
        comment=row["comment"],
    )
