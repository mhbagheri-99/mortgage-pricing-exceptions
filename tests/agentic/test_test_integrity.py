"""The test-integrity guard refuses skips, asks before a test loses checks, and lets normal work through.

Each test runs the real hook script as Claude Code would: a JSON event on stdin, then exit code and output.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "test_integrity.py"

SAMPLE = """import pytest


def test_total():
    assert 1 + 1 == 2
    with pytest.raises(ZeroDivisionError):
        1 / 0


@pytest.mark.req(id="FR-3")
def test_marked():
    assert True
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    return tmp_path


def run_guard(
    project: Path, tool_name: str, tool_input: dict[str, object]
) -> subprocess.CompletedProcess[str]:
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": str(project),
    }
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project)}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def edit(project: Path, old: str, new: str) -> subprocess.CompletedProcess[str]:
    path = project / "tests" / "test_sample.py"
    return run_guard(project, "Edit", {"file_path": str(path), "old_string": old, "new_string": new})


def write(project: Path, relative: str, content: str) -> subprocess.CompletedProcess[str]:
    return run_guard(project, "Write", {"file_path": str(project / relative), "content": content})


SKIPS = {
    "skip decorator": ("def test_total():", "@pytest.mark.skip(reason='later')\ndef test_total():"),
    "skipif decorator": ("def test_total():", "@pytest.mark.skipif(True, reason='flaky')\ndef test_total():"),
    "xfail decorator": ("def test_total():", "@pytest.mark.xfail\ndef test_total():"),
    "skip call": ("    assert 1 + 1 == 2", "    pytest.skip('later')\n    assert 1 + 1 == 2"),
    "importorskip call": ("import pytest\n", "import pytest\n\npytest.importorskip('nothing_here')\n"),
    "skip imported by name": (
        "import pytest\n",
        "import pytest\nfrom pytest import skip\n\nskip('later', allow_module_level=True)\n",
    ),
    "skip through an alias": ("def test_total():", "m = pytest.mark\n\n\n@m.skip\ndef test_total():"),
    "pytest.skip.Exception raised": ("    assert True\n", "    raise pytest.skip.Exception('later')\n"),
    "unittest.SkipTest raised": (
        "    assert True\n",
        "    import unittest\n\n    raise unittest.SkipTest('later')\n",
    ),
    "self.skipTest called": ("    assert True\n", "    self.skipTest('later')\n    assert True\n"),
}


@pytest.mark.parametrize("case", SKIPS)
def test_refuses_an_added_skip(project: Path, case: str) -> None:
    old, new = SKIPS[case]
    result = edit(project, old, new)
    assert result.returncode == 2
    assert "skip" in result.stderr.lower() or "xfail" in result.stderr.lower()


def test_refuses_a_skip_written_as_a_whole_file(project: Path) -> None:
    result = write(
        project, "tests/test_sample.py", SAMPLE.replace("def test_total", "@pytest.mark.skip\ndef test_total")
    )
    assert result.returncode == 2
    assert "skip" in result.stderr.lower()


LOSSES = {
    "an assert": ("    assert 1 + 1 == 2\n", "", "assert"),
    "a pytest.raises check": ("    with pytest.raises(ZeroDivisionError):\n        1 / 0\n", "", "assert"),
    "a test function": (
        '\n\n@pytest.mark.req(id="FR-3")\ndef test_marked():\n    assert True\n',
        "\n",
        "test",
    ),
    "a requirement marker": ('@pytest.mark.req(id="FR-3")\n', "", "FR-3"),
}


@pytest.mark.parametrize("case", LOSSES)
def test_asks_you_before_a_test_loses(project: Path, case: str) -> None:
    old, new, named = LOSSES[case]
    result = edit(project, old, new)
    assert result.returncode == 0
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "ask"
    assert named in decision["permissionDecisionReason"]


def test_asks_you_when_the_edit_leaves_the_file_unparsable(project: Path) -> None:
    result = edit(project, "    assert True\n", "    assert (\n")
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "ask"
    assert "parse" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "broken",
    ["def test_total(:\n    assert True\n", b"\xff\xfe not utf-8 \x80\n"],
    ids=["a file that does not parse", "a file that is not UTF-8"],
)
def test_asks_you_when_the_current_file_cannot_be_read(project: Path, broken: str | bytes) -> None:
    path = project / "tests" / "test_sample.py"
    if isinstance(broken, bytes):
        path.write_bytes(broken)
        old = " not utf-8 "
    else:
        path.write_text(broken, encoding="utf-8")
        old = "def test_total(:"
    result = edit(project, old, "def test_total():")
    assert result.returncode == 0
    assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


NORMAL = {
    "an added assert": ("tests/test_sample.py", "    assert True\n", "    assert True\n    assert 2 > 1\n"),
    "a renamed test": ("tests/test_sample.py", "def test_marked():", "def test_marked_again():"),
    "skip only inside a string and a comment": (
        "tests/test_sample.py",
        "    assert True\n",
        "    assert True  # pytest.mark.skip is not used here\n    assert 'pytest.skip()' != ''\n",
    ),
}


@pytest.mark.parametrize("case", NORMAL)
def test_lets_normal_edits_through(project: Path, case: str) -> None:
    _, old, new = NORMAL[case]
    result = edit(project, old, new)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize(
    "relative",
    ["tests/test_new.py", "src/app.py", "tests/data.json"],
    ids=["a new test file", "a file outside tests", "a file that is not Python"],
)
def test_lets_other_files_through(project: Path, relative: str) -> None:
    result = write(
        project,
        relative,
        "import pytest\n\n\n@pytest.mark.skip\ndef test_later():\n    pass\n"
        if relative == "src/app.py"
        else "def test_new():\n    assert True\n",
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_logs_each_refusal_and_question(project: Path) -> None:
    edit(project, "def test_total():", "@pytest.mark.skip\ndef test_total():")
    edit(project, "    assert 1 + 1 == 2\n", "")
    edit(project, "def test_marked():", "def test_marked_again():")
    lines = (project / ".claude" / "logs" / "guardrails.jsonl").read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [(e["guard"], e["decision"], e["file"]) for e in entries] == [
        ("test-integrity", "deny", "tests/test_sample.py"),
        ("test-integrity", "ask", "tests/test_sample.py"),
    ]
