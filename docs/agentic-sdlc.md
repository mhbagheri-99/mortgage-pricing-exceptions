# Agentic SDLC

AI agents wrote the code, the tests and these documents. This page says how that was controlled: the problem each
control addresses, how we know it works, what it caught, and what risk remains. Every commit cited here exists in
this repository's history; a test fails the build if one does not (`tests/agentic/test_cited_commits.py`).

## How the work was run

- **Design first.** Day 1 produced a design, agreed with the human section by section. Research agents read current
  versions from official sources; four reviewer agents attacked every decision of an earlier draft; two skeptic
  agents attacked the reviewers; two agents tried to break the two biggest decisions (the natural key and the row
  lock) by running races on a throwaway PostgreSQL. The design document itself is a private working file; the
  decisions it settled are published as the [ADRs](adr/), [architecture](architecture.md) and
  [production readiness](production-readiness.md).
- **Build in phases.** Day 2 built in phases, each in its own Claude Code session and its own pull request, merged
  with a merge commit so every cited hash survives. Each phase opened with its open decisions, brought by the agent
  as options with a recommendation; **the human decided every one, and approved every commit and push.** The agent
  wrote all code, tests and documents.
- **Who ran what.** One Claude Code session per phase, on Claude Opus, did the work. The read-only reviewer agent and the parallel
  side sessions (four in the hardening phase, one for these documents, one for the release check) ran on Claude
  Sonnet, each side session touching only its own files and committing nothing; the phase session reviewed their
  diffs and made every commit.
  The independent audit ran on Claude Fable, in a fresh clone, with only the client's needs and the repository.
- **Guardrails first.** The core controls (`AGENTS.md`, the approval rules, the two hooks, the gates, red first and
  the reviewer agent) went in during the first phase, before any feature code, so everything after was built under
  them; guardrails added at the end would have guarded nothing. The red runs, mutation testing, the independent
  audit and the cited-commit check came later, each in the phase that could use it.
- **Where the evidence lives.** The commits, the tests and these documents are in the repository. The guardrail log
  is local (ignored by git), and the raw output of the red runs and the review tables are kept with the working
  notes; this page summarises them. Every red run can be repeated: make the temporary edit, run the tests.

## The controls

