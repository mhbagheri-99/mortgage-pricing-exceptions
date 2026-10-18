"""Write use cases: one transaction each, opened here and nowhere else (design doc 7.4)."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Connection, Engine

from pricing_exceptions.db import reference, requests
from pricing_exceptions.domain import rules
from pricing_exceptions.domain.errors import ApplicationNotFound, Conflict, NotFound, RequestExists
from pricing_exceptions.domain.model import Actor, Clock, PricingExceptionRequest, RequestEvent, RequestRecord

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
    if created.replayed:  # no state change, but operators see that upstream systems retry
        log.info("replayed create: request=%s actor=%s", created.record.request.id, actor.id)
    else:  # logged after the commit, so the line never claims a change that rolled back
        _log_change(event)
    return created


def revise(
    engine: Engine, clock: Clock, actor: Actor, request_id: UUID, revision: int, terms: rules.Terms
) -> RequestRecord:
    """Change the terms as the next revision (design doc D2). The terms were checked when they were built
    (422); whether the actor may revise at all is checked before any database access (403)."""
    rules.check_role_to_revise(actor)
    return _change(
        engine, clock, request_id, lambda request, now: rules.revise(request, actor, revision, terms, now)
    )


def decide(
    engine: Engine, clock: Clock, actor: Actor, request_id: UUID, revision: int, verdict: rules.Verdict
) -> RequestRecord:
    """Approve or decline one revision (design doc D1, D4). The verdict was checked when it was built (422);
    whether the actor may decide at all is checked before any database access (403)."""
    rules.check_role_to_decide(actor)
    return _change(
        engine, clock, request_id, lambda request, now: rules.decide(request, actor, revision, verdict, now)
    )


# A pure rule from the domain: the request as locked, and the time, give the changed request and its event.
type Rule = Callable[[PricingExceptionRequest, datetime], tuple[PricingExceptionRequest, RequestEvent]]


def _change(engine: Engine, clock: Clock, request_id: UUID, rule: Rule) -> RequestRecord:
    """The locked transaction behind every change to a request (design doc D4, 6.5 and 7.4).

    Lock the row by primary key; read the clock; run the pure rule on the row the lock returned; write the row
    and its one event; read the response; commit. A writer that waited for the lock gets the newest committed
    row, so the loser of a race runs the same rule on the winner's state and gets a precise 409. A refused
    change rolls back with nothing written, and a 409 first reads `current` under the same lock.
    """
    with engine.begin() as conn:
        request = requests.lock_request(conn, request_id)
        if request is None:
            raise NotFound(f"There is no request {request_id}.")
        try:
            changed, event = rule(request, clock())  # read under the lock: times follow the order of history
        except Conflict as conflict:
            conflict.current = _read(conn, request_id)  # the state the rule refused, for the 409 body
            raise
        requests.update_request(conn, changed)
        requests.append_event(conn, event)
        record = _read(conn, request_id)  # before the commit, still under the lock
    _log_change(event)  # after the commit, so the line never claims a change that rolled back
    return record


def _log_change(event: RequestEvent) -> None:
    """One INFO line per state change: never the reason or the comment (design doc 7.9)."""
    log.info(
        "state change: request=%s actor=%s event=%s revision=%d",
        event.request_id,
        event.actor_id,
        event.event_type,
        event.revision,
    )


def _read(conn: Connection, request_id: UUID | None) -> RequestRecord:
    """Read a request this transaction knows exists: it inserted it, locked it, or the unique constraint just
    said so."""
    record = requests.read_request(conn, request_id) if request_id is not None else None
    if record is None:
        raise RuntimeError(f"request {request_id} should exist inside this transaction")
    return record
