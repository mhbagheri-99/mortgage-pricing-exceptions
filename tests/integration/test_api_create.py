"""Create, and its replay (design doc D3, 5.2 and 5.5), through the real app on a real PostgreSQL.

The seeded requests (section 10) give a taken application for every conflict: MA-1002 is rm.anna's, at 20 bps.
"""

import logging
import time

import pytest
from calls import PROBLEMS, REASON, REQUESTS, TickingClock, as_user, create_body, seeded_id, stored
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine

SEEDED = (4, 7)  # requests and history events after the seed
MA_1002_REASON = "Retention: the borrower holds a competing offer"
UNREACHABLE = "postgresql+psycopg://nobody:nothing@192.0.2.1:5432/nothing"  # reserved; never answers
PRIVATE_TEXT = "Borrower note 7f3a: never log this"


@pytest.mark.req(id="FR-1")
def test_create_returns_the_new_request_with_its_history(client: TestClient, engine: Engine) -> None:
    response = client.post(REQUESTS, json=create_body(), headers=as_user("rm.anna"))
    assert response.status_code == 201
    body = response.json()
    assert response.headers["Location"] == f"{REQUESTS}/{body['id']}"
    assert "Idempotent-Replayed" not in response.headers
    assert body == {
        "id": body["id"],
        "application_id": "MA-1001",
        "status": "PENDING_REVIEW",
        "revision": 1,
        "discount_bps": 25,
        "reason": REASON,
        "requested_by": "rm.anna",
        "created_at": "2026-10-18T09:00:00Z",
        "latest_decision": None,
        "history": [
            {
                "event_type": "SUBMITTED",
                "revision": 1,
                "actor": "rm.anna",
                "occurred_at": "2026-10-18T09:00:00Z",
                "discount_bps": 25,
                "reason": REASON,
                "comment": None,
            }
        ],
        # What rm.anna may do next (P7): revise her request; deciding needs the reviewer role.
        "permissions": {
            "decide": {
                "allowed": False,
                "problem": "forbidden",
                "detail": "Only a reviewer can decide a request.",
            },
            "revise": {"allowed": True, "problem": None, "detail": None},
        },
    }
    # The same caller reads it back; permissions belong to the caller, so another user would see others.
    assert client.get(response.headers["Location"], headers=as_user("rm.anna")).json() == body
    assert stored(engine) == (5, 8)


@pytest.mark.req(id="FR-6")
def test_a_retried_create_replays_the_same_request(client: TestClient) -> None:
    first = client.post(REQUESTS, json=create_body(), headers=as_user("rm.anna"))
    retry = client.post(REQUESTS, json=create_body(), headers=as_user("rm.anna"))
    assert (first.status_code, retry.status_code) == (201, 201)
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.headers["Location"] == first.headers["Location"]
    assert retry.json() == first.json()


@pytest.mark.req(id="N2")
def test_identical_creates_one_after_another_give_one_request(client: TestClient, engine: Engine) -> None:
    responses = [client.post(REQUESTS, json=create_body(), headers=as_user("rm.anna")) for _ in range(3)]
    assert [response.status_code for response in responses] == [201, 201, 201]
    assert len({response.json()["id"] for response in responses}) == 1
    assert stored(engine) == (5, 8)


