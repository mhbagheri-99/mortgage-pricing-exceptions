"""The live demo, as a test: the twelve steps of docs/demo.http, with the same users, ids and status codes, on
the seeded data. Step 1 is the README and Swagger, so the calls start at step 2.

It guards the demo against later changes; it claims no requirement id, because every behaviour it touches has
its own focused test. The second test checks that docs/demo.http holds exactly the calls this one sends.
"""

import json
import logging
import re
from pathlib import Path

import pytest
from calls import NEW_REASON, PROBLEMS, REASON, REQUESTS, as_user
from fastapi.testclient import TestClient
from httpx2 import Response

DEMO_HTTP = Path(__file__).resolve().parents[2] / "docs" / "demo.http"
REQUEST = REQUESTS + "/{id}"  # "{id}" stands for the request that step 2 creates
APPROVAL = "Within retention policy"

# Every call of the demo: step, acting as, method, path, JSON body, expected status.
CALLS = [
    (
        2,
        "lead.fatima",
        "POST",
        REQUESTS,
        {"application_id": "MA-1006", "discount_bps": 25, "reason": REASON},
        201,
    ),
    (
        3,
        "lead.fatima",
        "POST",
        REQUESTS,
        {"application_id": "MA-1006", "discount_bps": 25, "reason": REASON},
        201,
    ),
    (
        4,
        "lead.fatima",
        "POST",
        REQUESTS,
        {"application_id": "MA-1006", "discount_bps": 30, "reason": REASON},
        409,
    ),
    (5, "lead.fatima", "POST", REQUEST + "/decisions", {"revision": 1, "outcome": "APPROVED"}, 403),
    (6, "rv.dana", "GET", REQUEST, None, 200),
    (
        7,
        "lead.fatima",
        "POST",
        REQUEST + "/revisions",
        {"revision": 1, "discount_bps": 30, "reason": NEW_REASON},
        200,
    ),
    (
        8,
        "rv.dana",
        "POST",
        REQUEST + "/decisions",
        {"revision": 1, "outcome": "APPROVED", "comment": APPROVAL},
        409,
    ),
    (
        9,
        "rv.dana",
        "POST",
        REQUEST + "/decisions",
        {"revision": 2, "outcome": "APPROVED", "comment": APPROVAL},
        200,
    ),
    (
        10,
        "lead.fatima",
        "POST",
        REQUEST + "/revisions",
        {"revision": 2, "discount_bps": 35, "reason": NEW_REASON},
        409,
    ),
    (11, "svc.mortgage", "GET", "/api/v1/mortgage-applications/MA-1006/approved-discount", None, 200),
    (12, "rv.dana", "GET", REQUEST, None, 200),
]


def refused(actor: str, action: str, request_id: str, problem: str, status: int) -> str:
    """The WARNING line the service writes for a refused write: who, what, which request, the problem."""
    return (
        f"refused write: actor={actor} action={action} request={request_id} problem={problem} status={status}"
    )


def test_demo_scenario(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="pricing_exceptions")
    answers: dict[int, Response] = {}
    request_id = ""
    for step, user, method, path, body, status in CALLS:
        answer = client.request(method, path.replace("{id}", request_id), json=body, headers=as_user(user))
        assert answer.status_code == status, f"step {step} answered {answer.status_code}: {answer.text}"
        answers[step] = answer
        request_id = request_id or answer.json()["id"]

    created = answers[2]
    assert (created.json()["revision"], created.json()["discount_bps"]) == (1, 25)
    assert created.headers["Location"] == f"{REQUESTS}/{request_id}"

    assert answers[3].json()["id"] == request_id
    assert answers[3].headers["Idempotent-Replayed"] == "true"

    assert answers[4].json()["type"] == PROBLEMS + "request-exists"
    assert answers[4].json()["current"]["id"] == request_id

    assert answers[5].json()["type"] == PROBLEMS + "four-eyes-violation"

    assert (answers[6].json()["revision"], answers[6].json()["discount_bps"]) == (1, 25)

    assert (answers[7].json()["revision"], answers[7].json()["discount_bps"]) == (2, 30)

    stale = answers[8].json()
    assert (stale["type"], stale["sent_revision"]) == (PROBLEMS + "stale-revision", 1)
    assert (stale["current"]["revision"], stale["current"]["discount_bps"]) == (2, 30)

    decision = answers[9].json()["latest_decision"]
    assert (decision["outcome"], decision["revision"], decision["decided_by"]) == ("APPROVED", 2, "rv.dana")

    assert answers[10].json()["type"] == PROBLEMS + "invalid-state"

    assert answers[11].json() == {
        "application_id": "MA-1006",
        "status": "APPROVED",
        "discount_bps": 30,
        "request_id": request_id,
        "revision": 2,
        "approved_by": "rv.dana",
        "approved_at": decision["decided_at"],
    }

    history = answers[12].json()["history"]
    assert [(event["event_type"], event["revision"]) for event in history] == [
        ("SUBMITTED", 1),
        ("AMENDED", 2),
        ("APPROVED", 2),
    ]
    assert history[0]["reason"] == REASON

    # Rejected attempts go to the log, not the history: one WARNING line for each of steps 4, 5, 8 and 10.
    warnings = [
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("pricing_exceptions") and record.levelno == logging.WARNING
    ]
    assert warnings == [
        refused("lead.fatima", "create", request_id, "request-exists", 409),
        refused("lead.fatima", "decide", request_id, "four-eyes-violation", 403),
        refused("rv.dana", "decide", request_id, "stale-revision", 409),
        refused("lead.fatima", "revise", request_id, "invalid-state", 409),
    ]


def test_demo_http_follows_the_same_steps() -> None:
    """docs/demo.http holds exactly the calls above: each step's user, method, path, body and status."""
    calls = []
    for block in DEMO_HTTP.read_text(encoding="utf-8").split("\n###")[1:]:
        step = re.search(r"^ Step (\d+) ", block)
        request = re.search(r"^(GET|POST) (\S+)$", block, re.MULTILINE)
        user = re.search(r"^X-User-Id: (\S+)$", block, re.MULTILINE)
        expected = re.search(r"^# Expect: (\d{3})\b", block, re.MULTILINE)
        assert None not in (step, request, user, expected), (
            f"a call in demo.http lacks a part: {block[:60]!r}"
        )
        path = request[2].replace("{{api}}", "/api/v1").replace("{{create.response.body.$.id}}", "{id}")
        body = block.partition("\n\n")[2].strip()
        calls.append(
            (int(step[1]), user[1], request[1], path, json.loads(body) if body else None, int(expected[1]))
        )
    assert calls == CALLS
