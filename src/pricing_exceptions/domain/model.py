"""What the domain is made of: the request, the decision on it, its history events and who acts. Data only.

Enumerations are `StrEnum`s that mirror the text CHECK lists in the migration (design doc 4.2). The reason and
comment are left out of every repr, so a stray log of an object never leaks them (design doc 7.9).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

# Where the time comes from: main.py injects `datetime.now(UTC)`, the tests a fixed time. The service reads it
# once per use case and hands the domain the value, so a request row and its event share one timestamp.
type Clock = Callable[[], datetime]


class Role(StrEnum):
    RELATIONSHIP_MANAGER = "RELATIONSHIP_MANAGER"
    REVIEWER = "REVIEWER"
    MORTGAGE_PROCESS = "MORTGAGE_PROCESS"


class Status(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class Outcome(StrEnum):
    """What a reviewer decides."""

    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


class EventType(StrEnum):
    """One per transition in the state machine (design doc 6.3)."""

    SUBMITTED = "SUBMITTED"
    AMENDED = "AMENDED"
    RESUBMITTED = "RESUBMITTED"
    APPROVED = "APPROVED"
    DECLINED = "DECLINED"


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is calling: a seeded user here, an identity-provider subject in production."""

    id: str
    roles: frozenset[Role]


@dataclass(frozen=True, slots=True)
class Decision:
    """The decision on the current revision. Its outcome is the request's status (design doc 4.1)."""

    decided_by: str
    decided_at: datetime
    comment: str | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class PricingExceptionRequest:
    """The aggregate: one row and one lock. `decision` is None exactly while the request is pending.

    The terms are plain values: they were checked on the way in, and a later change to a policy limit must
    never make a stored request unreadable.
    """

    id: UUID
    application_id: str
    requested_by: str
    status: Status
    revision: int
    discount_bps: int
    reason: str = field(repr=False)
    created_at: datetime
    decision: Decision | None = None


@dataclass(frozen=True, slots=True)
class RequestEvent:
    """One append-only history row: what happened, at which revision, by whom, when, and the terms then."""

    request_id: UUID
    event_type: EventType
    revision: int
    actor_id: str
    occurred_at: datetime
    discount_bps: int
    reason: str = field(repr=False)
    comment: str | None = field(default=None, repr=False)
