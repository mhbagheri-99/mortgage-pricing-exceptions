"""Every commit the tracked documents cite is in this branch's history.

The documents cite commits as short hashes in backticks, 7 to 40 hex characters. Each must name exactly
one commit (git refuses an ambiguous one), and that commit must be an ancestor of the checked-out commit:
an object left behind by rewritten history still exists locally, but no clone would have it. A made-up
hash in the account of how the work was done fails here, in the finish check and in CI, which checks out
the whole history (fetch-depth: 0).
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CITED = re.compile(r"`([0-9a-f]{7,40})`")


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)


def cited_commits(text: str) -> set[str]:
    return set(CITED.findall(text))


def in_history(short: str) -> bool:
    commit = git("rev-parse", "--verify", "--quiet", f"{short}^{{commit}}")
    return (
        commit.returncode == 0
        and git("merge-base", "--is-ancestor", commit.stdout.strip(), "HEAD").returncode == 0
    )


def unknown(hashes: set[str]) -> list[str]:
    """The hashes that name no commit in this branch's history, or more than one."""
    return sorted(h for h in hashes if not in_history(h))


def test_every_commit_the_documents_cite_exists() -> None:
    assert git("rev-parse", "--is-shallow-repository").stdout.strip() == "false", (
        "Not a full git checkout (no history, or a shallow clone): check out with fetch-depth: 0"
    )
    # Tracked Markdown, plus new files not yet added; the ignore and exclude rules keep local notes out.
    documents = git("ls-files", "--cached", "--others", "--exclude-standard", "*.md").stdout.split()
    cited = {document: cited_commits((ROOT / document).read_text(encoding="utf-8")) for document in documents}
    assert sum(len(hashes) for hashes in cited.values()) > 0
    assert {document: unknown(hashes) for document, hashes in cited.items() if unknown(hashes)} == {}


def test_a_made_up_hash_is_caught() -> None:
    head = git("rev-parse", "--short=7", "HEAD").stdout.strip()
    made_up = "0" * 40
    text = f"Merged as `{head}`; fixed in `{made_up}`; ten is `10`."
    assert cited_commits(text) == {head, made_up}
    assert unknown(cited_commits(text)) == [made_up]
