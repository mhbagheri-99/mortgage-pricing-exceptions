"""PreToolUse hook on Edit and Write: guard the tests against being weakened to make them pass.

Rule (design doc D10 and 10.2):
- an edit that adds a skip or xfail marker or call is refused (exit code 2; the agent is told why);
- an edit that removes an assert or pytest.raises, a test function or a requirement marker asks the human;
- everything else passes silently.
It compares the file before and after the edit by parsing both, and logs every refusal and question.
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from testfacts import FileFacts, facts


def project_dir(event: dict[str, object]) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or str(event.get("cwd") or ".")).resolve()


def test_file(project: Path, file_path: str) -> str | None:
    """Return the path relative to the project if it is a Python file under tests/, else None."""
    try:
        relative = Path(file_path).resolve().relative_to(project)
    except ValueError:
        return None
    if relative.parts[:1] != ("tests",) or relative.suffix != ".py":
        return None
    return relative.as_posix()


def file_after(path: Path, tool_name: str, tool_input: dict[str, object]) -> tuple[str, str] | None:
    """Return (before, after) for the edit, or None when the edit cannot apply (the tool will report it)."""
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    if tool_name == "Write":
        return before, str(tool_input.get("content", ""))
    old, new = str(tool_input.get("old_string", "")), str(tool_input.get("new_string", ""))
    if not old or old not in before:
        return None
    count = -1 if tool_input.get("replace_all") else 1
    return before, before.replace(old, new, count)


def judge(before: FileFacts, after: FileFacts) -> tuple[str, str] | None:
    if after.skips > before.skips:
        return "deny", (
            "Refused: this edit adds a skip or xfail marker to a test. Fix the code or the test's setup "
            "instead; a skipped test proves nothing. If a skip is truly needed, ask the human."
        )
    losses = []
    if after.checks < before.checks:
        losses.append(f"{before.checks - after.checks} assert or pytest.raises check(s) removed")
    if after.tests < before.tests:
        losses.append(f"{before.tests - after.tests} test function(s) removed")
    lost_ids = before.req_ids - after.req_ids
    if lost_ids:
        losses.append("requirement marker(s) removed: " + ", ".join(sorted(lost_ids)))
    if losses:
        return "ask", "This edit weakens a test: " + "; ".join(losses) + ". Approve only if that is intended."
    return None


def log(project: Path, decision: str, file: str, reason: str) -> None:
    logs = project / ".claude" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "guard": "test-integrity",
        "decision": decision,
        "file": file,
        "reason": reason,
    }
    with (logs / "guardrails.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry) + "\n")


def check(event: dict[str, object], project: Path) -> tuple[str, str, str] | None:
    """Return (decision, file, reason) for an edit that needs one, or None to let it through."""
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None
    file = test_file(project, str(tool_input.get("file_path", "")))
    if file is None:
        return None
    if tool_name not in ("Edit", "Write"):  # fail closed on a file-editing tool this guard does not model
        return (
            "ask",
            file,
            f"The guard does not know the {tool_name} tool, so it cannot check this edit to a test.",
        )
    try:
        texts = file_after(project / file, tool_name, tool_input)
    except UnicodeDecodeError:
        return "ask", file, "The guard cannot read this test file as UTF-8, so it cannot check the edit."
    if texts is None:
        return None
    before, after = facts(texts[0]), facts(texts[1])
    if after is None:
        return "ask", file, "The test file would not parse after this edit, so the guard cannot check it."
    if before is None and texts[0]:
        return (
            "ask",
            file,
            "The test file does not parse now, so the guard cannot tell what this edit removes.",
        )
    verdict = judge(before or FileFacts(), after)
    return (verdict[0], file, verdict[1]) if verdict else None


def main() -> int:
    project = Path.cwd()
    file = "?"
    try:
        event = json.load(sys.stdin)
        project = project_dir(event)
        outcome = check(event, project)
    except Exception as error:  # fail closed: a guard that crashes would let the edit through
        outcome = ("ask", file, f"The test-integrity guard failed ({type(error).__name__}: {error}).")
    if outcome is None:
        return 0
    decision, file, reason = outcome
    log(project, decision, file, reason)
    if decision == "deny":
        print(reason, file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
