"""Revise (FR-2): the requester changes the terms as a new numbered revision (design doc D2, 5.3 and 5.5),
through the real app on a real PostgreSQL.

The seeded requests (section 10): MA-1002 is rm.anna's, pending at r1; MA-1003 is rm.bram's, pending at r2;
MA-1004 is rm.bram's, approved at r1 by rv.dana; MA-1005 is rm.anna's, declined at r1 by rv.erik.
"""

import pytest
from calls import (
    NEW_REASON,
    PROBLEMS,
    REQUESTS,
    as_user,
    decisions,
    revise_body,
    revisions,
    seeded_id,
    stored,
)
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

SEEDED = (4, 7)  # requests and history events after the seed
RESUBMIT_REASON = "Reduced to the 30 bps the reviewer would accept"


def kinds(body: dict) -> list[tuple[str, int, int]]:
    """A response's history as (event type, revision, discount) triples, oldest first."""
    return [(event["event_type"], event["revision"], event["discount_bps"]) for event in body["history"]]


@pytest.mark.req(id="FR-2")
def test_amend_makes_the_next_revision(client: TestClient, engine: Engine) -> None:
    response = client.post(revisions(1003), json=revise_body(2, 25), headers=as_user("rm.bram"))
    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"], body["revision"]) == (seeded_id(1003), "PENDING_REVIEW", 3)
    assert (body["discount_bps"], body["reason"], body["requested_by"]) == (25, NEW_REASON, "rm.bram")
    assert body["created_at"] == "2026-10-15T09:15:00Z"
    assert body["latest_decision"] is None
    assert kinds(body) == [("SUBMITTED", 1, 15), ("AMENDED", 2, 20), ("AMENDED", 3, 25)]
    newest = body["history"][-1]
    assert (newest["actor"], newest["reason"], newest["comment"]) == ("rm.bram", NEW_REASON, None)
    assert stored(engine) == (4, 8)


@pytest.mark.req(id="FR-2")
def test_resubmit_clears_decision_keeps_history(client: TestClient, engine: Engine) -> None:
    """A declined request takes new terms as a resubmission: pending again at the next revision, the row's
    decision columns cleared, and the decline still in history and in latest_decision (design doc 4.2,
    5.2)."""
    response = client.post(
        revisions(1005), json=revise_body(1, 30, RESUBMIT_REASON), headers=as_user("rm.anna")
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["revision"], body["discount_bps"]) == ("PENDING_REVIEW", 2, 30)
    assert body["latest_decision"] == {
        "outcome": "DECLINED",
        "revision": 1,
        "decided_by": "rv.erik",
        "decided_at": "2026-10-15T11:10:00Z",
        "comment": "Up to 30 bps acceptable",
    }
    assert kinds(body) == [("SUBMITTED", 1, 40), ("DECLINED", 1, 40), ("RESUBMITTED", 2, 30)]
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, revision_no, decided_by, decided_at, decision_comment "
                "FROM pricing_exception_requests WHERE id = :id"
            ),
            {"id": seeded_id(1005)},
        ).one()
    assert tuple(row) == ("PENDING_REVIEW", 2, None, None, None)
    queue = client.get(REQUESTS, headers=as_user("rv.dana")).json()
    assert seeded_id(1005) in [item["id"] for item in queue["items"]]
    assert stored(engine) == (4, 8)


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "status"),
    [
        pytest.param("rm.anna", 200, id="rm.anna, the requester"),
        pytest.param("rm.bram", 403, id="rm.bram, another relationship manager"),
        pytest.param("lead.fatima", 403, id="lead.fatima, both roles but not the requester"),
        pytest.param("rv.dana", 403, id="rv.dana, a reviewer"),
        pytest.param("svc.mortgage", 403, id="svc.mortgage, the mortgage process"),
    ],
)
def test_only_the_requester_revises(client: TestClient, engine: Engine, user: str, status: int) -> None:
    response = client.post(revisions(1002), json=revise_body(1), headers=as_user(user))
    assert response.status_code == status
    if status == 403:
        assert response.json()["type"] == PROBLEMS + "forbidden"
        assert stored(engine) == SEEDED
    else:
        assert response.json()["revision"] == 2


