"""The race barrier: wait for a condition, with a deadline, never a fixed sleep.

This file holds only the polling helper. Its 10 ms pause between polls is the one `time.sleep` the lint rule
allows, through a per-file ignore in pyproject.toml that lifts every banned API here, so nothing else belongs
in this file.
"""

import time
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, text

POLL_SECONDS = 0.01
DEADLINE_SECONDS = 5.0

# Anyone waiting on a lock that the holder's connection holds: a row lock, or an uncommitted duplicate key.
WAITING_ON = text("SELECT count(*) FROM pg_stat_activity WHERE :holder = ANY(pg_blocking_pids(pid))")


def wait_for(condition: Callable[[], bool], what: str, deadline: float = DEADLINE_SECONDS) -> None:
    """Return as soon as `condition()` holds; after `deadline` seconds, fail the test, naming `what`."""
    give_up = time.monotonic() + deadline
    while not condition():
        if time.monotonic() > give_up:
            pytest.fail(f"waited {deadline:.0f} s for {what}, and it never happened")
        time.sleep(POLL_SECONDS)


def wait_until_blocked(engine: Engine, holder: int) -> None:
    """Return once some connection is waiting on connection `holder` (its pg_backend_pid()).

    Each poll is its own transaction (autocommit): PostgreSQL keeps one snapshot of pg_stat_activity for a
    whole transaction, so polling inside one transaction would never see a call that started waiting after the
    first poll.
    """
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as poll:
        wait_for(
            lambda: poll.execute(WAITING_ON, {"holder": holder}).scalar_one() > 0,
            f"a call to wait on connection A (pid {holder})",
        )
