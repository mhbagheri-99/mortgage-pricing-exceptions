"""The value objects check themselves when built (design doc 3.4, 5.5 step 4): a bad value is a 422 before any
lock is taken. Text is checked but never rewritten: it is kept exactly as sent, so a retried create compares
equal (5.7). The limits are our assumptions; the brief sets none.

The terms tests carry no FR-1 marker yet: FR-1 is first proven by P3's create API test, and a marker here would
let the requirement gate pass without it (P2 review finding 2). P3 marks them once that test exists.
"""

from collections.abc import Callable

import pytest

from pricing_exceptions.domain.errors import ValidationFailed
from pricing_exceptions.domain.model import Outcome
from pricing_exceptions.domain.rules import Terms, Verdict

REASON = "Retention: competitor offer 25 bps below standard"


def broken_fields(build: Callable[[], object]) -> set[str]:
    """The fields a value object refuses, or an empty set when it builds."""
    try:
        build()
    except ValidationFailed as failed:
        return set(failed.problems)
    return set()


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
        pytest.param(0, "  ", {"discount_bps", "reason"}, id="both broken, both reported"),
    ],
)
def test_terms_bounds(discount: int, reason: str, broken: set[str]) -> None:
    """1 to 100 basis points; a reason that is not blank once trimmed and at most 1,000 characters."""
    assert broken_fields(lambda: Terms(discount, reason)) == broken


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
    ],
)
def test_comment_rules(outcome: Outcome, comment: str | None, broken: set[str]) -> None:
    """A decline needs a comment; any comment that is sent follows the same text rules as the reason."""
    assert broken_fields(lambda: Verdict(outcome, comment)) == broken


def test_text_is_kept_exactly_as_sent() -> None:
    reason, comment = "  Retention offer \n", "\tUp to 20 bps acceptable "
    assert Terms(25, reason).reason == reason
    assert Verdict(Outcome.DECLINED, comment).comment == comment
