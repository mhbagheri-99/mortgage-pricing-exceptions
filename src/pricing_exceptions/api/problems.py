"""Every error as RFC 9457 problem details (design doc D7, 5.5), and the log line for each refused write
(7.9).

Five handlers: input that is unparseable (400) or invalid (422); HTTP errors such as 401, 404 and 405; our
domain errors; the database being busy or down (503); and anything else, which is a bug (500, no stack trace
in the body). Clients branch on `type`; `detail` is for people.
"""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from pricing_exceptions.api.schemas import RequestCurrent
from pricing_exceptions.domain.errors import (
    ApplicationNotFound,
    Conflict,
    DomainError,
    Forbidden,
    FourEyesViolation,
    InvalidState,
    NotFound,
    RequestExists,
    StaleRevision,
    ValidationFailed,
)
from pricing_exceptions.service import errors as database

PROBLEM_TYPES = "https://pricing-exceptions.example/problems/"  # .example is reserved; it never resolves

log = logging.getLogger(__name__)

# Each domain error's status and title. NotFound has no type of its own in the catalogue: `about:blank`.
DOMAIN_PROBLEMS: dict[type[DomainError], tuple[int, str]] = {
    ValidationFailed: (422, "The request is not valid"),
    ApplicationNotFound: (422, "No such mortgage application"),
    Forbidden: (403, "You may not do this"),
    FourEyesViolation: (403, "You cannot decide your own request"),
    NotFound: (404, HTTPStatus.NOT_FOUND.phrase),
    RequestExists: (409, "This application already has a request"),
    InvalidState: (409, "Not allowed in the request's current status"),
    StaleRevision: (409, "The request changed since you read it"),
}


def problem(
    request: Request,
    status: int,
    code: str | None,
    title: str,
    detail: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    request_id: UUID | None = None,
    extensions: Mapping[str, object] | None = None,
) -> JSONResponse:
    """One problem response; a refused write also gets its one WARNING line here."""
    body: dict[str, object] = {"type": PROBLEM_TYPES + code if code else "about:blank", "title": title}
    body["status"] = status
    if detail:
        body["detail"] = detail
    body.update(extensions or {})
    if request.method == "POST" and 400 <= status < 500:
        log_refused_write(request, code or "about:blank", status, request_id)
    return JSONResponse(body, status_code=status, headers=headers, media_type="application/problem+json")


def log_refused_write(request: Request, problem_type: str, status: int, request_id: UUID | None) -> None:
    """Who tried what, and why it was refused; never the reason, the comment or the body (design doc 7.9).
    The actor is the verified one, or `none` before the identity check passed: the raw header is never
    logged, because the caller controls it. The request id is printed only once it has parsed as a UUID."""
    actor = getattr(request.state, "actor", None)
    if request_id is None:
        request_id = _path_request_id(request)
    log.warning(
        "refused write: actor=%s action=%s request=%s problem=%s status=%d",
        actor.id if actor else "none",
        _action(request),
        request_id or "-",
        problem_type,
        status,
    )


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    def invalid_input(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        if any(error["type"] == "json_invalid" for error in errors):
            return problem(request, 400, "malformed-request", "The body is not valid JSON")
        return problem(
            request,
            422,
            "validation-failed",
            DOMAIN_PROBLEMS[ValidationFailed][1],
            "See errors for what is wrong with each field.",
            extensions={"errors": [_field_error(error) for error in errors]},
        )

    @app.exception_handler(HTTPException)
    def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 401:
            return problem(
                request, 401, "unauthenticated", "Who is calling is unknown", exc.detail, headers=exc.headers
            )
        phrase = HTTPStatus(exc.status_code).phrase
        detail = exc.detail if exc.detail != phrase else None
        return problem(request, exc.status_code, None, phrase, detail, headers=exc.headers)

    @app.exception_handler(DomainError)
    def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        status, title = DOMAIN_PROBLEMS[type(exc)]
        extensions: dict[str, object] = {}
        request_id = None
        if isinstance(exc, ValidationFailed):
            extensions["errors"] = [
                {"pointer": f"#/{field}", "detail": why} for field, why in exc.problems.items()
            ]
        if isinstance(exc, Conflict):
            if exc.current is None:
                raise RuntimeError("every 409 must carry the current request (design doc 5.3)") from exc
            extensions["current"] = RequestCurrent.of_record(exc.current).model_dump(mode="json")
            request_id = exc.current.request.id
        code = None if isinstance(exc, NotFound) else exc.code
        return problem(request, status, code, title, str(exc), request_id=request_id, extensions=extensions)

    @app.exception_handler(database.DatabaseError)
    def database_error(request: Request, exc: database.DatabaseError) -> JSONResponse:
        if not database.is_unavailable(exc):
            raise exc  # any other database error is a bug: the 500 handler answers it
        log.warning("database unavailable: action=%s %s", _action(request), database.describe(exc))
        return problem(
            request,
            503,
            "temporarily-unavailable",
            "Try again shortly",
            "The database is busy or unavailable; nothing was changed.",
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(Exception)
    def unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Starlette re-raises after this handler, so the server logs the traceback; the client sees none.
        return problem(request, 500, None, HTTPStatus.INTERNAL_SERVER_ERROR.phrase)


def _field_error(error: Any) -> dict[str, str]:
    """A Pydantic error as RFC 9457's example shows it: a JSON pointer into the body, or the parameter's
    name. Only Pydantic's message is kept, never the input, which can hold reason text."""
    where, *path = error["loc"]
    if where == "body":
        pointer = "#" + "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)
        return {"pointer": pointer, "detail": error["msg"]}
    return {"parameter" if where in ("query", "path") else str(where): str(path[0]), "detail": error["msg"]}


def _action(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "name", "-"))


def _path_request_id(request: Request) -> UUID | None:
    try:
        return UUID(str(request.path_params["request_id"]))
    except KeyError, ValueError:
        return None
