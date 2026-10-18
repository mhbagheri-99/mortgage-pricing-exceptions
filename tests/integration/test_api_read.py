"""Read: a request with its whole history (FR-5), the review queue page by page (FR-7, design doc 5.6), and
the approved discount the mortgage process reads (FR-4, 5.4). The expected bodies spell out the seed table of
section 10, so a change to the seed or the response shape shows up here."""

import base64
import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from calls import PROBLEMS, REQUESTS, TickingClock, as_user, create_body, seeded_id
from fastapi.testclient import TestClient


def event(
    event_type: str, revision: int, actor: str, at: str, bps: int, reason: str, comment: str | None = None
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "revision": revision,
        "actor": actor,
        "occurred_at": at,
        "discount_bps": bps,
        "reason": reason,
        "comment": comment,
    }


ALLOWED = {"allowed": True, "problem": None, "detail": None}
NOT_AN_RM = {
    "allowed": False,
    "problem": "forbidden",
    "detail": "Only a relationship manager can revise a request.",
}


def not_in(status: str) -> dict[str, object]:
    return {
        "allowed": False,
        "problem": "invalid-state",
        "detail": f"A request that is {status} cannot be decided.",
    }


MOVING = "Long-standing customer moving their savings to us"
CONFIRMED = "Savings transfer confirmed; matching the competing offer"
FIRST_HOME = "First-time buyer with a large deposit"
CONSOLIDATING = "Consolidating three mortgages with us"
SEEDED = {
    1003: {
        "id": seeded_id(1003),
        "application_id": "MA-1003",
        "status": "PENDING_REVIEW",
        "revision": 2,
        "discount_bps": 20,
        "reason": CONFIRMED,
        "requested_by": "rm.bram",
        "created_at": "2026-10-15T09:15:00Z",
        "latest_decision": None,
        "history": [
            event("SUBMITTED", 1, "rm.bram", "2026-10-15T09:15:00Z", 15, MOVING),
            event("AMENDED", 2, "rm.bram", "2026-10-15T09:40:00Z", 20, CONFIRMED),
        ],
        # What rv.dana, who reads these, may do (P7): decide this pending one, never revise.
        "permissions": {"decide": ALLOWED, "revise": NOT_AN_RM},
    },
    1004: {
        "id": seeded_id(1004),
        "application_id": "MA-1004",
        "status": "APPROVED",
        "revision": 1,
        "discount_bps": 15,
        "reason": FIRST_HOME,
        "requested_by": "rm.bram",
        "created_at": "2026-10-15T09:30:00Z",
        "latest_decision": {
            "outcome": "APPROVED",
            "revision": 1,
            "decided_by": "rv.dana",
            "decided_at": "2026-10-15T10:02:44Z",
            "comment": None,
        },
        "history": [
            event("SUBMITTED", 1, "rm.bram", "2026-10-15T09:30:00Z", 15, FIRST_HOME),
            event("APPROVED", 1, "rv.dana", "2026-10-15T10:02:44Z", 15, FIRST_HOME),
        ],
        "permissions": {"decide": not_in("APPROVED"), "revise": NOT_AN_RM},
    },
    1005: {
        "id": seeded_id(1005),
        "application_id": "MA-1005",
        "status": "DECLINED",
        "revision": 1,
        "discount_bps": 40,
        "reason": CONSOLIDATING,
        "requested_by": "rm.anna",
        "created_at": "2026-10-15T09:45:00Z",
        "latest_decision": {
            "outcome": "DECLINED",
            "revision": 1,
            "decided_by": "rv.erik",
            "decided_at": "2026-10-15T11:10:00Z",
            "comment": "Up to 30 bps acceptable",
        },
        "history": [
            event("SUBMITTED", 1, "rm.anna", "2026-10-15T09:45:00Z", 40, CONSOLIDATING),
            event(
                "DECLINED", 1, "rv.erik", "2026-10-15T11:10:00Z", 40, CONSOLIDATING, "Up to 30 bps acceptable"
            ),
        ],
        "permissions": {"decide": not_in("DECLINED"), "revise": NOT_AN_RM},
    },
}
LIST_FIELDS = {"id", "application_id", "status", "revision", "discount_bps", "requested_by", "created_at"}


@pytest.mark.req(id="FR-5")
@pytest.mark.parametrize("number", [1003, 1004, 1005], ids=["changed", "approved", "declined"])
def test_get_shows_the_status_every_revision_and_every_decision(client: TestClient, number: int) -> None:
    response = client.get(f"{REQUESTS}/{seeded_id(number)}", headers=as_user("rv.dana"))
    assert response.status_code == 200
    assert response.json() == SEEDED[number]


