"""The database engine, with the isolation level and the time limits the guarantees rely on (design doc 7.3).

Worst case for one call: 3 s to get a connection (waiting for a pooled one, or opening a new one), then 5 s
for the whole transaction, so the server gives up before a client that waits 10 s.
"""

from sqlalchemy import Engine, create_engine, text

# psycopg cannot bind parameters into SET, so the limits are passed once, as connection options.
CONNECTION_OPTIONS = "-c lock_timeout=2s -c statement_timeout=3s -c transaction_timeout=5s -c TimeZone=UTC"


def make_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        isolation_level="READ COMMITTED",  # the create replay and the row-lock re-check depend on it
        pool_size=5,
        max_overflow=10,
        pool_timeout=3,  # waiting for a pooled connection
        pool_pre_ping=True,
        # connect_timeout bounds opening a new connection; without it an unreachable database hangs a call
        # for minutes (130 s on Windows in P1), far past the 8 s budget.
        connect_args={"options": CONNECTION_OPTIONS, "connect_timeout": 3},
    )


def ping(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