@pytest.mark.req(id="FR-6")
@pytest.mark.parametrize(
    ("number", "user", "discount", "reason", "status", "revision"),
    [
        pytest.param(
            1003,
            "rm.bram",
            15,
            "Long-standing customer moving their savings to us",
            "PENDING_REVIEW",
            2,
            id="changed since",
        ),
        pytest.param(
            1004, "rm.bram", 15, "First-time buyer with a large deposit", "APPROVED", 1, id="approved since"
        ),
        pytest.param(
            1005, "rm.anna", 40, "Consolidating three mortgages with us", "DECLINED", 1, id="declined since"
        ),
    ],
)
def test_a_late_retry_replays_the_request_as_it_is_now(
    client: TestClient,
    engine: Engine,
    number: int,
    user: str,
    discount: int,
    reason: str,
    status: str,
    revision: int,
) -> None:
    """A retry repeats the original create: it is compared with the SUBMITTED event, not the current row."""
    response = client.post(
        REQUESTS, json=create_body(f"MA-{number}", discount, reason), headers=as_user(user)
    )
    assert (response.status_code, response.headers.get("Idempotent-Replayed")) == (201, "true")
    assert response.headers["Location"] == f"{REQUESTS}/{seeded_id(number)}"
    body = response.json()
    assert (body["status"], body["revision"]) == (status, revision)
    assert body == client.get(response.headers["Location"], headers=as_user("rv.dana")).json()
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-6")
@pytest.mark.parametrize(
    ("user", "discount", "reason"),
    [
        pytest.param("rm.anna", 30, MA_1002_REASON, id="another discount"),
        pytest.param("rm.anna", 20, MA_1002_REASON + " ", id="a reason one space longer"),
        pytest.param("rm.bram", 20, MA_1002_REASON, id="another requester"),
        pytest.param("lead.fatima", 20, MA_1002_REASON, id="another requester with both roles"),
    ],
)
def test_a_different_create_for_a_taken_application_is_409(
    client: TestClient, engine: Engine, user: str, discount: int, reason: str
) -> None:
    response = client.post(REQUESTS, json=create_body("MA-1002", discount, reason), headers=as_user(user))
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == PROBLEMS + "request-exists"
    current = client.get(f"{REQUESTS}/{seeded_id(1002)}", headers=as_user("rv.dana")).json()
    del current["history"]
    assert problem["current"] == current
    assert stored(engine) == SEEDED


