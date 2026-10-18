"""Every business rule: the policy limits, the value objects that enforce them, and the transitions.

Red skeleton: the shapes only, no behaviour yet, so the tests in tests/domain fail on their assertions.
"""

from dataclasses import dataclass
from datetime import datetime

from pricing_exceptions.domain.model import Actor, Outcome, PricingExceptionRequest, RequestEvent


@dataclass(frozen=True, slots=True)
class Terms:
    """What a requester asks for: a discount in basis points and a reason."""

    discount_bps: int
    reason: str


@dataclass(frozen=True, slots=True)
class Verdict:
    """What a reviewer decides: the outcome, and a comment."""

    outcome: Outcome
    comment: str | None = None


def decide(
    request: PricingExceptionRequest, actor: Actor, revision: int, verdict: Verdict, now: datetime
) -> tuple[PricingExceptionRequest, RequestEvent]:
    """Approve or decline one revision of a request."""
    raise NotImplementedError
