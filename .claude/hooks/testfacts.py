"""Facts about a test file, read from its syntax tree rather than its text.

Shared by the test-integrity hook and scripts/check.py. Parsing means the word "skip" inside a string or a
comment never counts; only real skip markers, asserts, test functions and requirement markers do.
"""

import ast
from collections import Counter
from dataclasses import dataclass, field

SKIP_NAMES = frozenset(
    {
        "pytest.mark.skip",
        "pytest.mark.skipif",
        "pytest.mark.xfail",
        "pytest.skip",
        "pytest.xfail",
        "pytest.importorskip",
        "unittest.skip",
        "unittest.skipIf",
        "unittest.skipUnless",
        "unittest.expectedFailure",
        "unittest.SkipTest",
        "pytest.skip.Exception",
    }
)
SKIP_METHODS = frozenset({"skipTest"})  # self.skipTest() in unittest-style tests
CHECK_CALLS = frozenset({"pytest.raises", "pytest.warns"})
REQ_MARKER = "pytest.mark.req"


@dataclass(frozen=True)
class FileFacts:
    skips: int = 0
    checks: int = 0  # assert statements plus pytest.raises and pytest.warns
    tests: int = 0
    req_ids: Counter[str] = field(default_factory=Counter)


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names to what they stand for: `from pytest import mark` and `m = pytest.mark` both make a
    name mean `pytest.mark`."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _dotted(node.value, names) if isinstance(node.value, ast.Attribute | ast.Name) else None
            if value:
                names[node.targets[0].id] = value
    return names


def _dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(aliases.get(node.id, node.id))
    return ".".join(reversed(parts))


def _is_skip(node: ast.AST, aliases: dict[str, str], inner: set[int]) -> bool:
    if isinstance(node, ast.Attribute) and node.attr in SKIP_METHODS:
        return True
    return (
        isinstance(node, ast.Attribute | ast.Name)
        and id(node) not in inner
        and _dotted(node, aliases) in SKIP_NAMES
    )


def facts(source: str) -> FileFacts | None:
    """Return the facts of a Python test file, or None when it does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    aliases = _aliases(tree)
    skips = checks = tests = 0
    req_ids: Counter[str] = Counter()
    inner: set[int] = set()  # attribute nodes that are part of a longer chain, so a chain counts once
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            inner.add(id(node.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            checks += 1
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
            tests += 1
        elif _is_skip(node, aliases, inner):
            skips += 1
        if isinstance(node, ast.Call):
            name = _dotted(node.func, aliases)
            if name in CHECK_CALLS:
                checks += 1
            elif name == REQ_MARKER:
                for keyword in node.keywords:
                    if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                        req_ids[str(keyword.value.value)] += 1
    return FileFacts(skips=skips, checks=checks, tests=tests, req_ids=req_ids)