@pytest.mark.req(id="N5")
@pytest.mark.parametrize("user", ["rv.dana", "rv.erik", "svc.mortgage"])
def test_only_a_relationship_manager_creates(client: TestClient, engine: Engine, user: str) -> None:
    response = client.post(REQUESTS, json=create_body(), headers=as_user(user))
    assert (response.status_code, response.json()["type"]) == (403, PROBLEMS + "forbidden")
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-1")
def test_an_unknown_application_is_422(client: TestClient, engine: Engine) -> None:
    response = client.post(REQUESTS, json=create_body("MA-9999"), headers=as_user("rm.anna"))
    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "application-not-found"
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    ("discount", "reason", "pointers"),
    [
        pytest.param(0, REASON, ["#/discount_bps"], id="0 bps"),
        pytest.param(101, REASON, ["#/discount_bps"], id="101 bps"),
        pytest.param(25, " \t\n", ["#/reason"], id="a blank reason"),
        pytest.param(25, "x" * 1001, ["#/reason"], id="a reason of 1001 characters"),
        pytest.param(25, "Retention" + chr(0), ["#/reason"], id="a NUL in the reason"),
        pytest.param(0, "", ["#/discount_bps", "#/reason"], id="both, both listed"),
    ],
)
def test_values_outside_the_policy_are_422_listing_every_field(
    client: TestClient, engine: Engine, discount: int, reason: str, pointers: list[str]
) -> None:
    response = client.post(
        REQUESTS, json=create_body("MA-1001", discount, reason), headers=as_user("rm.anna")
    )
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation-failed"
    assert sorted(error["pointer"] for error in problem["errors"]) == pointers
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        pytest.param(create_body(discount_bps="25"), "#/discount_bps", id="a discount as text"),
        pytest.param(create_body(discount_bps=25.0), "#/discount_bps", id="a discount as a float"),
        pytest.param(create_body(discount_bps=True), "#/discount_bps", id="a discount as true"),
        pytest.param(create_body(reason=25), "#/reason", id="a reason as a number"),
        pytest.param({**create_body(), "status": "APPROVED"}, "#/status", id="a status sent by the client"),
        pytest.param(
            {**create_body(), "decided_by": "rv.dana"}, "#/decided_by", id="a decider sent by the client"
        ),
        pytest.param({"application_id": "MA-1001", "discount_bps": 25}, "#/reason", id="no reason"),
        pytest.param(
            create_body(application_id=1001), "#/application_id", id="an application id as a number"
        ),
        pytest.param(create_body(application_id=""), "#/application_id", id="an empty application id"),
        pytest.param(
            create_body(application_id="MA 1001"), "#/application_id", id="a space in the application id"
        ),
        pytest.param(create_body(application_id="MA-1001\n"), "#/application_id", id="a trailing newline"),
        pytest.param(create_body(application_id="MA-1001" + chr(0)), "#/application_id", id="a NUL"),
        pytest.param(create_body(application_id="M" * 65), "#/application_id", id="65 characters"),
    ],
)
def test_strict_json(client: TestClient, engine: Engine, body: dict[str, object], pointer: str) -> None:
    """Nothing is coerced: "25", 25.0 and true are not a discount, and the client cannot set server fields."""
    response = client.post(REQUESTS, json=body, headers=as_user("rm.anna"))
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation-failed"
    assert [error["pointer"] for error in problem["errors"]] == [pointer]
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    "raw",
    [b'{"application_id": "MA-1001",', b'{"reason": "Retention \xff"}'],
    ids=["cut short", "not UTF-8"],
)
@pytest.mark.parametrize("headers", [as_user("rm.anna"), {}], ids=["with an identity", "without one"])
def test_unparseable_json_is_400(client: TestClient, headers: dict[str, str], raw: bytes) -> None:
    """FastAPI reads the JSON before the identity (P1 spike step 7), so 400 comes even before 401."""
    response = client.post(REQUESTS, content=raw, headers={**headers, "Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.json()["type"] == PROBLEMS + "malformed-request"


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    ("user", "body", "status", "problem"),
    [
        pytest.param(
            None, create_body(discount_bps="25"), 401, "unauthenticated", id="identity before shape"
        ),
        pytest.param(
            "rv.dana", create_body(discount_bps="25"), 422, "validation-failed", id="shape before role"
        ),
        pytest.param(
            "rv.dana", create_body(discount_bps=0), 422, "validation-failed", id="values before role"
        ),
        pytest.param("rv.dana", create_body("MA-9999"), 403, "forbidden", id="role before the application"),
        pytest.param("rv.dana", create_body("MA-1002"), 403, "forbidden", id="role before the conflict"),
        pytest.param(
            "rm.anna",
            create_body("MA-9999", discount_bps=0),
            422,
            "validation-failed",
            id="values before the application",
        ),
    ],
)
def test_create_checks_run_in_order(
    client: TestClient, user: str | None, body: dict[str, object], status: int, problem: str
) -> None:
    """Design doc 5.5: identity (401), shape and values (422), the RM role (403), the application (422), then
    the insert. The first check that fails answers."""
    response = client.post(REQUESTS, json=body, headers=as_user(user) if user else {})
    assert (response.status_code, response.json()["type"]) == (status, PROBLEMS + problem)


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    ("raw", "pointer"),
    [
        pytest.param(
            rb'{"application_id": "MA-1001", "discount_bps": 25, "reason": "Retention \ud800"}',
            "#/reason",
            id="a lone surrogate in the reason",
        ),
        pytest.param(
            rb'{"application_id": "MA-1001", "discount_bps": 25, "reason": "Retention", "\ud800": 1}',
            "#",
            id="a lone surrogate as a field name, which Pydantic refuses for the whole body",
        ),
    ],
)
def test_text_that_is_not_valid_unicode_is_422_not_500(
    client: TestClient, engine: Engine, raw: bytes, pointer: str
) -> None:
    """JSON may escape half of a surrogate pair, which UTF-8 cannot encode: PostgreSQL would refuse it as a
    500, and so would the error response that names the field (P3 review finding 1)."""
    response = client.post(
        REQUESTS, content=raw, headers={**as_user("rm.anna"), "Content-Type": "application/json"}
    )
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation-failed"
    assert [error["pointer"] for error in problem["errors"]] == [pointer]
    assert stored(engine) == SEEDED


@pytest.mark.req(id="N8")
def test_a_create_with_the_database_down_is_refused_with_503(
    clock: TickingClock, caplog: pytest.LogCaptureFixture
) -> None:
    down = TestClient(create_app(make_engine(UNREACHABLE), clock, demo_identity=True))
    caplog.clear()  # only what the call logs, not the start-up warning about the demo identity
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    started = time.monotonic()
    response = down.post(REQUESTS, json=create_body(reason=PRIVATE_TEXT), headers=as_user("rm.anna"))
    elapsed = time.monotonic() - started
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json()["type"] == PROBLEMS + "temporarily-unavailable"
    assert elapsed < 6, f"an unreachable database must fail fast (connect limit 3 s), took {elapsed:.0f} s"
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("pricing_exceptions") and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].startswith("database unavailable: action=create OperationalError sqlstate=")
    assert PRIVATE_TEXT not in caplog.text