@pytest.mark.req(id="FR-5")
def test_an_unknown_request_is_404_and_a_malformed_id_422(client: TestClient) -> None:
    missing = client.get(f"{REQUESTS}/{uuid4()}", headers=as_user("rv.dana"))
    assert (missing.status_code, missing.json()["type"]) == (404, "about:blank")
    malformed = client.get(f"{REQUESTS}/not-a-request-id", headers=as_user("rv.dana"))
    assert (malformed.status_code, malformed.json()["type"]) == (422, PROBLEMS + "validation-failed")
    assert [error["parameter"] for error in malformed.json()["errors"]] == ["request_id"]


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    "path",
    [REQUESTS, f"{REQUESTS}/{seeded_id(1002)}", f"{REQUESTS}/{uuid4()}"],
    ids=["the list", "a request", "an unknown request, so 403 comes before 404"],
)
def test_the_mortgage_process_cannot_read_requests(client: TestClient, path: str) -> None:
    response = client.get(path, headers=as_user("svc.mortgage"))
    assert (response.status_code, response.json()["type"]) == (403, PROBLEMS + "forbidden")


@pytest.mark.req(id="FR-5")
@pytest.mark.parametrize(
    ("user", "path", "params", "status", "problem"),
    [
        pytest.param(None, f"{REQUESTS}/not-an-id", {}, 401, "unauthenticated", id="identity before shape"),
        pytest.param("svc.mortgage", f"{REQUESTS}/not-an-id", {}, 422, "validation-failed", id="shape first"),
        pytest.param("svc.mortgage", REQUESTS, {"limit": "0"}, 422, "validation-failed", id="limit first"),
        pytest.param(
            "svc.mortgage", REQUESTS, {"cursor": "not a cursor!"}, 422, "validation-failed", id="cursor first"
        ),
        pytest.param("svc.mortgage", f"{REQUESTS}/{uuid4()}", {}, 403, "forbidden", id="role before lookup"),
    ],
)
def test_read_checks_run_in_order(
    client: TestClient, user: str | None, path: str, params: dict[str, str], status: int, problem: str
) -> None:
    """Identity (401), then shape and values (422), then the staff role (403), then the lookup (404)."""
    response = client.get(path, params=params, headers=as_user(user) if user else {})
    assert (response.status_code, response.json()["type"]) == (status, PROBLEMS + problem)


