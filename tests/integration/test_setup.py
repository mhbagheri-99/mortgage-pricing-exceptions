"""Setup checks (docs/architecture.md, "Testing"): the app reports database health, the seed can run twice,
and the engine's isolation level and time limits are really in effect on a pooled connection."""

import time

from calls import REQUESTS, seeded_id
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.db.seed import seed
from pricing_exceptions.main import system_clock

# 192.0.2.1 is reserved for documentation and never answers, so the connection attempt hangs on every OS
# (a closed local port is refused at once on Linux, which would not exercise the connect limit).
UNREACHABLE = "postgresql+psycopg://nobody:nothing@192.0.2.1:5432/nothing"


def test_health_reports_database(engine: Engine) -> None:
    up = TestClient(create_app(engine, system_clock, demo_identity=False)).get("/health")
    assert (up.status_code, up.json()) == (200, {"status": "ok", "database": "ok"})

    started = time.monotonic()
    down = TestClient(create_app(make_engine(UNREACHABLE), system_clock, demo_identity=False)).get("/health")
    elapsed = time.monotonic() - started
    assert (down.status_code, down.json()) == (503, {"status": "unavailable", "database": "unreachable"})
    assert down.headers["Retry-After"] == "1"
    assert elapsed < 6, f"an unreachable database must fail fast (connect limit 3 s), took {elapsed:.0f} s"


def counts(engine: Engine) -> tuple[int, int, int, int]:
    """Users, applications, requests and history events."""
    with engine.connect() as conn:
        users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
        applications = conn.execute(text("SELECT count(*) FROM mortgage_applications")).scalar_one()
        requests = conn.execute(text("SELECT count(*) FROM pricing_exception_requests")).scalar_one()
        events = conn.execute(text("SELECT count(*) FROM pricing_exception_events")).scalar_one()
    return users, applications, requests, events


def test_seed_runs_twice(engine: Engine) -> None:
    """Thirty applications, fourteen of them with a seeded request: sixteen stay free for a live walk-through,
    because each application takes one request for good."""
    seed(engine)
    assert counts(engine) == (6, 30, 14, 19)
    seed(engine)
    assert counts(engine) == (6, 30, 14, 19)


def test_seed_leaves_a_changed_seeded_request_alone(engine: Engine) -> None:
    """A second `docker compose up` after a demo seeds again: what the demo changed must stay as it is."""
    client = TestClient(create_app(engine, system_clock, demo_identity=True))
    revised = client.post(
        f"{REQUESTS}/{seeded_id(1002)}/revisions",
        json={"revision": 1, "discount_bps": 30, "reason": "Competitor improved their offer"},
        headers={"X-User-Id": "rm.anna"},
    )
    assert revised.status_code == 200
    seed(engine)
    assert counts(engine) == (6, 30, 14, 20)
    again = client.get(f"{REQUESTS}/{seeded_id(1002)}", headers={"X-User-Id": "rm.anna"}).json()
    assert (again["revision"], again["discount_bps"]) == (2, 30)
    assert [event["event_type"] for event in again["history"]] == ["SUBMITTED", "AMENDED"]


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
    assert engine.hide_parameters, "an error message must never carry a bound value, such as a reason"
