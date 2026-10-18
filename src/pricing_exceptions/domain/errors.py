"""What can go wrong: one class per problem type in the error catalogue (design doc 5.5).

Each class carries its catalogue `code`. The api adds the HTTP status (P3); the domain never knows about HTTP.
"""

from typing import ClassVar

from pricing_exceptions.domain.model import Status


class DomainError(Exception):
    code: ClassVar[str]


class ValidationFailed(DomainError, ValueError):
    """422: the input is wrong on its own, whatever the request's state. Lists every broken field."""

    code = "validation-failed"

    def __init__(self, problems: dict[str, str]) -> None:
        self.problems = problems  # field name to what is wrong with it
        super().__init__("; ".join(f"{field} {problem}" for field, problem in problems.items()))


class Forbidden(DomainError):
    """403: the actor lacks the role the action needs."""

    code = "forbidden"


class FourEyesViolation(DomainError):
    """403: a reviewer tried to decide their own request."""

    code = "four-eyes-violation"


class InvalidState(DomainError):
    """409: the action is not allowed in the request's status; for example, approved is final."""

    code = "invalid-state"

    def __init__(self, status: Status, action: str) -> None:
        self.status = status
        super().__init__(f"A request that is {status} cannot be {action}.")


class StaleRevision(DomainError):
    """409: the caller named a revision that is not the current one."""

    code = "stale-revision"

    def __init__(self, sent: int, current: int) -> None:
        self.sent_revision = sent
        self.current_revision = current
        super().__init__(f"You sent revision {sent}; the current revision is {current}.")
