"""Every response that shows a request also says what its caller may do to it (P7, design doc 10.2).

`permissions` holds one entry per action. When the caller may not, it names the problem type and the detail
the action would be refused with, so a client can show why instead of offering a button that can only fail.
The server still checks every rule when the action is tried; these are the same checks, run in advance.
"""

import pytest
from calls import REQUESTS, as_user, create_body, decide_body, decisions, seeded_id
from fastapi.testclient import TestClient

ALLOWED = {"allowed": True, "problem": None, "detail": None}


def refused(problem: str, detail: str) -> dict[str, object]:
    return {"allowed": False, "problem": problem, "detail": detail}


NOT_A_REVIEWER = refused("forbidden", "Only a reviewer can decide a request.")
NOT_AN_RM = refused("forbidden", "Only a relationship manager can revise a request.")


@pytest.mark.req(id="N5")
@pytest.mark.parametrize(
    ("user", "application", "decide", "revise"),
    [
        pytest.param("rm.anna", 1002, NOT_A_REVIEWER, ALLOWED, id="the requester, pending"),
        pytest.param("rv.dana", 1002, ALLOWED, NOT_AN_RM, id="a reviewer, pending"),
        pytest.param(
            "rm.bram",
            1002,
            NOT_A_REVIEWER,
            refused("forbidden", "Only the relationship manager who made the request can revise it."),
            id="another relationship manager",
        ),
        pytest.param(
            "rv.dana",
            1004,
            refused("invalid-state", "A request that is APPROVED cannot be decided."),
            NOT_AN_RM,
            id="a reviewer, approved",
        ),
        pytest.param("rm.anna", 1005, NOT_A_REVIEWER, ALLOWED, id="the requester, declined: resubmit"),
        pytest.param(
            "rv.erik",
            1005,
            refused("invalid-state", "A request that is DECLINED cannot be decided."),
            NOT_AN_RM,
            id="a reviewer, declined",
        ),
    ],
)
def test_a_request_says_what_its_caller_may_do(
    client: TestClient, user: str, application: int, decide: dict, revise: dict
) -> None:
    response = client.get(f"{REQUESTS}/{seeded_id(application)}", headers=as_user(user))
    assert response.status_code == 200
    assert response.json()["permissions"] == {"decide": decide, "revise": revise}


@pytest.mark.req(id="N5")
def test_write_answers_say_what_the_caller_may_do_next(client: TestClient) -> None:
    # lead.fatima holds both roles, so only four-eyes stops her deciding her own request.
    created = client.post(REQUESTS, json=create_body("MA-1006"), headers=as_user("lead.fatima"))
    assert created.status_code == 201
    assert created.json()["permissions"] == {
        "decide": refused("four-eyes-violation", "You cannot decide a request you made."),
        "revise": ALLOWED,
    }
    approved = client.post(decisions(created.json()["id"]), json=decide_body(1), headers=as_user("rv.dana"))
    assert approved.status_code == 200
    assert approved.json()["permissions"] == {
        "decide": refused("invalid-state", "A request that is APPROVED cannot be decided."),
        "revise": NOT_AN_RM,
    }
