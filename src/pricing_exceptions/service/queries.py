"""Read-only use cases."""

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from pricing_exceptions.db.engine import ping


def database_ok(engine: Engine) -> bool:
    """True when a pooled connection answers SELECT 1 within the engine's time limits."""
    try:
        ping(engine)
    except SQLAlchemyError:
        return False
    return True
