"""The full schema: users, mortgage applications, pricing exception requests and their history.

Every rule here is a backstop for a rule the domain also enforces (docs/architecture.md, "Data model"), so a
bug in the code cannot write a row that breaks one. The policy limits (1 to 100 bps, text up to 1,000
characters) live in the domain only; the database checks shape.

Revision ID: 0001
Revises:
Create Date: 2026-10-18
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

STATEMENTS = [
    """
    CREATE TABLE users (
        id           text PRIMARY KEY,
        display_name text NOT NULL,
        roles        text[] NOT NULL,
        CONSTRAINT ck_users_roles CHECK (
            cardinality(roles) > 0
            AND roles <@ ARRAY['RELATIONSHIP_MANAGER', 'REVIEWER', 'MORTGAGE_PROCESS']
        )
    )
    """,
    """
    CREATE TABLE mortgage_applications (
        id    text PRIMARY KEY,
        label text NOT NULL
    )
    """,
    """
    CREATE TABLE pricing_exception_requests (
        id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        application_id   text NOT NULL REFERENCES mortgage_applications (id),
        requested_by     text NOT NULL REFERENCES users (id),
        status           text NOT NULL,
        revision_no      integer NOT NULL,
        discount_bps     integer NOT NULL,
        reason           text NOT NULL,
        decided_by       text REFERENCES users (id),
        decided_at       timestamptz,
        decision_comment text,
        created_at       timestamptz NOT NULL,
        -- One request per application: the natural key that makes create safe to retry. Never partial.
        CONSTRAINT uq_request_application UNIQUE (application_id),
        CONSTRAINT ck_request_status CHECK (status IN ('PENDING_REVIEW', 'APPROVED', 'DECLINED')),
        CONSTRAINT ck_request_revision CHECK (revision_no >= 1),
        CONSTRAINT ck_request_discount CHECK (discount_bps > 0),
        -- Not blank: at least one character that is not whitespace (btrim alone strips only spaces).
        CONSTRAINT ck_request_reason CHECK (reason ~ '[^[:space:]]'),
        -- Four-eyes. On its own it passes when decided_by is NULL; the next check closes that gap.
        CONSTRAINT ck_request_four_eyes CHECK (decided_by <> requested_by),
        CONSTRAINT ck_request_decision_matches_status CHECK (
               (status = 'PENDING_REVIEW' AND decided_by IS NULL     AND decided_at IS NULL
                                          AND decision_comment IS NULL)
            OR (status = 'APPROVED'       AND decided_by IS NOT NULL AND decided_at IS NOT NULL)
            OR (status = 'DECLINED'       AND decided_by IS NOT NULL AND decided_at IS NOT NULL
                                          AND decision_comment IS NOT NULL)
        )
    )
    """,
    # The review queue: every page is one range scan of this index (docs/architecture.md, "Listing").
    "CREATE INDEX ix_request_status_created ON pricing_exception_requests (status, created_at, id)",
    """
    CREATE TABLE pricing_exception_events (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        request_id   uuid NOT NULL REFERENCES pricing_exception_requests (id),
        event_type   text NOT NULL,
        revision_no  integer NOT NULL,
        actor_id     text NOT NULL REFERENCES users (id),
        occurred_at  timestamptz NOT NULL,
        discount_bps integer NOT NULL,
        reason       text NOT NULL,
        comment      text,
        CONSTRAINT ck_event_type CHECK (
            event_type IN ('SUBMITTED', 'AMENDED', 'RESUBMITTED', 'APPROVED', 'DECLINED')
        ),
        CONSTRAINT ck_event_revision CHECK (revision_no >= 1),
        CONSTRAINT ck_event_discount CHECK (discount_bps > 0),
        CONSTRAINT ck_event_reason CHECK (reason ~ '[^[:space:]]')
    )
    """,
    # A request's history in order, and the create replay's lookup of the SUBMITTED event.
    "CREATE INDEX ix_event_request ON pricing_exception_events (request_id, id)",
    # A revision never gets two decisions, even if a future code path forgets the row lock.
    """
    CREATE UNIQUE INDEX ux_event_one_decision_per_revision
        ON pricing_exception_events (request_id, revision_no)
        WHERE event_type IN ('APPROVED', 'DECLINED')
    """,
    # A revision number is created once: two writers cannot both create revision n + 1 (no lost update).
    """
    CREATE UNIQUE INDEX ux_event_one_revision_event
        ON pricing_exception_events (request_id, revision_no)
        WHERE event_type IN ('SUBMITTED', 'AMENDED', 'RESUBMITTED')
    """,
]


def upgrade() -> None:
    for statement in STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    op.execute(
        "DROP TABLE pricing_exception_events, pricing_exception_requests, mortgage_applications, users"
    )
