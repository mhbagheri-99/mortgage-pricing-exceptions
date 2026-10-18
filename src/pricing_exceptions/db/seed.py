"""Fictional seed data. Running it again changes nothing.

The container runs it on every start (docs/architecture.md, "Operating and changing it safely"). Users and
applications are inserted with ON CONFLICT DO NOTHING. Each seeded request goes through the create's own
insert, and its history is written only when that insert lands, so a second run adds no events either. The
requests have fixed ids and times on 15 October 2026: four, one in each situation the demo and the example of
an approved discount need, and ten more awaiting review, so the queue spans more than one page.

    python -m pricing_exceptions.db.seed     # uses DATABASE_URL
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import Engine, text

from pricing_exceptions.db.requests import append_event, insert_request
from pricing_exceptions.domain.model import (
    Decision,
    EventType,
    PricingExceptionRequest,
    RequestEvent,
    Status,
)

USERS = [
    ("rm.anna", "Anna, relationship manager", ["RELATIONSHIP_MANAGER"]),
    ("rm.bram", "Bram, relationship manager", ["RELATIONSHIP_MANAGER"]),
    ("rv.dana", "Dana, reviewer", ["REVIEWER"]),
    ("rv.erik", "Erik, reviewer", ["REVIEWER"]),
    (
        "lead.fatima",
        "Fatima, team lead: relationship manager and reviewer",
        ["RELATIONSHIP_MANAGER", "REVIEWER"],
    ),
    ("svc.mortgage", "Mortgage application process (a service)", ["MORTGAGE_PROCESS"]),
]
# Thirty. Fourteen hold a seeded request (MA-1002 to MA-1005 and MA-1021 to MA-1030), so a live walk-through
# has sixteen free: each application takes one request for good.
APPLICATIONS = [(f"MA-{n}", f"Fictional mortgage application {n}") for n in range(1001, 1031)]


def seeded_id(application_number: int) -> UUID:
    """A readable fixed id that ends with the application's number, so it can be typed in Swagger."""
    return UUID(f"00000000-0000-4000-8000-{application_number:012d}")


def on_15_october(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 15, hour, minute, second, tzinfo=UTC)


COMPETING = "Retention: the borrower holds a competing offer"
MOVING = "Long-standing customer moving their savings to us"
CONFIRMED = "Savings transfer confirmed; matching the competing offer"
FIRST_HOME = "First-time buyer with a large deposit"
CONSOLIDATING = "Consolidating three mortgages with us"
DECLINE = "Up to 30 bps acceptable"

MA_1002, MA_1003, MA_1004, MA_1005 = (seeded_id(n) for n in (1002, 1003, 1004, 1005))
T = EventType

# Each request as it is now, then its history in order. The terms in each event are the terms at that
# revision; a decision event repeats the terms it judged.
REQUESTS: list[tuple[PricingExceptionRequest, list[RequestEvent]]] = [
    (  # MA-1002: pending at revision 1
        PricingExceptionRequest(
            MA_1002, "MA-1002", "rm.anna", Status.PENDING_REVIEW, 1, 20, COMPETING, on_15_october(9, 0)
        ),
        [RequestEvent(MA_1002, T.SUBMITTED, 1, "rm.anna", on_15_october(9, 0), 20, COMPETING)],
    ),
    (  # MA-1003: 15 bps, then changed to 20 bps; pending at revision 2
        PricingExceptionRequest(
            MA_1003, "MA-1003", "rm.bram", Status.PENDING_REVIEW, 2, 20, CONFIRMED, on_15_october(9, 15)
        ),
        [
            RequestEvent(MA_1003, T.SUBMITTED, 1, "rm.bram", on_15_october(9, 15), 15, MOVING),
            RequestEvent(MA_1003, T.AMENDED, 2, "rm.bram", on_15_october(9, 40), 20, CONFIRMED),
        ],
    ),
    (  # MA-1004: approved at revision 1 by rv.dana (the approved-discount example)
        PricingExceptionRequest(
            MA_1004,
            "MA-1004",
            "rm.bram",
            Status.APPROVED,
            1,
            15,
            FIRST_HOME,
            on_15_october(9, 30),
            Decision("rv.dana", on_15_october(10, 2, 44), None),
        ),
        [
            RequestEvent(MA_1004, T.SUBMITTED, 1, "rm.bram", on_15_october(9, 30), 15, FIRST_HOME),
            RequestEvent(MA_1004, T.APPROVED, 1, "rv.dana", on_15_october(10, 2, 44), 15, FIRST_HOME),
        ],
    ),
    (  # MA-1005: declined at revision 1 by rv.erik, with the comment a decline needs
        PricingExceptionRequest(
            MA_1005,
            "MA-1005",
            "rm.anna",
            Status.DECLINED,
            1,
            40,
            CONSOLIDATING,
            on_15_october(9, 45),
            Decision("rv.erik", on_15_october(11, 10), DECLINE),
        ),
        [
            RequestEvent(MA_1005, T.SUBMITTED, 1, "rm.anna", on_15_october(9, 45), 40, CONSOLIDATING),
            RequestEvent(
                MA_1005, T.DECLINED, 1, "rv.erik", on_15_october(11, 10), 40, CONSOLIDATING, DECLINE
            ),
        ],
    ),
]


