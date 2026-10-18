"""Write use cases: one transaction each, opened here and nowhere else (design doc 7.4)."""

import logging
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine

from pricing_exceptions.db import reference, requests
from pricing_exceptions.domain import rules
from pricing_exceptions.domain.errors import ApplicationNotFound, RequestExists
from pricing_exceptions.domain.model import Actor, Clock, RequestRecord

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Created:
    """A create's answer: the request with its history, and whether this call was a retry of the first."""

    record: RequestRecord
    replayed: bool


def submit(engine: Engine, clock: Clock, actor: Actor, application_id: str, terms: rules.Terms) -> Created:
    """Create a request, or replay the create that made it (design doc D3).

    The terms were checked when they were built (422). Then, in order: the RM role (403), the application
    (422), the insert. On a conflict the existing request is read in a separate statement, which at READ
    COMMITTED takes a fresh snapshot and so sees a create that committed while this insert waited. The same
    requester with the same terms as its SUBMITTED event is a retry: 201 again. Anything else is 409.
    """
    request, event = rules.submit(actor, application_id, terms, uuid4(), clock())
    with engine.begin() as conn:
        if not reference.application_exists(conn, application_id):
            raise ApplicationNotFound(application_id)
        if requests.insert_request(conn, request):
            requests.append_event(conn, event)
            created = Created(_read(conn, request.id), replayed=False)
        else:
            existing = _read(conn, requests.find_id_for_application(conn, application_id))
            if not rules.is_retry(existing, actor, terms):
                raise RequestExists(current=existing)  # rolls back; nothing was written
            created = Created(existing, replayed=True)
    if not created.replayed:  # logged after the commit, so the line never claims a change that rolled back
        log.info(
            "state change: request=%s actor=%s event=%s revision=%d",
            event.request_id,
            event.actor_id,
            event.event_type,
            event.revision,
        )
    return created


def _read(conn: Connection, request_id: UUID | None) -> RequestRecord:
    """Read a request this transaction knows exists: it inserted it, or the unique constraint just said so."""
    record = requests.read_request(conn, request_id) if request_id is not None else None
    if record is None:
        raise RuntimeError(f"request {request_id} should exist inside this transaction")
    return record
