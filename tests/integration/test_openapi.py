"""The API description at /openapi.json, which Swagger at /docs draws from: every route lists each status it
can answer, every error is described as the problem details it really is, and the headers that come with an
answer are named. A client generated from this description then handles the real answers."""

from typing import Any

import pytest
from calls import REQUESTS
from fastapi.testclient import TestClient

PROBLEM_JSON = "application/problem+json"
ONE_REQUEST = REQUESTS + "/{request_id}"
# Each route's own errors, plus 400 for a body that is not JSON, 422 for a malformed body, path or query
# value, and 503 when the database is busy or down, which every route can answer.
STATUSES = {
    ("post", REQUESTS): {"201", "400", "401", "403", "409", "422", "503"},
    ("get", REQUESTS): {"200", "401", "403", "422", "503"},
    ("get", ONE_REQUEST): {"200", "401", "403", "404", "422", "503"},
    ("post", ONE_REQUEST + "/revisions"): {"200", "400", "401", "403", "404", "409", "422", "503"},
    ("post", ONE_REQUEST + "/decisions"): {"200", "400", "401", "403", "404", "409", "422", "503"},
    ("get", "/api/v1/mortgage-applications/{application_id}/approved-discount"): {
        "200",
        "401",
        "404",
        "422",
        "503",
    },
    ("get", "/health"): {"200", "503"},
}
# A 409 carries `current`, a 422 lists `errors`; every other error is plain problem details.
SCHEMA_FOR = {"409": "ConflictProblem", "422": "ValidationProblem"}


@pytest.fixture
def described(client: TestClient) -> dict[str, Any]:
    return client.get("/openapi.json").json()


def operations(described: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, operation)
        for path, by_method in described["paths"].items()
        for method, operation in by_method.items()
    ]


def test_every_route_lists_each_status_it_can_answer(described: dict[str, Any]) -> None:
    listed = {
        (method, path): set(operation["responses"]) for method, path, operation in operations(described)
    }
    assert listed == STATUSES


def test_every_error_is_described_as_problem_details(described: dict[str, Any]) -> None:
    for method, path, operation in operations(described):
        if path == "/health":  # its 503 is the health answer itself, not a problem
            continue
        for status, response in operation["responses"].items():
            if int(status) >= 400:
                schema = {"$ref": f"#/components/schemas/{SCHEMA_FOR.get(status, 'Problem')}"}
                assert response["content"] == {PROBLEM_JSON: {"schema": schema}}, (method, path, status)
    assert "HTTPValidationError" not in described["components"]["schemas"], "FastAPI's 422 is not ours"


def test_the_headers_that_come_with_an_answer_are_named(described: dict[str, Any]) -> None:
    created = described["paths"][REQUESTS]["post"]["responses"]["201"]
    assert set(created["headers"]) == {"Location", "Idempotent-Replayed"}
    for method, path, operation in operations(described):
        responses = operation["responses"]
        if "401" in responses:
            assert set(responses["401"]["headers"]) == {"WWW-Authenticate"}, (method, path)
        assert set(responses["503"]["headers"]) == {"Retry-After"}, (method, path)