| Control | Problem it addresses | How it is verified | What it caught | Remaining risk |
| --- | --- | --- | --- | --- |
| [`AGENTS.md`](../AGENTS.md), loaded by `CLAUDE.md` | Agents relearn conventions every session and drift from them | Advice only, which is why the rest exists | n/a | Followed mostly, not always (see "What the agents got wrong") |
| Approval rules (`.claude/settings.json`): asking before an edit to `.claude/`, `scripts/check.py`, `.github/`, `pyproject.toml`, `uv.lock` or `docs/requirements.md`, before adding, removing or upgrading a package, and before every commit and push; refusing force push, hard reset and force-add | An agent stuck on a red check "fixes" the check instead of the code, adds a dependency casually, or destroys work | A live run after the first phase: the rules refused `git push --force` and `git -C . push -f`, and an edit to `pyproject.toml` raised the approval prompt | It prevents rather than catches; the live run is its evidence | Rules match the command text, so an unusual spelling can slip past; a shell write (`sed -i`) bypasses file rules; the human may approve without reading |
| Test-integrity guard (`.claude/hooks/test_integrity.py`), before every edit under `tests/` | Making a failing test pass by skipping it or deleting its asserts | It parses the file before and after: an added skip or xfail is refused; a removed assert, `pytest.raises`, test function or requirement marker asks the human; it fails closed (an unreadable file, a crash or an unknown editing tool asks). Its own tests feed it sample edits | 2 refusals (both deliberate probes of a skip) and 3 questions: two FR-1 requirement markers removed from a domain test file on purpose (approved), one assert replaced by a plain call (approved) | An assert weakened in place (`==` to `>=`) keeps the count; an edit made through the shell is never seen. Three such shell edits by the agent are listed below |
| Finish check (`.claude/hooks/finish_check.py`), when the agent stops | The agent says "done" while a gate is red | It runs the fast gates and blocks the stop when one fails; its tests cover pass, block, a hung gate and the loop guard | 4 blocks: a deliberate `import sqlalchemy` in the domain, shown to the human after the first phase (repeat it by adding that import and ending a turn), a red test left in the tree, and two lint failures from parallel sessions | It blocks once in a row, so a second stop goes through (audit F34); it runs only inside Claude Code, so CI repeats every gate |
| The gates (`scripts/check.py`), run by the finish check, by `/check` and by CI | Stale or forbidden APIs, layer violations, untyped code, invented or forgotten requirements, and ways round a guard | ruff (with banned APIs: psycopg2, the ORM and query builder, `pydantic.v1`, `utcnow`, `time.sleep`), mypy strict, three import-linter contracts, the requirement-id gate, scans for skip markers, overriding config files, non-standard domain imports, `noqa` comments that hide a banned API and `conftest.py` hooks that hide tests; the test run fails if any test was skipped, xfailed, deselected, or written but never run. Each scan has its own tests | A stale import-linter cache reporting a removed import (now run without a cache); the audit's three one-line ways to hide a failing test (F19), fixed red first in `4ec0c00` and `655af94` | An early `return` in a test still passes; code running inside the test process can always tamper with what it reports (see "Remaining risks") |
| Requirement-id gate | Agents silently drop or invent requirements | Every test that proves a requirement carries its id; the gate fails on an unknown id, and on a requirement with no test once its phase is finished | Reviews, not the gate, found two markers that would have let it pass without the real test: N9 on a settings test (removed in the first phase, before the guard was live in the session) and FR-1 on domain tests (removed through the guard's two questions) | It proves a test exists, not that it is strong; mutation testing and review cover strength |
| Red first (`/red-first`) | Tests written after the code mirror the code | The history shows each failing test committed before the code that passes it; no commit that passes a failing test weakened it (three of them also touched tests, said below) | 13 pairs, listed below | A wrong specification gives confidently wrong tests |
| Red runs | A test that cannot fail proves nothing | The guard itself is removed as a temporary edit, the test is watched failing, the guard is restored and `git diff` on the source is checked empty | See "Red runs" | Done by hand, for chosen guards |
| Mutation testing (mutmut, domain only) | Agent-written tests that run code but check little | Small deliberate bugs in the domain's functions; each surviving one becomes a stronger test, seen failing first | 46 weak spots, all closed (see "Mutation testing") | The domain only, and a report, not a gate |
| Read-only reviewer agent (`.claude/agents/rules-reviewer.md`, `/review-rules`) after each phase | A solo developer has no second reviewer | It may only read; each finding is fixed or rejected with a reason, checked against the files first | 44 findings over nine passes, below | The same model family shares blind spots; its own invariant on the order of checks was stale until F7 |
| Automated security reviews of the first phase | Ways for an agent to dodge the gates | Reviews of the guardrail commits and of the first push | The first two found sidesteps, fixed in `f8b04c1` and `b9a0678`; a third found more ways for code inside the test process to fool the gates, which is where the scans stop; the review of the push found the demo port open on every network interface, fixed in `e3b78c9` | The point where we stopped adding scans is explained below |
| Independent audit against the client's needs | The agents and the reviewer share the design's assumptions | Another model, in a fresh clone, with no design document | 43 findings, 8 medium, none high; every one checked against the files before it was accepted ([build history](history.md#the-audits-findings)) | One audit, by a model of the same vendor |
| Release check | The README reads well to the people who wrote it, who know what it leaves out; a secret or private file reaches the public history | A side session with nothing but a fresh clone followed the README word for word and timed it. gitleaks scanned a mirror of every branch and pull request, and a sweep read every path, file, commit message and pull-request text for private files and words; each scanner was first shown catching a planted case | Every step passed in under 4 minutes on a warm machine; 6 unclear places in the README, none blocking, and 2 more found while checking them, all fixed; both scans clean | One run, on one machine, with the images already downloaded |
| The human | Everything above can be approved without reading | Every decision, commit and push needed the human's go-ahead | n/a | Fatigue at the end of a long day |

The **guardrail log** (`.claude/logs/guardrails.jsonl`, local, ignored by git) holds one line per refusal,
question and block. It held 9 lines at the end of the hardening phase: 2 refusals and 3 questions from the test
guard, and 4 blocks from the finish check, each listed above.

## Red first, in the history

| Failing tests | Code that passes them | What |
| --- | --- | --- |
| `752d1bd` | `5d4d369` | The decide transition table and value rules |
| `980c018` | `db912d5` | NUL in text refused; free text kept out of `repr` |
| `087c271` | `691b4dc` | Create with replay, get with history, the queue, the approved discount |
| `555aa37` | `95cd080` | Lone surrogates, non-UTC cursors, database error text in logs |
| `b0ac39a` | `d2f879b` | Revise and decide under a row lock |
| `11091e0` | `3e88cb8` | The page and its security headers |
| `87e8bef` | `88488e2` | A request says what its caller may do |
| `a83febd` | `1d46b67` | The list says whether its caller may create |
| `60bd73f` | `ca003be` | The page's files revalidated on every load |
| `41b07e9` | `81a47a0` | The audit's API findings |
| `4ec0c00` | `655af94` | The test gate counts tests written but never run |
| `9b30136` | `a47f338` | The test guard triggers for every file-editing tool |
| `f335f47` | `36b6b6d` | The seeded review queue spans more than one page |

Red commits carry a skeleton with no behaviour, so a test fails on the missing behaviour rather than an import
error. The first red commit's message overstated this (F35): of its 69 failing cases, 51 failed on the skeleton's
`NotImplementedError` and 18 on an assertion. Both are the missing behaviour, but the message said every one failed
on its own assertion. History is never rewritten, so this page states it precisely.

In three pairs (`4ec0c00` and `655af94`, `9b30136` and `a47f338`, `41b07e9` and `81a47a0`) both commits were made
seconds apart in one group the human approved, so the history shows their order but not a separate run; the
failing run was watched before the commit.

In three other pairs the passing commit also changed test files, without weakening a failing test: `db912d5`
reflowed a docstring; `88488e2` adapted two older tests to the new `permissions` field (a replay is read back by
the same caller, whose permissions it carries, and a 409's `current` is compared without them); `ca003be` added
eight more banned markup sinks to the page test, a stronger check. The other ten passing commits leave `tests/`
untouched (`git diff <red> <green> -- tests/`).

Where the behaviour already existed (the race tests, the backstops, the demo scenario), the tests went in green and
red runs showed they bite.

## Red runs

| Guard removed (temporary edit) | Result |
| --- | --- |
| `FOR NO KEY UPDATE` dropped from the row lock | 6 of the 11 cases then in `test_races.py` failed (a twelfth, the pool time limit, came later). Two reviewers: a 500, from the decision index. **Three silent lost updates, each answered 200**: an old revision approved over an amendment, an approval overwritten by an amendment, and a change after a decline landing as an amendment instead of a resubmission |
| The engine at REPEATABLE READ instead of READ COMMITTED | 7 failed: every waiter got 503, a serialization failure (40001), the create replay among them |
| `lock_timeout` removed | Both lock-wait cases failed: the wait ended at the 3-second statement limit (57014) instead of 2 seconds (55P03) |
| `transaction_timeout` removed | The stuck-transaction test failed: the transaction was never ended |
| A bare `ON CONFLICT DO NOTHING` | The check that the create names its conflict constraint failed |
| An inline script, an inline style, an `onclick`, `innerHTML` or `DOMParser` in the page; the page's policy sent on every response | Each caught by its test |
| The replay header removed; refused writes logged at INFO; a drifted status or body in `demo.http` | Each caught by the demo-scenario tests |
| A made-up hash, and a commit left behind by rewritten history, cited in this page | Both caught by the cited-commit test |

The three lost updates are the strongest evidence in this repository: without the row lock, the service answered
200 to changes it then silently lost, and no database constraint noticed.

## Mutation testing

mutmut 3.8.0 made small deliberate bugs ("mutants") in the domain (`domain/rules.py` and `model.py`) and ran the
domain tests against each, on Linux in a throwaway container. A mutant the tests miss "survives". mutmut changes
code inside functions only, so all 229 came from `rules.py`; `model.py` holds only data classes and enums.

| Run | Mutants | Caught | Survived | What the survivors were |
| --- | --- | --- | --- | --- |
| First | 229 | 182 | 47 | 36 the words of a refusal, 8 the status or action named in a refusal, 2 a retry check with no submission event, 1 equivalent |
| After 18 new test cases (`8e4f6a1`) | 229 | 228 | 1 | Equivalent: `"utf-8"` written as `"UTF-8"`, which Python treats the same |

No survivor was a wrong rule: the state machine, the discount limits, four-eyes and the revision arithmetic were
all caught on the first run. mutmut cannot change module-level values, so a hand check made 22 mutants of them
(limits, tables and enum texts): 16 were caught, and the 6 that survived the domain tests were enum texts, which
the integration tests catch. A test now pins every enum's text (`771b692`), seen failing on all six. The README has the command that reruns the report.

## Reviews

| Phase | Findings | Fixed | Rejected, with the reason |
| --- | --- | --- | --- |
| Skeleton and guardrails | 11 | 10 | 1: the database backstop tests belonged to a later phase, and were written there |
| Domain core | 5 | 3 | 2: the gate proves each id has a test by design; a non-UTC `now` is still stored as the right instant |
| Create and read | 6 | 6 | 0 |
| Revise and decide | 4 | 3, and 1 in part | Part of 1: one conflict test deliberately covers every 409's `current` |
| Proving the guarantees | 3 | 3 | 0 |
| The page (two passes) | 6 | 6 | 0 |
| Final pass over the whole codebase | 5 | 5 | 0 |
| The release check | 4 | 4 | 0 |

The final pass covered every tracked file and confirmed the invariants it checks: the layers, static SQL only, the
plain conflict target, READ COMMITTED, the lock by primary key, every response built by `read_request()`, the
decision columns, the clock, the time limits, no skip, mock or sleep but the race barrier's, and no reason or
comment in any log line.

## What the agents got wrong, and what caught it

Each of these is real, found by a gate, a test, a review, the audit or the agent itself.

- **Assumptions about libraries.** A first-phase spike found that FastAPI reads the JSON before the identity check,
  so unparseable JSON gets 400, not 401; the order of checks was changed to match. A test found that with the
  database unreachable one call hung for 130 seconds, because the pool limit does not cover opening a connection;
  `connect_timeout` was added.
- **Input that reached the database as a 500.** A NUL character and a lone surrogate in a reason passed the rules
  and failed in PostgreSQL or the driver; both are now 422, red first.
- **Private text in logs.** A database error's message, which holds the bound values, reached the log; the engine now
  hides parameters and the handler logs the error class only.
- **A test helper that could never see the wait.** The race barrier first polled `pg_stat_activity` inside one
  transaction, which keeps one snapshot, so it never saw the waiting call; a spike found it before the tests were
  written, and the barrier polls in autocommit.
- **Tests that proved less than their names.** A retry test sent the amended terms instead of the current ones
  (F20); the history test never showed two decisions (F23); the pool-timeout test sent a read while claiming a
  refused write. The first two were each shown passing on a mutant before they were fixed.
- **The page.** Five bugs no test covered were found in the browser (the word "null" on screen, a refusal out of
  sight, a squeezed history, a growing gap, doubled brackets); a reload restored untouched fields and could have
  reverted another user's change; quick switches could draw stale rows (F38).
- **The guardrails themselves.** The test guard was triggered only for Edit and Write, not MultiEdit or
  NotebookEdit; the reviewer's own order of checks was stale (F7); "worst case 8 seconds" was untrue with a full
  pool, because the demo identity takes a connection of its own (now stated: up to 3 seconds more).
- **The agent's own slips.** Three times a test file was edited through the shell, which the test guard cannot see:
  appending new tests (first phase); a scripted edit that removed an `assert callable(...)` the agent had just added
  (third phase); and `ruff format` on a new file (whitespace only, hardening phase). Each was found by the agent and
  reported. A shell write also damaged a working file; it was restored from a backup. The rule now: test edits go through the file-editing tools only, and new tests are formatted
  in a scratch copy and compared with the guard's own counter before they are applied.

## Remaining risks

- **Shell edits.** The hooks see the file-editing tools and the command text only. A shell command that writes a
  file, or an unusual spelling of a git command, can slip past them. CI repeats every gate on a clean checkout.
- **A weakened assert, or an early `return`.** The test guard counts asserts; it cannot see `==` become `>=`. The
  test gate counts tests that ran; a test that returns before its asserts still passes. Review and mutation testing
  are the defence.
- **Hostile code inside the test process.** Each automated security review found another way for code running inside
  pytest to fool what pytest reports. We stopped adding scans there: the gates defend against an agent quietly
  weakening tests, not against deliberately hostile test code, and code inside a process can always tamper with what
  that process reports. The backstop is CI on a clean checkout and the human's review of every pull request.
- **One block.** The finish check blocks a stop once, then lets the next one through, so it never traps the agent in
  a loop (F34).
- **Shared blind spots.** The reviewer and the builder are models of the same family; the audit used another model
  of the same vendor.
- **The size of it** (F43). The guardrails and their tests are a real part of the codebase. Each control above is
  tied to a failure it prevents or caught; anything that caught nothing and prevents nothing specific should go.
- **Approval rules depend on the session's permission mode.** While these documents were written, a side session in
  the app's auto mode edited four guarded files (comments in the two hooks, `scripts/check.py` and
  `pyproject.toml`) and reported that no approval prompt appeared. Those edits were comment-only, and a comparison
  of the code with comments and docstrings stripped showed no other change before they were committed. The gates, CI
  and the human's review of every commit are the backstop; a session that must ask should run in a mode that asks.
- **The human.** Every control ends in a human approval, which is only as good as the reading behind it.

## The history

Commit messages and code comments name build phases (P1 to P8b), design decisions (D1 to D14) and audit
findings (F1 to F43). [Build history](history.md) says what each one is, and what became of every audit
finding.
