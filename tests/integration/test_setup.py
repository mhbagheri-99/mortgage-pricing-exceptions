"""Setup checks (design doc 7.10): the app reports database health, the seed can run twice, and the engine's
isolation level and time limits are really in effect on a pooled connection."""

import time

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.db.seed import seed

# 192.0.2.1 is reserved for documentation and never answers, so the connection attempt hangs on every OS
# (a closed local port is refused at once on Linux, which would not exercise the connect limit).
UNREACHABLE = "postgresql+psycopg://nobody:nothing@192.0.2.1:5432/nothing"


def test_health_reports_database(engine: Engine) -> None:
    up = TestClient(create_app(engine)).get("/health")
    assert (up.status_code, up.json()) == (200, {"status": "ok", "database": "ok"})

    started = time.monotonic()
    down = TestClient(create_app(make_engine(UNREACHABLE))).get("/health")
    elapsed = time.monotonic() - started
    assert (down.status_code, down.json()) == (503, {"status": "unavailable", "database": "unreachable"})
    assert down.headers["Retry-After"] == "1"
    assert elapsed < 6, f"an unreachable database must fail fast (connect limit 3 s), took {elapsed:.0f} s"


def counts(engine: Engine) -> tuple[int, int]:
    with engine.connect() as conn:
        users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        applications = conn.execute(text("SELECT count(*) FROM mortgage_applications")).scalar_one()
    return users, applications


def test_seed_runs_twice(engine: Engine) -> None:
    seed(engine)
    assert counts(engine) == (6, 8)
    seed(engine)
    assert counts(engine) == (6, 8)


SETTINGS = {
    "transaction_isolation": ("SHOW transaction_isolation", "read committed"),
    "lock_timeout": ("SHOW lock_timeout", "2s"),
    "statement_timeout": ("SHOW statement_timeout", "3s"),
    "transaction_timeout": ("SHOW transaction_timeout", "5s"),
    "TimeZone": ("SHOW TimeZone", "UTC"),
}


def test_engine_settings_in_effect(engine: Engine) -> None:
    with engine.connect() as conn:
        shown = {name: conn.execute(text(sql)).scalar_one() for name, (sql, _) in SETTINGS.items()}
    assert shown == {name: expected for name, (_, expected) in SETTINGS.items()}
    assert engine.pool.timeout() == 3
