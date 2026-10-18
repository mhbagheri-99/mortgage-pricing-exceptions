"""What can go wrong: one class per problem type in the error catalogue (design doc 5.5).

Each class carries its catalogue `code`. The api adds the HTTP status (api/problems.py); the domain never
knows about HTTP.
"""

from typing import ClassVar

from pricing_exceptions.domain.model import RequestRecord, Status


class DomainError(Exception):
    code: ClassVar[str]


class ValidationFailed(DomainError, ValueError):
    """422: the input is wrong on its own, whatever the request's state. Lists every broken field."""

    code = "validation-failed"

    def __init__(self, problems: dict[str, str]) -> None:
        self.problems = problems  # field name to what is wrong with it
        super().__init__("; ".join(f"{field} {problem}" for field, problem in problems.items()))


class ApplicationNotFound(DomainError):
    """422: the body names a mortgage application that does not exist."""

    code = "application-not-found"

    def __init__(self, application_id: str) -> None:
        self.application_id = application_id
        super().__init__(f"There is no mortgage application {application_id}.")


class Forbidden(DomainError):
    """403: the actor lacks the role the action needs."""

    code = "forbidden"


class FourEyesViolation(DomainError):
    """403: a reviewer tried to decide their own request."""

    code = "four-eyes-violation"


class NotFound(DomainError):
    """404: no request with that id, or no application with the id in the path. The catalogue gives it no
    type of its own, so the api answers `about:blank`."""

    code = "not-found"


class Conflict(DomainError):
    """409: the action conflicts with the request as it is now.

    `current` is that request, for the response body: every 409 carries it (design doc 5.3), so a client whose
    response was lost can see whether its change landed. The pure rules cannot read it; the service fills it
    in before its transaction ends.
    """

    current: RequestRecord | None = None


class RequestExists(Conflict):
    """409: the application already has a request, and this create is not a retry of the one that made it."""

    code = "request-exists"

    def __init__(self, current: RequestRecord) -> None:
        self.current = current
        # No advice: revising is no way out of an approved request, nor of another manager's. `current` shows
        # the request.
        super().__init__(f"Mortgage application {current.request.application_id} already has a request.")


class InvalidState(Conflict):
    """409: the action is not allowed in the request's status; for example, approved is final."""

    code = "invalid-state"

    def __init__(self, status: Status, action: str) -> None:
        self.status = status
        super().__init__(f"A request that is {status} cannot be {action}.")


class StaleRevision(Conflict):
    """409: the caller named a revision that is not the current one."""

    code = "stale-revision"

    def __init__(self, sent: int, current: int) -> None:
        self.sent_revision = sent
        self.current_revision = current
        super().__init__(f"You sent revision {sent}; the current revision is {current}.")
