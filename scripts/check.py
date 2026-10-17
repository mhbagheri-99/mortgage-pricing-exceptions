"""Every gate in one place. CI runs `--all`, the finish check runs `--fast`, and /check runs either.

Gates: ruff lint and format, mypy (strict on src), import-linter's layer contracts, the requirement-id gate,
the skip-marker scan, the sidestep scans, and the tests (`--fast`: the ones that need no Docker; `--all`:
every test). `--check-reqs` runs the requirement-id gate alone.
"""

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import textwrap
import tokenize
from collections.abc import Callable, Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))

from testfacts import facts  # noqa: E402

# The last finished build phase. Every requirement whose first-tested phase is at or before it must have a
# test. Moving it on needs your approval: this file is a guardrail file, so editing it asks you first.
LAST_FINISHED_PHASE = 1

REQUIREMENT_ROW = re.compile(r"^\|\s*(FR-\d+|N\d+)\s*\|.*\|\s*P(\d)\s*\|$")
FAST_TEST_DIRS = ("tests/domain", "tests/agentic")


def read_requirements(root: Path) -> dict[str, int]:
    """Requirement id to the phase that first tests it, read from docs/requirements.md."""
    text = (root / "docs" / "requirements.md").read_text(encoding="utf-8")
    rows = (REQUIREMENT_ROW.match(line.strip()) for line in text.splitlines())
    return {row.group(1): int(row.group(2)) for row in rows if row}


def test_files(root: Path) -> list[Path]:
    tests = root / "tests"
    return sorted(tests.rglob("*.py")) if tests.is_dir() else []


def check_requirements(root: Path, last_finished: int) -> list[str]:
    known = read_requirements(root)
    tested: set[str] = set()
    errors = []
    for path in test_files(root):
        found = facts(path.read_text(encoding="utf-8"))
        if found is None:
            continue
        file = path.relative_to(root).as_posix()
        for req_id in sorted(found.req_ids):
            tested.add(req_id)
            if req_id not in known:
                errors.append(f"{file} names {req_id}, which docs/requirements.md does not list")
    for req_id, phase in known.items():
        if phase <= last_finished and req_id not in tested:
            errors.append(f"{req_id} has no test, and its phase P{phase} is finished")
    return errors


def find_skip_markers(root: Path) -> list[str]:
    errors = []
    for path in test_files(root):
        found = facts(path.read_text(encoding="utf-8"))
        if found is not None and found.skips:
            errors.append(f"{path.relative_to(root).as_posix()} has {found.skips} skip or xfail marker(s)")
    return errors


# ---- Sidestep scans: ways round a guardrail that the guardrail itself cannot see ------------------------
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "private",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".import_linter_cache",
        "build",
        "dist",
        "node_modules",
    }
)
# Each of these would take precedence over the guarded settings in pyproject.toml for its tool.
SHADOW_CONFIG = frozenset(
    {
        "pytest.ini",
        "ruff.toml",
        ".ruff.toml",
        "mypy.ini",
        ".mypy.ini",
        "setup.cfg",
        "tox.ini",
        ".importlinter",
    }
)
BARE_NOQA = re.compile(r"#\s*noqa\b(?!\s*:)", re.IGNORECASE)
CODED_NOQA = re.compile(r"#\s*noqa\s*:(?P<codes>[^#]*)", re.IGNORECASE)
FILE_NOQA = re.compile(r"#\s*(ruff|flake8)\s*:\s*noqa\b", re.IGNORECASE)


def walk(root: Path) -> Iterator[Path]:
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in SKIP_DIRS)
        for name in sorted(files):
            yield Path(directory) / name


def find_shadow_config(root: Path) -> list[str]:
    errors = []
    for path in walk(root):
        relative = path.relative_to(root).as_posix()
        if path.name in SHADOW_CONFIG or relative == "conftest.py":
            errors.append(f"{relative} would override the settings in pyproject.toml")
    return errors


def find_domain_imports(root: Path) -> list[str]:
    """The domain may import only the standard library and itself (import-linter can only list names)."""
    domain = root / "src" / "pricing_exceptions" / "domain"
    errors = []
    for path in sorted(domain.rglob("*.py")) if domain.is_dir() else []:
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                own = name == "pricing_exceptions.domain" or name.startswith("pricing_exceptions.domain.")
                if not own and name.split(".")[0] not in sys.stdlib_module_names:
                    errors.append(
                        (
                            node.lineno,
                            f"{relative}:{node.lineno} imports {name}, which is not the standard library",
                        )
                    )
    return [message for _, message in sorted(errors)]


