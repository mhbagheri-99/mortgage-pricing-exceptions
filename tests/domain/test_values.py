"""The value objects check themselves when built (docs/architecture.md, "Order of checks"): a bad
value is a 422 before any lock is taken. Text is checked but never rewritten: it is kept exactly as sent, so a
retried create compares equal (ADR 0002). The limits are our assumptions (README, "Assumptions"); the brief
sets none.

The terms tests carry FR-1 because the create API tests prove FR-1 end to end; a marker here before those
tests existed would have let the requirement gate pass without any create test.
"""

from collections.abc import Callable
from enum import StrEnum

import pytest

from pricing_exceptions.domain.errors import ValidationFailed
from pricing_exceptions.domain.model import EventType, Outcome, Role, Status
from pricing_exceptions.domain.rules import Action, Terms, Verdict

REASON = "Retention: competitor offer 25 bps below standard"

# The text of every member, in order. The API sends it, clients match it, and the migration's CHECK lists and
# the seed store it, so a renamed value breaks the database and every client at once. Mutation testing cannot
# change module-level values, so this test pins them, without needing a database.
WIRE_TEXT: list[tuple[type[StrEnum], list[str]]] = [
    (Role, ["RELATIONSHIP_MANAGER", "REVIEWER", "MORTGAGE_PROCESS"]),
    (Status, ["PENDING_REVIEW", "APPROVED", "DECLINED"]),
    (Outcome, ["APPROVED", "DECLINED"]),
    (EventType, ["SUBMITTED", "AMENDED", "RESUBMITTED", "APPROVED", "DECLINED"]),
    (Action, ["create", "decide", "revise"]),
]


@pytest.mark.parametrize(("enum", "texts"), WIRE_TEXT, ids=[enum.__name__ for enum, _ in WIRE_TEXT])
def test_every_enum_member_keeps_its_wire_text(enum: type[StrEnum], texts: list[str]) -> None:
    assert [member.value for member in enum] == texts


def broken_fields(build: Callable[[], object]) -> set[str]:
    """The fields a value object refuses, or an empty set when it builds."""
    try:
        build()
    except ValidationFailed as failed:
        return set(failed.problems)
    return set()


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    ("discount", "reason", "broken"),
    [
        pytest.param(1, REASON, set(), id="1 bps, the lowest"),
        pytest.param(100, REASON, set(), id="100 bps, the highest"),
        pytest.param(0, REASON, {"discount_bps"}, id="0 bps"),
        pytest.param(-5, REASON, {"discount_bps"}, id="negative"),
        pytest.param(101, REASON, {"discount_bps"}, id="101 bps"),
        pytest.param(True, REASON, {"discount_bps"}, id="true is not a number"),
        pytest.param(25.0, REASON, {"discount_bps"}, id="a float is not whole basis points"),
        pytest.param(25, "x" * 1000, set(), id="reason of 1000 characters"),
        pytest.param(25, "x" * 1001, {"reason"}, id="reason of 1001 characters"),
        pytest.param(25, " " + "x" * 1000, {"reason"}, id="padding counts towards the length"),
        pytest.param(25, "", {"reason"}, id="empty reason"),
        pytest.param(25, "   ", {"reason"}, id="reason of spaces"),
        pytest.param(25, "\t\r\n", {"reason"}, id="reason of a tab and newlines"),
        pytest.param(25, chr(0x00A0) + chr(0x2003), {"reason"}, id="reason of no-break and em spaces"),
        pytest.param(25, "Retention" + chr(0) + "offer", {"reason"}, id="NUL, which PostgreSQL text refuses"),
        pytest.param(25, "Retention " + chr(0xD800), {"reason"}, id="a lone surrogate: not UTF-8"),
        pytest.param(0, "  ", {"discount_bps", "reason"}, id="both broken, both reported"),
    ],
)
def test_terms_bounds(discount: int, reason: str, broken: set[str]) -> None:
    """1 to 100 basis points; a reason that is not blank once trimmed and at most 1,000 characters."""
    assert broken_fields(lambda: Terms(discount, reason)) == broken