def walk(client: TestClient, limit: int, **params: str) -> list[list[dict[str, object]]]:
    """Follow next_cursor from the first page until it is null, as any client must; return every page."""
    pages: list[list[dict[str, object]]] = []
    cursor: str | None = None
    while True:
        query: dict[str, str | int] = {"limit": limit, **params}
        if cursor is not None:
            query["cursor"] = cursor
        response = client.get(REQUESTS, params=query, headers=as_user("rv.dana"))
        assert response.status_code == 200
        page = response.json()
        assert set(page) == {"items", "next_cursor"}
        pages.append(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            return pages
        assert len(pages) < 20, "the walk must end"


@pytest.mark.req(id="FR-7")
def test_the_queue_walk_reaches_every_request_once_oldest_first(
    client: TestClient, clock: TickingClock
) -> None:
    clock.step = timedelta(0)  # four creates at the same instant, so only the id can order them
    for application in ["MA-1001", "MA-1006", "MA-1007", "MA-1008"]:
        assert (
            client.post(REQUESTS, json=create_body(application), headers=as_user("rm.anna")).status_code
            == 201
        )

    pages = walk(client, limit=2)
    assert [len(page) for page in pages] == [2, 2, 2], "a full last page still ends the walk"
    walked = [item for page in pages for item in page]
    ids = [item["id"] for item in walked]
    assert len(set(ids)) == len(ids) == 6
    assert [item["application_id"] for item in walked[:2]] == ["MA-1002", "MA-1003"]  # seeded on 15 October
    order = [(datetime.fromisoformat(str(item["created_at"])), UUID(str(item["id"]))) for item in walked]
    assert order == sorted(order), "oldest first, the id breaking ties"
    assert all(set(item) == LIST_FIELDS for item in walked), "list fields only; history stays in GET"
    assert [len(page) for page in walk(client, limit=5)] == [5, 1]
    assert walk(client, limit=200) == [walked]


@pytest.mark.req(id="FR-7")
@pytest.mark.parametrize(
    ("status", "applications"),
    [
        pytest.param(None, ["MA-1002", "MA-1003"], id="pending by default"),
        pytest.param("PENDING_REVIEW", ["MA-1002", "MA-1003"], id="pending"),
        pytest.param("APPROVED", ["MA-1004"], id="approved"),
        pytest.param("DECLINED", ["MA-1005"], id="declined"),
    ],
)
def test_the_list_filters_by_status(client: TestClient, status: str | None, applications: list[str]) -> None:
    pages = walk(client, limit=50, **({"status": status} if status else {}))
    assert [item["application_id"] for item in pages[0]] == applications
    if status == "APPROVED":
        assert pages[0] == [
            {
                "id": seeded_id(1004),
                "application_id": "MA-1004",
                "status": "APPROVED",
                "revision": 1,
                "discount_bps": 15,
                "requested_by": "rm.bram",
                "created_at": "2026-10-15T09:30:00Z",
            }
        ]


def as_cursor(data: object) -> str:
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


AT = "2026-10-15T09:00:00+00:00"


@pytest.mark.req(id="FR-7")
@pytest.mark.parametrize(
    ("params", "parameter"),
    [
        pytest.param({"cursor": "not a cursor!"}, "cursor", id="not base64url"),
        pytest.param({"cursor": as_cursor(b"not json")}, "cursor", id="not JSON"),
        pytest.param({"cursor": as_cursor([AT, seeded_id(1002)])}, "cursor", id="not an object"),
        pytest.param({"cursor": as_cursor({"created_at": AT})}, "cursor", id="no id"),
        pytest.param(
            {"cursor": as_cursor({"created_at": AT, "id": seeded_id(1002), "x": 1})}, "cursor", id="extra"
        ),
        pytest.param(
            {"cursor": as_cursor({"created_at": "yesterday", "id": seeded_id(1002)})}, "cursor", id="time"
        ),
        pytest.param(
            {"cursor": as_cursor({"created_at": "2026-10-15T09:00:00", "id": seeded_id(1002)})},
            "cursor",
            id="no time zone",
        ),
        pytest.param(
            {"cursor": as_cursor({"created_at": "2026-10-15T09:00:00+20:00", "id": seeded_id(1002)})},
            "cursor",
            id="an offset of 20 hours",
        ),
        pytest.param(
            {"cursor": as_cursor({"created_at": "2026-10-15T10:00:00+01:00", "id": seeded_id(1002)})},
            "cursor",
            id="not UTC, so not a cursor this service issued",
        ),
        pytest.param({"cursor": as_cursor({"created_at": AT, "id": "not-a-uuid"})}, "cursor", id="id"),
        pytest.param({"cursor": as_cursor({"created_at": 5, "id": 7})}, "cursor", id="numbers"),
        pytest.param({"cursor": "A" * 201}, "cursor", id="too long"),
        pytest.param({"limit": "0"}, "limit", id="limit 0"),
        pytest.param({"limit": "201"}, "limit", id="limit 201"),
        pytest.param({"limit": "ten"}, "limit", id="limit as a word"),
        pytest.param({"status": "OPEN"}, "status", id="unknown status"),
    ],
)
def test_bad_list_parameters_are_422(client: TestClient, params: dict[str, str], parameter: str) -> None:
    response = client.get(REQUESTS, params=params, headers=as_user("rv.dana"))
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation-failed"
    assert [error["parameter"] for error in problem["errors"]] == [parameter]


DISCOUNT = "/api/v1/mortgage-applications/{}/approved-discount"


@pytest.mark.req(id="FR-4")
@pytest.mark.parametrize("user", ["svc.mortgage", "rm.anna", "rv.dana"])
def test_the_mortgage_process_reads_the_approved_discount(client: TestClient, user: str) -> None:
    response = client.get(DISCOUNT.format("MA-1004"), headers=as_user(user))
    assert response.status_code == 200
    assert response.json() == {
        "application_id": "MA-1004",
        "status": "APPROVED",
        "discount_bps": 15,
        "request_id": seeded_id(1004),
        "revision": 1,
        "approved_by": "rv.dana",
        "approved_at": "2026-10-15T10:02:44Z",
    }


@pytest.mark.req(id="FR-4")
@pytest.mark.parametrize(
    "application",
    ["MA-1001", "MA-1002", "MA-1003", "MA-1005"],
    ids=["no request", "pending", "changed", "declined"],
)
def test_before_an_approval_the_answer_is_none(client: TestClient, application: str) -> None:
    """No discount_bps key at all, so a consumer cannot read a null as zero (design doc 5.4)."""
    response = client.get(DISCOUNT.format(application), headers=as_user("svc.mortgage"))
    assert response.status_code == 200
    assert response.json() == {"application_id": application, "status": "NONE"}


@pytest.mark.req(id="FR-4")
def test_an_unknown_application_is_404_so_a_typo_never_reads_as_none(client: TestClient) -> None:
    missing = client.get(DISCOUNT.format("MA-9999"), headers=as_user("svc.mortgage"))
    assert (missing.status_code, missing.json()["type"]) == (404, "about:blank")
    for malformed in ["MA%201001", "MA-1001%00"]:
        response = client.get(DISCOUNT.format(malformed), headers=as_user("svc.mortgage"))
        assert (response.status_code, response.json()["type"]) == (422, PROBLEMS + "validation-failed")
        assert [error["parameter"] for error in response.json()["errors"]] == ["application_id"]
