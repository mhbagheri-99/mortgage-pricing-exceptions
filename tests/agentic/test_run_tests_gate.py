"""The test-run gate in scripts/check.py: every test function written in a collected file must show up in
pytest's report (audit finding F19).

Three one-line edits used to hide a failing, requirement-marked test from every gate while the run stayed
green: `__test__ = False` in a test module, a `collect_ignore` list in `tests/conftest.py`, and
`pytest.exit(..., returncode=0)` in an earlier test. The gate now lists the test functions written in the
source, with `ast`, and compares them with the JUnit report. An early `return` inside a test is still not
caught.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check", ROOT / "scripts" / "check.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load_check()

PASSING = "def test_runs():\n    assert True\n"
FAILING = 'import pytest\n\n\n@pytest.mark.req(id="FR-1")\ndef test_create():\n    assert False\n'
EVERY_KIND = """import pytest


def test_at_module_level():
    def test_nested_helper():
        raise AssertionError("a function inside a function is not a test")

    assert callable(test_nested_helper)


class TestGroup:
    def test_in_a_class(self):
        assert True

    class TestInner:
        def test_in_a_nested_class(self):
            assert True


@pytest.mark.parametrize("n", [1, 2])
def test_with_parameters(n):
    assert n


@pytest.fixture
def test_data():
    return 1


def test_uses_the_data(test_data):
    assert test_data


try:
    import json

    def test_under_a_try():
        assert json
except ImportError:
    pass

if True:

    def test_under_an_if():
        assert True
"""

# Each case writes these files into a throwaway project, and a failing test hides in one of them. A passing
# test always runs too, so the run itself is not empty.
HIDDEN = {
    "a module switched off with __test__": (
        {"tests/test_ok.py": PASSING, "tests/test_hidden.py": "__test__ = False\n\n\n" + FAILING},
        "tests/test_hidden.py::test_create",
    ),
    "a class switched off with __test__": (
        {
            "tests/test_ok.py": PASSING,
            "tests/test_hidden.py": "class TestHidden:\n    __test__ = False\n\n    def test_create(self):\n"
            "        assert False\n",
        },
        "tests/test_hidden.py::TestHidden::test_create",
    ),
    "a collect_ignore list in tests/conftest.py": (
        {
            "tests/conftest.py": 'collect_ignore = ["test_hidden.py"]\n',
            "tests/test_ok.py": PASSING,
            "tests/test_hidden.py": FAILING,
        },
        "tests/test_hidden.py::test_create",
    ),
    "pytest.exit with return code 0 in an earlier test": (
        {
            "tests/test_a_first.py": "import pytest\n\n\n" + PASSING + "\n\n"
            "def test_stops_the_run():\n    pytest.exit('stop here', returncode=0)\n",
            "tests/test_b_hidden.py": FAILING,
        },
        "tests/test_b_hidden.py::test_create",
    ),
}


def write_project(root: Path, files: dict[str, str]) -> Path:
    for relative, source in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(source, encoding="utf-8")
    return root


@pytest.mark.parametrize("case", HIDDEN)
def test_a_test_hidden_from_the_run_fails_the_gate(tmp_path: Path, case: str) -> None:
    files, unrun = HIDDEN[case]
    ok, output = check.run_tests(write_project(tmp_path, files), ["tests"])
    assert not ok
    assert "every test function must run" in output.lower()
    assert unrun in output


def test_every_kind_of_test_function_that_runs_passes(tmp_path: Path) -> None:
    """The control: a class, a nested class, parameters, an `if` and a `try` all run and are all reported, and
    neither a fixture named test_data nor a function inside a function counts as a test."""
    ok, output = check.run_tests(write_project(tmp_path, {"tests/test_kinds.py": EVERY_KIND}), ["tests"])
    assert ok, output


def test_only_the_files_pytest_collects_are_counted(tmp_path: Path) -> None:
    """With python_files set in pyproject.toml, a test_*.py file is not collected, so it is not counted."""
    project = write_project(
        tmp_path,
        {
            "pyproject.toml": '[tool.pytest.ini_options]\npython_files = ["check_*.py"]\n',
            "tests/check_real.py": PASSING,
            "tests/test_not_collected.py": FAILING,
        },
    )
    ok, output = check.run_tests(project, ["tests"])
    assert ok, output
