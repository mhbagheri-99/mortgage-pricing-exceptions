"""The checks inside scripts/check.py: the requirement-id gate, the skip-marker scan, and the scans that stop
a guardrail being sidestepped (an overriding config file, a domain import, a silenced lint rule).

The gate fails at once on a test naming an unknown id, and on a requirement with no test once the phase that
should first test it is finished. scripts/check.py holds the last finished phase, so moving it needs approval.
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

REQUIREMENTS = """| Id | Requirement | Where it comes from | First tested in |
| --- | --- | --- | --- |
| FR-1 | Create a request | Client need | P2 |
| FR-2 | Change a request | One stakeholder | P4 |
"""
TESTS_FR1 = 'import pytest\n\n\n@pytest.mark.req(id="FR-1")\ndef test_create():\n    assert True\n'


def make_project(root: Path, test_source: str) -> Path:
    (root / "docs").mkdir()
    (root / "docs" / "requirements.md").write_text(REQUIREMENTS, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text(test_source, encoding="utf-8")
    return root


def test_a_test_naming_an_unknown_id_fails_at_once(tmp_path: Path) -> None:
    project = make_project(
        tmp_path, TESTS_FR1 + '\n\n@pytest.mark.req(id="FR-99")\ndef test_other():\n    assert True\n'
    )
    assert check.check_requirements(project, last_finished=0) == [
        "tests/test_a.py names FR-99, which docs/requirements.md does not list"
    ]


def test_an_untested_requirement_fails_once_its_phase_is_finished(tmp_path: Path) -> None:
    project = make_project(tmp_path, TESTS_FR1)
    assert check.check_requirements(project, last_finished=3) == []
    assert check.check_requirements(project, last_finished=4) == [
        "FR-2 has no test, and its phase P4 is finished"
    ]


def test_the_real_requirements_list_is_complete_and_passes() -> None:
    gated = {f"FR-{n}" for n in range(1, 8)} | {"N1", "N2", "N3", "N4", "N5", "N8", "N9"}
    assert set(check.read_requirements(ROOT)) == gated
    assert check.check_requirements(ROOT, last_finished=check.LAST_FINISHED_PHASE) == []


def test_ci_rejects_any_skip_marker(tmp_path: Path) -> None:
    project = make_project(
        tmp_path, TESTS_FR1.replace("def test_create", "@pytest.mark.skip\ndef test_create")
    )
    assert check.find_skip_markers(project) == ["tests/test_a.py has 1 skip or xfail marker(s)"]


@pytest.mark.parametrize(
    "name",
    [
        "pytest.ini",
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        "setup.cfg",
        "tox.ini",
        ".importlinter",
        "conftest.py",
        "tests/pytest.ini",
    ],
)
def test_a_config_file_that_would_override_pyproject_fails(tmp_path: Path, name: str) -> None:
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("", encoding="utf-8")
    assert check.find_shadow_config(tmp_path) == [f"{name} would override the settings in pyproject.toml"]


def test_the_domain_may_import_only_the_standard_library(tmp_path: Path) -> None:
    domain = tmp_path / "src" / "pricing_exceptions" / "domain"
    domain.mkdir(parents=True)
    (domain / "rules.py").write_text(
        "import dataclasses\nfrom datetime import UTC\nfrom pricing_exceptions.domain import model\n"
        "from . import errors\nimport httpx\nfrom sqlalchemy import text\n",
        encoding="utf-8",
    )
    assert check.find_domain_imports(tmp_path) == [
        "src/pricing_exceptions/domain/rules.py:5 imports httpx, which is not the standard library",
        "src/pricing_exceptions/domain/rules.py:6 imports sqlalchemy, which is not the standard library",
    ]


def test_a_comment_that_silences_the_banned_api_rule_fails(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text(
        "import time  # noqa: TID251\n"
        "x = 1  # noqa\n"
        "# ruff: noqa\n"
        "y = '# noqa: TID251 inside a string is fine'\n"
        "z = 2  # noqa: S104\n",
        encoding="utf-8",
    )
    assert check.find_lint_escapes(tmp_path) == [
        "src/a.py:1 silences the banned-API rule",
        "src/a.py:2 has a bare noqa, which silences every rule",
        "src/a.py:3 switches lint off for the whole file",
    ]


def test_a_conftest_hook_that_can_hide_tests_fails(tmp_path: Path) -> None:
    (tmp_path / "tests" / "integration").mkdir(parents=True)
    (tmp_path / "tests" / "integration" / "conftest.py").write_text(
        "def pytest_configure(config):\n    pass\n\n\n"
        "def pytest_collection_modifyitems(config, items):\n    items[:] = []\n",
        encoding="utf-8",
    )
    hidden = "pytest_collection_modifyitems, which can hide tests from the run"
    assert check.find_collection_hooks(tmp_path) == [f"tests/integration/conftest.py defines {hidden}"]


def test_the_test_run_fails_when_any_test_does_not_run(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "import pytest\n\n\ndef test_runs():\n    assert True\n\n\n"
        "def test_dodges_the_static_scan():\n    getattr(pytest, 'sk' + 'ip')('later')\n",
        encoding="utf-8",
    )
    ok, output = check.run_tests(tmp_path, ["tests"])
    assert not ok
    assert "every test must run" in output.lower()


def test_the_real_repository_passes_the_sidestep_scans() -> None:
    assert check.find_shadow_config(ROOT) == []
    assert check.find_domain_imports(ROOT) == []
    assert check.find_lint_escapes(ROOT) == []
    assert check.find_collection_hooks(ROOT) == []