CHANGE_AFTER = timedelta(minutes=20)


def pending(
    number: int, requester: str, at: datetime, *terms: tuple[int, str]
) -> tuple[PricingExceptionRequest, list[RequestEvent]]:
    """A request awaiting review: submitted with the first terms, then changed once for each later pair,
    twenty minutes apart. The row holds the last terms; its created_at is the submission's time."""
    request_id = seeded_id(number)
    history = [
        RequestEvent(
            request_id,
            T.SUBMITTED if revision == 1 else T.AMENDED,
            revision,
            requester,
            at + (revision - 1) * CHANGE_AFTER,
            bps,
            reason,
        )
        for revision, (bps, reason) in enumerate(terms, start=1)
    ]
    bps, reason = terms[-1]
    request = PricingExceptionRequest(
        request_id, f"MA-{number}", requester, Status.PENDING_REVIEW, len(terms), bps, reason, at
    )
    return request, history


# The rest of the review queue: twelve pending requests in all, more than the page shows at its default of
# ten, so "Load more" appears without changing the page size. They come after the four above, oldest first.
REQUESTS += [
    pending(1021, "rm.anna", on_15_october(12, 0), (25, "Remortgage at the end of a fixed term elsewhere")),
    pending(1022, "rm.bram", on_15_october(12, 20), (10, "Low loan-to-value: half the property's value")),
    pending(1023, "rm.anna", on_15_october(12, 45), (30, COMPETING)),
    pending(
        1024,
        "rm.bram",
        on_15_october(13, 5),
        (10, "Self-employed borrower with three years of accounts"),
        (15, "Self-employed borrower; accounts checked, matching the broker's quote"),
    ),
    pending(1025, "rm.anna", on_15_october(13, 30), (20, "Energy-efficient home after a full renovation")),
    pending(1026, "rm.bram", on_15_october(14, 0), (35, "Moving all household banking to us")),
    pending(1027, "rm.anna", on_15_october(14, 25), (15, FIRST_HOME)),
    pending(
        1028,
        "rm.anna",
        on_15_october(14, 50),
        (25, "Customer for twenty years, renewing at the end of the term"),
        (20, "Customer for twenty years; renewal terms agreed with the borrower"),
    ),
    pending(1029, "rm.bram", on_15_october(15, 15), (20, "Salary paid into an account with us")),
    pending(1030, "rm.bram", on_15_october(15, 40), (30, "Bridging the gap to a published competitor rate")),
]


def seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, display_name, roles) VALUES (:id, :display_name, :roles) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            [{"id": i, "display_name": name, "roles": roles} for i, name, roles in USERS],
        )
        conn.execute(
            text(
                "INSERT INTO mortgage_applications (id, label) VALUES (:id, :label) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            [{"id": i, "label": label} for i, label in APPLICATIONS],
        )
        for request, history in REQUESTS:
            if insert_request(conn, request):  # False when the application already has a request
                for event in history:
                    append_event(conn, event)


if __name__ == "__main__":
    from pricing_exceptions.db.engine import make_engine
    from pricing_exceptions.settings import Settings

    seed(make_engine(Settings.from_env().database_url))