def find_lint_escapes(root: Path) -> list[str]:
    """A noqa comment can silence the banned-API rule, and ruff reports only unused ones."""
    errors = []
    for path in walk(root):
        if path.suffix != ".py":
            continue
        relative = path.relative_to(root).as_posix()
        try:
            with tokenize.open(path) as source:
                comments = [
                    t for t in tokenize.generate_tokens(source.readline) if t.type == tokenize.COMMENT
                ]
        except SyntaxError, tokenize.TokenError:
            continue  # ruff reports a file that does not parse
        for comment in comments:
            line = comment.start[0]
            coded = CODED_NOQA.search(comment.string)
            if FILE_NOQA.search(comment.string):
                errors.append(f"{relative}:{line} switches lint off for the whole file")
            elif BARE_NOQA.search(comment.string):
                errors.append(f"{relative}:{line} has a bare noqa, which silences every rule")
            elif coded and "TID251" in coded.group("codes").upper():
                errors.append(f"{relative}:{line} silences the banned-API rule")
    return errors


# pytest hooks that can drop, deselect or relabel tests before anyone sees them.
HIDING_HOOKS = frozenset(
    {
        "pytest_ignore_collect",
        "pytest_collect_file",
        "pytest_pycollect_makeitem",
        "pytest_collection_modifyitems",
        "pytest_deselected",
        "pytest_runtest_setup",
        "pytest_runtest_makereport",
        "pytest_report_teststatus",
        "pytest_terminal_summary",
    }
)
NOT_RUN = re.compile(r"(\d+) (skipped|xfailed|xpassed|deselected)")


def find_collection_hooks(root: Path) -> list[str]:
    errors = []
    for path in walk(root):
        if path.name != "conftest.py":
            continue
        relative = path.relative_to(root).as_posix()
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef) and node.name in HIDING_HOOKS:
                errors.append(f"{relative} defines {node.name}, which can hide tests from the run")
    return errors


def tool(*command: str, cwd: Path = ROOT) -> tuple[bool, str]:
    result = subprocess.run(
        list(command), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def run_tests(root: Path, test_dirs: list[str]) -> tuple[bool, str]:
    """Run the tests, and fail when any of them did not run: this catches a skip however it was written."""
    ok, output = tool(sys.executable, "-m", "pytest", "-q", "-rsxX", *test_dirs, cwd=root)
    summary = output.splitlines()[-1] if output else ""
    not_run = NOT_RUN.findall(summary)
    if ok and not_run:
        counts = ", ".join(f"{n} {kind}" for n, kind in not_run)
        return False, f"{output}\nEvery test must run, but {counts}."
    return ok, output


def listed(errors: list[str]) -> tuple[bool, str]:
    return not errors, "\n".join(errors)


def gates(mode: str) -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    python = sys.executable
    scripts = str(Path(python).parent)
    lint_imports = (
        shutil.which("lint-imports", path=scripts) or shutil.which("lint-imports") or "lint-imports"
    )
    test_dirs = ["tests"] if mode == "all" else [d for d in FAST_TEST_DIRS if (ROOT / d).is_dir()]
    requirements = ("requirement ids", lambda: listed(check_requirements(ROOT, LAST_FINISHED_PHASE)))
    if mode == "check-reqs":
        return [requirements]
    return [
        ("ruff lint", lambda: tool(python, "-m", "ruff", "check", ".")),
        ("ruff format", lambda: tool(python, "-m", "ruff", "format", "--check", ".")),
        ("mypy", lambda: tool(python, "-m", "mypy")),
        # No cache: a stale cache once reported an import that had already been removed (P1).
        ("import-linter", lambda: tool(lint_imports, "--no-cache")),
        requirements,
        ("skip markers", lambda: listed(find_skip_markers(ROOT))),
        (
            "sidestep scans",
            lambda: listed(
                find_shadow_config(ROOT)
                + find_domain_imports(ROOT)
                + find_lint_escapes(ROOT)
                + find_collection_hooks(ROOT)
            ),
        ),
        ("tests", lambda: run_tests(ROOT, test_dirs)),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--fast", action="store_const", const="fast", dest="mode", help="gates that need no Docker"
    )
    mode.add_argument("--all", action="store_const", const="all", dest="mode", help="every gate, as in CI")
    mode.add_argument(
        "--check-reqs",
        action="store_const",
        const="check-reqs",
        dest="mode",
        help="the requirement-id gate alone",
    )
    args = parser.parse_args(argv)
    failed = 0
    for name, run in gates(args.mode):
        ok, output = run()
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failed += 1
            print(textwrap.indent(output or "(no output)", "      "))
    print(f"\n{failed} gate(s) failed" if failed else "\nevery gate passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
