"""Helpers shared by the API tests: who is calling, where, and the seeded ids (design doc section 10)."""

from datetime import datetime, timedelta

from sqlalchemy import Engine, text

REQUESTS = "/api/v1/pricing-exception-requests"
PROBLEMS = "https://pricing-exceptions.example/problems/"
REASON = "Retention: competitor offer 25 bps below standard"
NEW_REASON = "Competitor improved their offer"


class TickingClock:
    """A test clock: each read returns the next time, one `step` later. A step of zero freezes it."""

    def __init__(self, start: datetime, step: timedelta = timedelta(seconds=1)) -> None:
        self.next = start
        self.step = step

    def __call__(self) -> datetime:
        now, self.next = self.next, self.next + self.step
        return now


def as_user(user_id: str) -> dict[str, str]:
    return {"X-User-Id": user_id}


def seeded_id(application_number: int) -> str:
    """The seeded requests' ids end with their application's number, so they can be typed in Swagger."""
    return f"00000000-0000-4000-8000-{application_number:012d}"


def create_body(application_id: str = "MA-1001", discount_bps: object = 25, reason: object = REASON) -> dict:
    return {"application_id": application_id, "discount_bps": discount_bps, "reason": reason}


def revisions(request: int | str) -> str:
    """Where a revision is sent: a seeded application's number, or any request id."""
    return f"{REQUESTS}/{seeded_id(request) if isinstance(request, int) else request}/revisions"


def decisions(request: int | str) -> str:
    """Where a decision is sent: a seeded application's number, or any request id."""
    return f"{REQUESTS}/{seeded_id(request) if isinstance(request, int) else request}/decisions"


def revise_body(revision: object, discount_bps: object = 30, reason: object = NEW_REASON) -> dict:
    """A revision always carries the revision it was based on and both terms (design doc 5.3)."""
    return {"revision": revision, "discount_bps": discount_bps, "reason": reason}


def decide_body(revision: object, outcome: object = "APPROVED", comment: object = None) -> dict:
    """A decision; the comment key is left out when there is none."""
    body = {"revision": revision, "outcome": outcome}
    return body if comment is None else {**body, "comment": comment}


def stored(engine: Engine) -> tuple[int, int]:
    """How many requests and history events the database holds."""
    with engine.connect() as conn:
        requests = conn.execute(text("SELECT count(*) FROM pricing_exception_requests")).scalar_one()
        events = conn.execute(text("SELECT count(*) FROM pricing_exception_events")).scalar_one()
    return requests, events
