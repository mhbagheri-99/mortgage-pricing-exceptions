"""Stop hook: the agent may not finish while a fast gate fails (design doc D10).

It runs `scripts/check.py --fast` (lint, format, types, layers, requirement ids, skip markers, the tests that
need no Docker). A failure exits with code 2, which keeps the agent working and shows it the failing gates.
It never blocks twice in a row (`stop_hook_active`), and it skips the run when git shows nothing changed.
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TAIL_LINES = 40
# The gates must finish inside the hook's own 300 s limit; the variable exists so the tests can shorten it.
GATE_SECONDS = int(os.environ.get("FINISH_CHECK_SECONDS", "280"))


def changes_pending(project: Path) -> bool:
    """True unless git says the working tree is clean. Outside a git repository, assume changes."""
    try:
        status = subprocess.run(
            ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except OSError, subprocess.TimeoutExpired:
        return True
    return status.returncode != 0 or bool(status.stdout.strip())


def log(project: Path, summary: str) -> None:
    logs = project / ".claude" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "guard": "finish-check",
        "decision": "block",
        "reason": summary,
    }
    with (logs / "guardrails.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry) + "\n")


def main() -> int:
    event = json.load(sys.stdin)
    if event.get("stop_hook_active"):
        return 0
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or str(event.get("cwd") or ".")).resolve()
    if not changes_pending(project):
        return 0
    try:
        result = subprocess.run(
            [sys.executable, str(project / "scripts" / "check.py"), "--fast"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=GATE_SECONDS,
        )
    except subprocess.TimeoutExpired:  # fail closed: a hung gate must not let the agent stop
        log(project, f"the gates did not finish within {GATE_SECONDS} s")
        print(
            f"Finish check: the gates did not finish within {GATE_SECONDS} s; find out why.", file=sys.stderr
        )
        return 2
    if result.returncode == 0:
        return 0
    output = (result.stdout + result.stderr).strip().splitlines()
    failed = [line for line in output if line.startswith("FAIL")]
    log(project, "; ".join(failed) or "check.py failed")
    print(
        "Finish check: a gate is red, so keep working until it is green.\n" + "\n".join(output[-TAIL_LINES:]),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