@pytest.mark.req(id="FR-2")
def test_a_stale_revision_is_409_with_the_revision_sent(client: TestClient, engine: Engine) -> None:
    """rm.bram changes MA-1003 from revision 1, but it is at revision 2: his change must not overwrite one he
    never saw (design doc 5.3)."""
    response = client.post(revisions(1003), json=revise_body(1), headers=as_user("rm.bram"))
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == PROBLEMS + "stale-revision"
    assert problem["detail"] == "You sent revision 1; the current revision is 2."
    assert problem["sent_revision"] == 1
    assert (problem["current"]["revision"], problem["current"]["discount_bps"]) == (2, 20)
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-2")
@pytest.mark.parametrize(
    ("user", "path", "body", "status", "problem"),
    [
        pytest.param(
            None, revisions(1002), revise_body("1"), 401, "unauthenticated", id="identity before shape"
        ),
        pytest.param(
            "rv.dana", revisions(1002), revise_body("1"), 422, "validation-failed", id="shape before role"
        ),
        pytest.param(
            "rv.dana", revisions(1002), revise_body(1, 0), 422, "validation-failed", id="values before role"
        ),
        pytest.param(
            "svc.mortgage",
            revisions(seeded_id(9999)),
            revise_body(1),
            403,
            "forbidden",
            id="role before the lock",
        ),
        pytest.param("rm.anna", revisions(1004), revise_body(1), 403, "forbidden", id="owner before status"),
        pytest.param(
            "rm.bram", revisions(1004), revise_body(99), 409, "invalid-state", id="status before revision"
        ),
        pytest.param(
            "rm.bram", revisions(1004), revise_body(1, 0), 422, "validation-failed", id="values before status"
        ),
    ],
)
def test_revise_checks_run_in_order(
    client: TestClient, engine: Engine, user: str | None, path: str, body: dict, status: int, problem: str
) -> None:
    """Design doc 5.5 and P4 decision 1: identity (401), shape and values (422), the role (403), the lock
    (404), the requester (403), the status (409), then the revision (409). The first check that fails
    answers."""
    response = client.post(path, json=body, headers=as_user(user) if user else {})
    assert (response.status_code, response.json()["type"]) == (status, PROBLEMS + problem)
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-2")
@pytest.mark.parametrize(
    ("body", "pointer"),
    [
        pytest.param(revise_body("2"), "#/revision", id="revision as a string"),
        pytest.param(revise_body(2.0), "#/revision", id="revision as a float"),
        pytest.param(revise_body(True), "#/revision", id="revision as a boolean"),
        pytest.param(revise_body(0), "#/revision", id="revision 0, which can never exist"),
        pytest.param(revise_body(-1), "#/revision", id="a negative revision"),
        pytest.param({"discount_bps": 25, "reason": NEW_REASON}, "#/revision", id="no revision"),
        pytest.param({"revision": 2, "discount_bps": 25}, "#/reason", id="no reason: both terms every time"),
        pytest.param(revise_body(2, "25"), "#/discount_bps", id="discount as a string"),
        pytest.param(
            {**revise_body(2), "application_id": "MA-1001"}, "#/application_id", id="application id"
        ),
        pytest.param({**revise_body(2), "status": "APPROVED"}, "#/status", id="status, which no client sets"),
    ],
)
def test_the_revise_body_is_strict(client: TestClient, engine: Engine, body: dict, pointer: str) -> None:
    response = client.post(revisions(1003), json=body, headers=as_user("rm.bram"))
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation-failed"
    assert [error["pointer"] for error in problem["errors"]] == [pointer]
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-2")
def test_revised_terms_outside_the_policy_are_422_listing_every_field(
    client: TestClient, engine: Engine
) -> None:
    response = client.post(revisions(1003), json=revise_body(2, 101, " \t\n"), headers=as_user("rm.bram"))
    assert response.status_code == 422
    assert {error["pointer"] for error in response.json()["errors"]} == {"#/discount_bps", "#/reason"}
    assert stored(engine) == SEEDED


@pytest.mark.req(id="FR-2")
@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("user", "path", "status"),
    [
        pytest.param("rm.anna", revisions(seeded_id(9999)), 404, id="revise an unknown request"),
        pytest.param("rv.dana", decisions(seeded_id(9999)), 404, id="decide an unknown request"),
        pytest.param("rm.anna", revisions("not-a-uuid"), 422, id="revise with a malformed id"),
        pytest.param("rv.dana", decisions("not-a-uuid"), 422, id="decide with a malformed id"),
    ],
)
def test_an_unknown_request_is_404_and_a_malformed_id_422(
    client: TestClient, engine: Engine, user: str, path: str, status: int
) -> None:
    body = revise_body(1) if path.endswith("/revisions") else {"revision": 1, "outcome": "APPROVED"}
    response = client.post(path, json=body, headers=as_user(user))
    assert response.status_code == status
    assert response.headers["Content-Type"] == "application/problem+json"
    if status == 404:
        assert response.json() == {
            "type": "about:blank",
            "title": "Not Found",
            "status": 404,
            "detail": f"There is no request {seeded_id(9999)}.",
        }
    else:
        assert [error.get("parameter") for error in response.json()["errors"]] == ["request_id"]
    assert stored(engine) == SEEDED
