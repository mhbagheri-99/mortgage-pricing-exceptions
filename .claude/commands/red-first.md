---
description: Write the failing test first for one rule, show it fail for the right reason, then make it pass
argument-hint: <requirement id and rule, for example "FR-3 four-eyes">
---

Work test-first on: $ARGUMENTS

1. Name the requirement id from `docs/requirements.md` and the behaviour the test proves. If no id fits, stop
   and ask the human.
2. Write the test or tests only, each marked `@pytest.mark.req(id="...")`. Do not touch production code yet.
3. Run only those tests and show the output. They must fail because the behaviour is missing, not because of an
   import error or a typo. Fix the test until it fails for the right reason.
4. Commit the failing tests on their own, as `test(<id>): <behaviour>`. The commit needs the human's approval.
5. Write the smallest code that makes the tests pass. Run them, then `uv run python scripts/check.py --fast`.
6. Never change a test to make it pass. If the test itself was wrong, say why and ask before changing it.