@pytest.mark.req(id="FR-1")
@pytest.mark.parametrize(
    ("discount", "reason", "problems"),
    [
        pytest.param(
            0,
            REASON,
            {"discount_bps": "must be a whole number from 1 to 100 basis points"},
            id="a discount out of range",
        ),
        pytest.param(25, "  ", {"reason": "must not be blank"}, id="a blank reason"),
        pytest.param(25, "Retention" + chr(0), {"reason": "must not contain the NUL character"}, id="a NUL"),
        pytest.param(
            25, "Retention " + chr(0xD800), {"reason": "must be valid Unicode text"}, id="a lone surrogate"
        ),
        pytest.param(
            25, "x" * 1001, {"reason": "must be at most 1000 characters"}, id="a reason that is too long"
        ),
        pytest.param(
            0,
            "  ",
            {
                "discount_bps": "must be a whole number from 1 to 100 basis points",
                "reason": "must not be blank",
            },
            id="both broken, both explained",
        ),
    ],
)
def test_terms_say_what_is_wrong(discount: int, reason: str, problems: dict[str, str]) -> None:
    """The 422 lists each broken field with its reason in words, and the page shows those words, so they are
    pinned exactly (a mutation run found them untested)."""
    with pytest.raises(ValidationFailed) as failed:
        Terms(discount, reason)
    assert failed.value.problems == problems


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("outcome", "comment", "broken"),
    [
        pytest.param(Outcome.APPROVED, None, set(), id="approve without a comment"),
        pytest.param(Outcome.APPROVED, "Within policy", set(), id="approve with a comment"),
        pytest.param(Outcome.APPROVED, "   ", {"comment"}, id="approve with a blank comment"),
        pytest.param(Outcome.APPROVED, "x" * 1001, {"comment"}, id="approve, comment of 1001 characters"),
        pytest.param(Outcome.DECLINED, "Up to 20 bps acceptable", set(), id="decline with a comment"),
        pytest.param(Outcome.DECLINED, "x" * 1000, set(), id="decline, comment of 1000 characters"),
        pytest.param(Outcome.DECLINED, None, {"comment"}, id="decline without a comment"),
        pytest.param(Outcome.DECLINED, "", {"comment"}, id="decline with an empty comment"),
        pytest.param(Outcome.DECLINED, " \n ", {"comment"}, id="decline with a blank comment"),
        pytest.param(Outcome.DECLINED, "x" * 1001, {"comment"}, id="decline, comment of 1001 characters"),
        pytest.param(Outcome.APPROVED, "Within" + chr(0), {"comment"}, id="approve, comment with a NUL"),
        pytest.param(Outcome.DECLINED, "Up to" + chr(0xDC00), {"comment"}, id="decline, a lone surrogate"),
    ],
)
def test_comment_rules(outcome: Outcome, comment: str | None, broken: set[str]) -> None:
    """A decline needs a comment; any comment that is sent follows the same text rules as the reason."""
    assert broken_fields(lambda: Verdict(outcome, comment)) == broken


@pytest.mark.req(id="FR-3")
@pytest.mark.parametrize(
    ("outcome", "comment", "problems"),
    [
        pytest.param(Outcome.DECLINED, None, {"comment": "is required to decline"}, id="decline, no comment"),
        pytest.param(Outcome.APPROVED, "  ", {"comment": "must not be blank"}, id="approve, blank comment"),
    ],
)
def test_verdict_says_what_is_wrong(outcome: Outcome, comment: str | None, problems: dict[str, str]) -> None:
    """Like the terms, the words of each refusal are pinned exactly."""
    with pytest.raises(ValidationFailed) as failed:
        Verdict(outcome, comment)
    assert failed.value.problems == problems


@pytest.mark.req(id="FR-1")
def test_text_is_kept_exactly_as_sent() -> None:
    reason, comment = "  Retention offer \n", "\tUp to 20 bps acceptable "
    assert Terms(25, reason).reason == reason
    assert Verdict(Outcome.DECLINED, comment).comment == comment
