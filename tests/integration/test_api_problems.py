"""The error format (RFC 9457 problem details, design doc D7 and 5.5) and the log lines (7.9).

Every 409 carries `current`, the request as it is now (5.3). Every refused write logs exactly one WARNING
line, and no log line ever holds a reason, a comment or a request body.
"""

import json
import logging
from uuid import uuid4

import pytest
from calls import (
    PROBLEMS,
    REQUESTS,
    TickingClock,
    as_user,
    create_body,
    decide_body,
    decisions,
    revise_body,
    revisions,
    seeded_id,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricing_exceptions.api.app import create_app

PRIVATE_TEXT = "Borrower note 7f3a: never log this"

# One row per 409 the API can give: (problem type, caller, path, body, the request in conflict). P3 has
# request-exists; P4 adds invalid-state and stale-revision.
CONFLICTS = [
    pytest.param(
        "request-exists", "rm.anna", REQUESTS, create_body("MA-1002", 30), 1002, id="request-exists"
    ),
    pytest.param(
        "invalid-state", "rm.bram", revisions(1004), revise_body(1), 1004, id="invalid-state, revise"
    ),
    pytest.param(
        "invalid-state",
        "rv.erik",
        decisions(1004),
        decide_body(1, "DECLINED", "Too high"),
        1004,
        id="invalid-state, decide",
    ),
    pytest.param(
        "stale-revision", "rm.bram", revisions(1003), revise_body(1), 1003, id="stale-revision, revise"
    ),
    pytest.param(
        "stale-revision", "rv.dana", decisions(1003), decide_body(1), 1003, id="stale-revision, decide"
    ),
]


@pytest.mark.req(id="FR-6")
@pytest.mark.parametrize(("problem_type", "user", "path", "body", "number"), CONFLICTS)
def test_every_409_carries_current(
    client: TestClient, problem_type: str, user: str, path: str, body: dict[str, object], number: int
) -> None:
    response = client.post(path, json=body, headers=as_user(user))
    assert response.status_code == 409
    assert response.headers["Content-Type"] == "application/problem+json"
    problem = response.json()
    assert (problem["type"], problem["status"]) == (PROBLEMS + problem_type, 409)
    assert problem["title"]
    assert problem["detail"]
    current = client.get(f"{REQUESTS}/{seeded_id(number)}", headers=as_user("rv.dana")).json()
    del current["history"]
    assert problem["current"] == current


def warnings_from_the_api(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("pricing_exceptions") and record.levelno == logging.WARNING
    ]


@pytest.mark.parametrize(
    ("user", "body", "status", "line"),
    [
        pytest.param(
            "rm.anna",
            create_body("MA-1002", 30, PRIVATE_TEXT),
            409,
            f"actor=rm.anna action=create request={seeded_id(1002)} problem=request-exists status=409",
            id="409",
        ),
        pytest.param(
            "rv.dana",
            create_body(reason=PRIVATE_TEXT),
            403,
            "actor=rv.dana action=create request=- problem=forbidden status=403",
            id="403",
        ),
        pytest.param(
            None,
            create_body(reason=PRIVATE_TEXT),
            401,
            "actor=none action=create request=- problem=unauthenticated status=401",
            id="401",
        ),
        pytest.param(
            "rm.anna",
            create_body(reason=PRIVATE_TEXT + chr(0)),
            422,
            "actor=rm.anna action=create request=- problem=validation-failed status=422",
            id="422 values",
        ),
        pytest.param(
            "rm.anna",
            {**create_body(reason=PRIVATE_TEXT), "status": PRIVATE_TEXT},
            422,
            "actor=rm.anna action=create request=- problem=validation-failed status=422",
            id="422 shape",
        ),
        pytest.param(
            "rm.anna",
            create_body("MA-9999", reason=PRIVATE_TEXT),
            422,
            "actor=rm.anna action=create request=- problem=application-not-found status=422",
            id="422 application",
        ),
        pytest.param(
            "rm.anna",
            ('{"reason": "' + PRIVATE_TEXT + '",').encode(),
            400,
            "actor=none action=create request=- problem=malformed-request status=400",
            id="400, before the identity is read",
        ),
    ],
)
def test_a_refused_write_logs_one_warning_line_without_the_body(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    user: str | None,
    body: dict[str, object] | bytes,
    status: int,
    line: str,
) -> None:
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    headers = {**(as_user(user) if user else {}), "Content-Type": "application/json"}
    content = body if isinstance(body, bytes) else json.dumps(body).encode()
    response = client.post(REQUESTS, content=content, headers=headers)
    assert response.status_code == status
    assert warnings_from_the_api(caplog) == [f"refused write: {line}"]
    assert PRIVATE_TEXT not in caplog.text


def test_reads_and_successes_log_no_warning_and_a_change_logs_one_info_line(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    assert client.get(f"{REQUESTS}/{seeded_id(9999)}", headers=as_user("rv.dana")).status_code == 404
    created = client.post(REQUESTS, json=create_body(reason=PRIVATE_TEXT), headers=as_user("rm.anna"))
    replayed = client.post(REQUESTS, json=create_body(reason=PRIVATE_TEXT), headers=as_user("rm.anna"))
    assert (created.status_code, replayed.status_code) == (201, 201)
    assert warnings_from_the_api(caplog) == []
    infos = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("pricing_exceptions") and record.levelno == logging.INFO
    ]
    request_id = created.json()["id"]
    assert infos == [f"state change: request={request_id} actor=rm.anna event=SUBMITTED revision=1"]
    assert PRIVATE_TEXT not in caplog.text


@pytest.mark.parametrize(
    ("user", "path", "body", "status", "line"),
    [
        pytest.param(
            "rm.bram",
            revisions(1002),
            revise_body(1, reason=PRIVATE_TEXT),
            403,
            f"actor=rm.bram action=revise request={seeded_id(1002)} problem=forbidden status=403",
            id="403 not the requester",
        ),
        pytest.param(
            "svc.mortgage",
            decisions(9999),
            decide_body(1, "DECLINED", PRIVATE_TEXT),
            403,
            f"actor=svc.mortgage action=decide request={seeded_id(9999)} problem=forbidden status=403",
            id="403 before the lock",
        ),
        pytest.param(
            "rv.dana",
            decisions(9999),
            decide_body(1, "DECLINED", PRIVATE_TEXT),
            404,
            f"actor=rv.dana action=decide request={seeded_id(9999)} problem=about:blank status=404",
            id="404",
        ),
        pytest.param(
            "rm.bram",
            revisions(1004),
            revise_body(1, reason=PRIVATE_TEXT),
            409,
            f"actor=rm.bram action=revise request={seeded_id(1004)} problem=invalid-state status=409",
            id="409 invalid-state",
        ),
        pytest.param(
            "rv.dana",
            decisions(1003),
            decide_body(1, "DECLINED", PRIVATE_TEXT),
            409,
            f"actor=rv.dana action=decide request={seeded_id(1003)} problem=stale-revision status=409",
            id="409 stale-revision",
        ),
        pytest.param(
            "rv.dana",
            decisions(1003),
            decide_body(2, "DECLINED", PRIVATE_TEXT + chr(0)),
            422,
            f"actor=rv.dana action=decide request={seeded_id(1003)} problem=validation-failed status=422",
            id="422",
        ),
    ],
)
def test_a_refused_revise_or_decide_logs_one_warning_line_without_the_body(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
    user: str,
    path: str,
    body: dict[str, object],
    status: int,
    line: str,
) -> None:
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    response = client.post(path, json=body, headers=as_user(user))
    assert response.status_code == status
    assert warnings_from_the_api(caplog) == [f"refused write: {line}"]
    assert PRIVATE_TEXT not in caplog.text


def test_revise_and_decide_log_one_info_line_each(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """One INFO line per state change, after its commit: who, what and which revision, never the text
    (7.9)."""
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    revised = client.post(
        revisions(1003), json=revise_body(2, reason=PRIVATE_TEXT), headers=as_user("rm.bram")
    )
    decided = client.post(
        decisions(1003), json=decide_body(3, "DECLINED", PRIVATE_TEXT), headers=as_user("rv.dana")
    )
    assert (revised.status_code, decided.status_code) == (200, 200)
    assert warnings_from_the_api(caplog) == []
    infos = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("pricing_exceptions") and record.levelno == logging.INFO
    ]
    assert infos == [
        f"state change: request={seeded_id(1003)} actor=rm.bram event=AMENDED revision=3",
        f"state change: request={seeded_id(1003)} actor=rv.dana event=DECLINED revision=3",
    ]
    assert PRIVATE_TEXT not in caplog.text


def test_a_bug_is_a_500_problem_without_the_stack_trace(engine: Engine, clock: TickingClock) -> None:
    app = create_app(engine, clock, demo_identity=True)

    @app.get("/broken")
    def broken() -> None:
        raise RuntimeError("internal detail that must not reach the client")

    response = TestClient(app, raise_server_exceptions=False).get("/broken")
    assert response.status_code == 500
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.json() == {"type": "about:blank", "title": "Internal Server Error", "status": 500}


def test_an_unexpected_database_error_is_a_500_that_logs_no_values(
    engine: Engine, clock: TickingClock, caplog: pytest.LogCaptureFixture
) -> None:
    """A bug that breaks a database rule: the exception's text holds the bound values and PostgreSQL's
    failing row, so neither may reach a log line (P3 review finding 3)."""
    app = create_app(engine, clock, demo_identity=True)

    @app.post("/broken-write")
    def broken_write() -> None:
        with engine.begin() as conn:  # 0 bps breaks ck_request_discount
            conn.execute(
                text(
                    "INSERT INTO pricing_exception_requests (id, application_id, requested_by, status, "
                    "revision_no, discount_bps, reason, created_at) VALUES (:id, 'MA-1008', 'rm.anna', "
                    "'PENDING_REVIEW', 1, 0, :reason, :created_at)"
                ),
                {"id": uuid4(), "reason": PRIVATE_TEXT, "created_at": clock()},
            )

    caplog.set_level(logging.INFO)
    response = TestClient(app).post("/broken-write")  # the default client re-raises anything that escapes
    assert response.status_code == 500
    assert response.json() == {"type": "about:blank", "title": "Internal Server Error", "status": 500}
    errors = [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors == ["unexpected database error: action=broken_write IntegrityError sqlstate=23514"]
    assert PRIVATE_TEXT not in caplog.text


def test_an_unknown_path_is_a_404_problem(client: TestClient) -> None:
    response = client.get("/api/v1/nothing-here", headers=as_user("rv.dana"))
    assert response.status_code == 404
    assert response.headers["Content-Type"] == "application/problem+json"
    assert response.json() == {"type": "about:blank", "title": "Not Found", "status": 404}
