"""One throwaway PostgreSQL 18 for the whole test session, with the migrations applied once."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from testcontainers.community.postgres import PostgresContainer

from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.db.schema import migrate


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
