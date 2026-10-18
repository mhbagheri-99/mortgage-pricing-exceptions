"""Read-only use cases. Each reads in one short transaction and changes nothing."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from pricing_exceptions.db import reference, requests
from pricing_exceptions.db.engine import ping
from pricing_exceptions.db.requests import Position as Position  # re-exported for the api's cursor
from pricing_exceptions.domain import rules
from pricing_exceptions.domain.errors import NotFound
from pricing_exceptions.domain.model import Actor, PricingExceptionRequest, RequestRecord, Status, User


def database_ok(engine: Engine) -> bool:
    """True when a pooled connection answers SELECT 1 within the engine's time limits."""
    try:
        ping(engine)
    except SQLAlchemyError:
        return False
    return True


def get_actor(engine: Engine, user_id: str) -> Actor | None:
    """The seeded user with this id and their roles, or None (design doc D6)."""
    with engine.connect() as conn:
        return reference.find_actor(conn, user_id)


def list_users(engine: Engine) -> list[User]:
    """The staff users, by id: the people the demo's acting-as switcher can act as. Only that switcher asks,
    and only with the demo identity on (design doc 5.1). svc.mortgage is left out: it is the mortgage
    process's account, which reads approved discounts through the API, not the page (P7)."""
    with engine.connect() as conn:
        return [user for user in reference.list_users(conn) if rules.is_staff(user.roles)]


def get_request(engine: Engine, actor: Actor, request_id: UUID) -> RequestRecord:
    """The request with its whole history. The role is checked first: it depends only on who asks."""
    rules.require_staff(actor)
    with engine.connect() as conn:
        record = requests.read_request(conn, request_id)
    if record is None:
        raise NotFound(f"There is no request {request_id}.")
    return record


@dataclass(frozen=True, slots=True)
class Page:
    items: list[PricingExceptionRequest]
    next_after: Position | None  # where the next page starts, or None on the last page


def list_requests(engine: Engine, actor: Actor, status: Status, after: Position | None, limit: int) -> Page:
    """One page of the requests in one status, oldest first. It reads one row more than it returns: that row
    tells whether a next page exists, with no count (design doc 5.6)."""
    rules.require_staff(actor)
    with engine.connect() as conn:
        rows = requests.list_requests(conn, status, after, limit + 1)
    items = rows[:limit]
    more = len(rows) > limit
    return Page(items, (items[-1].created_at, items[-1].id) if more else None)


def approved_discount(engine: Engine, application_id: str) -> PricingExceptionRequest | None:
    """The approved request for an application, or None before an approval. Every role may ask. An unknown
    application is 404, so a typo never reads as "no discount" (design doc 5.4)."""
    with engine.connect() as conn:
        if not reference.application_exists(conn, application_id):
            raise NotFound(f"There is no mortgage application {application_id}.")
        return requests.find_approved(conn, application_id)
