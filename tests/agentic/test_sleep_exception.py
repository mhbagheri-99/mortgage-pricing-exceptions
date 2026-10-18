"""The one time.sleep exception (docs/architecture.md, "Testing") stays one exception.

The per-file ignore for tests/integration/races.py switches off the whole banned-API rule (TID251) in that
file, not only time.sleep, and no lint run looks there again. These tests lint the barrier's source as if it
lived in any other file, and allow exactly one banned call: its 10 ms pause.
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BARRIER = ROOT / "tests" / "integration" / "races.py"
SLEEP = "`time.sleep` is banned"


def banned_apis(source: str) -> list[str]:
    """ruff's banned-API findings for `source`, linted under a name that no per-file ignore covers."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "TID251",
            "--output-format",
            "json",
            "--stdin-filename",
            "tests/integration/not_the_barrier.py",
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    return [finding["message"] for finding in json.loads(result.stdout)]


def test_the_barrier_uses_no_banned_api_but_its_one_sleep() -> None:
    found = banned_apis(BARRIER.read_text(encoding="utf-8"))
    assert len(found) == 1
    assert found[0].startswith(SLEEP)


def test_another_banned_api_in_the_barrier_would_be_found() -> None:
    """The check bites: a banned import added to the barrier's file is a second finding."""
    found = banned_apis(BARRIER.read_text(encoding="utf-8") + "\nfrom sqlalchemy import select\n")
    assert len(found) == 2
    assert [message for message in found if not message.startswith(SLEEP)] == [
        "`sqlalchemy.select` is banned: Write SQL with sqlalchemy.text(), not the query builder."
    ]
