"""The finish check keeps the agent working while a gate fails, and never blocks twice in a row.

Each test builds a throwaway project whose scripts/check.py is a stand-in that passes or fails on demand,
then runs the real Stop hook against it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "finish_check.py"

STAND_IN = """import pathlib, sys, threading
pathlib.Path(__file__).with_name("ran").write_text("yes")
if {hang}:
    threading.Event().wait(30)
print("FAIL  mypy: src/x.py:1: error: invented attribute" if {fail} else "PASS  every gate")
sys.exit(1 if {fail} else 0)
"""


def make_project(root: Path, *, fail: bool, hang: bool = False) -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "check.py").write_text(STAND_IN.format(fail=fail, hang=hang), encoding="utf-8")
    return root


def run_hook(
    project: Path, *, stop_hook_active: bool = False, gate_seconds: int | None = None
) -> subprocess.CompletedProcess[str]:
    event = {"hook_event_name": "Stop", "stop_hook_active": stop_hook_active, "cwd": str(project)}
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    if gate_seconds is not None:
        env["FINISH_CHECK_SECONDS"] = str(gate_seconds)
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_blocks_stopping_while_a_gate_fails(tmp_path: Path) -> None:
    project = make_project(tmp_path, fail=True)
    result = run_hook(project)
    assert result.returncode == 2
    assert "FAIL  mypy" in result.stderr
    entry = json.loads((project / ".claude" / "logs" / "guardrails.jsonl").read_text(encoding="utf-8"))
    assert (entry["guard"], entry["decision"]) == ("finish-check", "block")


def test_blocks_stopping_when_the_gates_hang(tmp_path: Path) -> None:
    project = make_project(tmp_path, fail=False, hang=True)
    result = run_hook(project, gate_seconds=2)
    assert result.returncode == 2
    assert "did not finish" in result.stderr


def test_lets_the_agent_stop_when_every_gate_passes(tmp_path: Path) -> None:
    project = make_project(tmp_path, fail=False)
    result = run_hook(project)
    assert result.returncode == 0
    assert (project / "scripts" / "ran").exists()


@pytest.mark.parametrize(
    "reason", ["already continuing after a block", "nothing changed since the last commit"]
)
def test_does_not_run_the_gates_when_it_should_not(tmp_path: Path, reason: str) -> None:
    project = make_project(tmp_path, fail=True)
    if reason == "nothing changed since the last commit":
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        # Ignore everything, so the stand-in project shows git no changes.
        (project / ".gitignore").write_text("*\n", encoding="utf-8")
        result = run_hook(project)
    else:
        result = run_hook(project, stop_hook_active=True)
    assert result.returncode == 0
    assert not (project / "scripts" / "ran").exists()
