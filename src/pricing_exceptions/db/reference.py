"""Reference data this service does not own: the users (an identity provider in production) and the mortgage
applications (loan origination in production). These two lookups are the production seam (design doc 6.7).
"""

from sqlalchemy import Connection, text

from pricing_exceptions.domain.model import Actor, Role

FIND_USER = text("SELECT id, roles FROM users WHERE id = :id")
APPLICATION_EXISTS = text("SELECT EXISTS (SELECT 1 FROM mortgage_applications WHERE id = :id)")


def find_actor(conn: Connection, user_id: str) -> Actor | None:
    row = conn.execute(FIND_USER, {"id": user_id}).first()
    return Actor(row.id, frozenset(Role(role) for role in row.roles)) if row else None


def application_exists(conn: Connection, application_id: str) -> bool:
    return bool(conn.execute(APPLICATION_EXISTS, {"id": application_id}).scalar_one())
