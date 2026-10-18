"""Database errors as the api needs to see them, without importing SQLAlchemy (import-linter contract 3).

Consistency over availability (N8): when the database is busy or down, a call is refused with 503 and
Retry-After, never answered without its guarantees. Any other database error is a bug, a 500.
"""

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeout

DatabaseError = SQLAlchemyError


def is_unavailable(error: SQLAlchemyError) -> bool:
    """True for "try again shortly" (docs/architecture.md, "Time limits"): no connection within 3 s, the
    database down or restarting, a lock wait over 2 s (55P03), a statement over 3 s (57014), a
    transaction over 5 s (it ends the session), and the deadlock and serialization failures that should never
    happen (40P01, 40001). psycopg reports all of these as OperationalError, or as a lost connection."""
    if isinstance(error, (OperationalError, InterfaceError, PoolTimeout)):
        return True
    return isinstance(error, DBAPIError) and error.connection_invalidated


def describe(error: SQLAlchemyError) -> str:
    """The error's class and SQLSTATE for a log line. Never its message, which can hold bound values."""
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    return f"{type(error).__name__} sqlstate={sqlstate or '-'}"
