"""Every gate in one place. CI runs `--all`, the finish check runs `--fast`, and /check runs either.

Gates: ruff lint and format, mypy (strict on src), import-linter's layer contracts, the requirement-id gate,
the skip-marker scan, the sidestep scans, and the tests (`--fast`: the ones that need no Docker; `--all`:
every test). `--check-reqs` runs the requirement-id gate alone.
"""

import argparse
import ast
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import tokenize
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))

from testfacts import facts  # noqa: E402

# The last finished build phase. Every requirement whose first-tested phase is at or before it must have a
# test. Moving it on needs your approval: this file is a guardrail file, so editing it asks you first.
LAST_FINISHED_PHASE = 5

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
        "pytest_cmdline_main",
        "pytest_load_initial_conftests",
        "pytest_configure",
        "pytest_sessionstart",
        "pytest_collection",
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
DESELECTED = re.compile(r"\b(\d+) deselected\b")
HIDES = "which can hide tests from the run"


def _is_plugin_registration(node: ast.AST) -> bool:
    """`config.pluginmanager.register(...)` or `pluginmanager.register(...)`."""
    if not (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register"
    ):
        return False
    owner = node.func.value
    return (isinstance(owner, ast.Attribute) and owner.attr == "pluginmanager") or (
        isinstance(owner, ast.Name) and owner.id == "pluginmanager"
    )


def find_collection_hooks(root: Path) -> list[str]:
    """Any conftest.py, and any file under tests/, that defines a hiding hook or loads a plugin."""
    errors = []
    for path in walk(root):
        relative = path.relative_to(root).as_posix()
        if path.suffix != ".py" or not (path.name == "conftest.py" or relative.startswith("tests/")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # ruff reports a file that does not parse
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names = [node.name]
            elif isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names = [node.target.id]
            for name in names:
                if name in HIDING_HOOKS:
                    errors.append(f"{relative} defines {name}, {HIDES}")
                elif name == "pytest_plugins":
                    errors.append(f"{relative} loads pytest plugins, {HIDES}")
            if _is_plugin_registration(node):
                errors.append(f"{relative} loads a pytest plugin at run time, {HIDES}")
    return errors


def tool(*command: str, cwd: Path = ROOT) -> tuple[bool, str]:
    result = subprocess.run(
        list(command), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def python_file_patterns(root: Path) -> list[str]:
    """pytest's `python_files` setting from pyproject.toml, or pytest's own default when it is not set."""
    try:
        options = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError, tomllib.TOMLDecodeError:
        options = {}
    patterns = options.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("python_files")
    if patterns is None:
        return ["test_*.py", "*_test.py"]
    return patterns.split() if isinstance(patterns, str) else list(patterns)


def collected_files(root: Path, test_dirs: list[str]) -> list[Path]:
    """The files pytest collects for these arguments: one named in full, or a match for `python_files`."""
    patterns = python_file_patterns(root)

    def matches(path: Path) -> bool:
        return any(
            fnmatch.fnmatch(path.as_posix(), f"*/{pattern}")  # a pattern with a separator matches the path
            if "/" in pattern
            else fnmatch.fnmatch(path.name, pattern)
            for pattern in patterns
        )

    files: set[Path] = set()
    for argument in test_dirs:
        path = root / argument
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(found for found in path.rglob("*.py") if matches(found))
    return sorted(files)


def _is_fixture(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """`@fixture`, `@pytest.fixture` and `@pytest.fixture(...)`: pytest does not collect these as tests."""
    targets = (d.func if isinstance(d, ast.Call) else d for d in function.decorator_list)
    return any(
        (isinstance(t, ast.Attribute) and t.attr == "fixture")
        or (isinstance(t, ast.Name) and t.id == "fixture")
        for t in targets
    )


def _written_tests(node: ast.AST, scope: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """The test functions written under `node`, each as its class names and then its own name.

    A test function is named test_* and sits at module level or in a class named Test*, also under an if, a
    try, a with or a loop. One inside another function is no test, and nor is a fixture named test_*.
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            if child.name.startswith("test_") and not _is_fixture(child):
                yield (*scope, child.name)
        elif isinstance(child, ast.ClassDef):
            if child.name.startswith("Test"):
                yield from _written_tests(child, (*scope, child.name))
        else:
            yield from _written_tests(child, scope)


def written_test_functions(root: Path, test_dirs: list[str]) -> dict[tuple[str, str], str]:
    """Each test function written in the collected files: JUnit's (classname, name) to `file::Class::name`."""
    written: dict[tuple[str, str], str] = {}
    for path in collected_files(root, test_dirs):
        relative = path.relative_to(root).as_posix()
        module = relative.removesuffix(".py").replace("/", ".")  # how pytest forms a JUnit classname
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError, ValueError:
            continue  # pytest reports a file that does not parse, and the run fails on that
        for *classes, name in _written_tests(tree):
            written[(".".join([module, *classes]), name)] = "::".join([relative, *classes, name])
    return written


def run_tests(root: Path, test_dirs: list[str]) -> tuple[bool, str]:
    """Run the tests, and fail unless every test function written in the collected files ran.

    It fails on three things, and only these:
    - a test that pytest reports as skipped or xfailed, however the skip was written, and a deselected test;
    - a test function that is written in a collected file but missing from the report: switched off with
      `__test__ = False`, left out by `collect_ignore` or a collection hook, or never reached because an
      earlier test called `pytest.exit(returncode=0)`. The source is read with `ast` and compared with the
      JUnit report, so the report cannot be argued with from inside the run;
    - an empty run, or a run that wrote no report.

    It does not catch a test that returns early, or one whose asserts are weakened. A test function written
    under a condition that is false on the machine, such as `if sys.platform == "win32":`, fails it too.
    The counts come from pytest's JUnit XML report, not the console, so stray output cannot hide them.
    """
    # Read as the run starts, not after it: the run can take minutes, and the files may change meanwhile.
    written = written_test_functions(root, test_dirs)
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.xml"
        ok, output = tool(
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rsxX",
            f"--rootdir={root}",
            f"--junitxml={report}",
            *test_dirs,
            cwd=root,
        )
        if not ok:
            return False, output
        if not report.exists():
            return False, f"{output}\nThe test run wrote no report, so its result cannot be trusted."
        top = ElementTree.parse(report).getroot()  # noqa: S314 (our own pytest run wrote this file)
    suites = [top] if top.tag == "testsuite" else list(top.iter("testsuite"))
    ran = sum(int(suite.get("tests", "0")) for suite in suites)
    skipped = sum(int(suite.get("skipped", "0")) for suite in suites)  # xfailed tests count as skipped
    deselected = sum(int(n) for n in DESELECTED.findall(output))
    if ran == 0:
        return False, f"{output}\nNo test ran."
    if skipped or deselected:
        return (
            False,
            f"{output}\nEvery test must run, but {skipped} skipped or xfailed, {deselected} deselected.",
        )
    # JUnit's classname is the file's dotted path and the test's class names; its name carries any parameters.
    reported = {
        (case.get("classname", ""), case.get("name", "").partition("[")[0]) for case in top.iter("testcase")
    }
    never_ran = sorted(shown for key, shown in written.items() if key not in reported)
    if never_ran:
        listing = "\n".join(f"  {name}" for name in never_ran)
        return False, f"{output}\nEvery test function must run, but these never ran:\n{listing}"
    return True, output


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
        # No cache: a stale cache once reported an import that had already been removed.
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
