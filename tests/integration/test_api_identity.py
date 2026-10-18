"""The identity stand-in (ADR 0004): `X-User-Id` against the seeded users, only when DEMO_IDENTITY is
on. Off, it fails closed: every call is 401, whoever the header names."""

import logging

import pytest
from calls import PROBLEMS, REQUESTS, TickingClock, as_user, create_body, seeded_id
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from pricing_exceptions.api.app import create_app

CHALLENGE = 'X-User-Id realm="pricing-exceptions"'
CALLS = [
    pytest.param("POST", REQUESTS, id="create"),
    pytest.param("GET", REQUESTS, id="list"),
    pytest.param("GET", f"{REQUESTS}/{seeded_id(1002)}", id="get"),
    pytest.param("GET", "/api/v1/mortgage-applications/MA-1004/approved-discount", id="approved discount"),
]


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(("method", "path"), CALLS)
def test_every_call_is_401_with_the_demo_identity_off(
    engine: Engine, clock: TickingClock, method: str, path: str
) -> None:
    off = TestClient(create_app(engine, clock, demo_identity=False))
    body = create_body() if method == "POST" else None
    response = off.request(method, path, json=body, headers=as_user("rm.anna"))
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == CHALLENGE
    assert response.json()["type"] == PROBLEMS + "unauthenticated"
    assert off.get("/health").status_code == 200


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(("method", "path"), CALLS)
@pytest.mark.parametrize(
    "headers", [{}, as_user(""), as_user("rm.nobody")], ids=["no header", "an empty one", "an unknown user"]
)
def test_a_missing_or_unknown_caller_is_401(
    client: TestClient, method: str, path: str, headers: dict[str, str]
) -> None:
    body = create_body() if method == "POST" else None
    response = client.request(method, path, json=body, headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == CHALLENGE
    assert response.json()["type"] == PROBLEMS + "unauthenticated"


@pytest.mark.req(id="N5")
def test_the_app_warns_at_start_up_when_the_demo_identity_is_on(
    engine: Engine, clock: TickingClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    create_app(engine, clock, demo_identity=False)
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []
    create_app(engine, clock, demo_identity=True)
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "demo identity is on" in warnings[0].getMessage().lower()
