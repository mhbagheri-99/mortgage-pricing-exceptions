"""One throwaway PostgreSQL 18 for the whole test session, with the migrations applied once.

Before every test the requests and their history are emptied and the seed runs again, so each test starts from
the seeded data: 6 users, 20 applications and the 4 seeded requests (design doc 7.10).
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from calls import TickingClock
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from testcontainers.community.postgres import PostgresContainer

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.db.schema import migrate
from pricing_exceptions.db.seed import seed


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    with PostgresContainer("postgres:18", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        migrate(url)
        yield url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    engine = make_engine(database_url)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def seeded_data(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE pricing_exception_events, pricing_exception_requests RESTART IDENTITY"))
    seed(engine)


@pytest.fixture
def clock() -> TickingClock:
    return TickingClock(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))


@pytest.fixture
def client(engine: Engine, clock: TickingClock) -> TestClient:
    return TestClient(create_app(engine, clock, demo_identity=True))
