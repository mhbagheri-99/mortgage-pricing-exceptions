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
- **Who ran what.** One Claude Code session per phase did the work. The read-only reviewer agent and the parallel
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
| Finish check (`.claude/hooks/finish_check.py`), when the agent stops | The agent says "done" while a gate is red | It runs the fast gates and blocks the stop when one fails; its tests cover pass, block, a hung gate and the loop guard | 4 blocks: the live demo (`import sqlalchemy` in the domain), a red test left in the tree, and two lint failures from parallel sessions | It blocks once in a row, so a second stop goes through (audit F34); it runs only inside Claude Code, so CI repeats every gate |
| The gates (`scripts/check.py`), run by the finish check, by `/check` and by CI | Stale or forbidden APIs, layer violations, untyped code, invented or forgotten requirements, and ways round a guard | ruff (with banned APIs: psycopg2, the ORM and query builder, `pydantic.v1`, `utcnow`, `time.sleep`), mypy strict, three import-linter contracts, the requirement-id gate, scans for skip markers, overriding config files, non-standard domain imports, `noqa` comments that hide a banned API and `conftest.py` hooks that hide tests; the test run fails if any test was skipped, xfailed, deselected, or written but never run. Each scan has its own tests | A stale import-linter cache reporting a removed import (now run without a cache); the audit's three one-line ways to hide a failing test (F19), fixed red first in `4ec0c00` and `655af94` | An early `return` in a test still passes; code running inside the test process can always tamper with what it reports (see "Remaining risks") |
| Requirement-id gate | Agents silently drop or invent requirements | Every test that proves a requirement carries its id; the gate fails on an unknown id, and on a requirement with no test once its phase is finished | Reviews, not the gate, found two markers that would have let it pass without the real test: N9 on a settings test (removed in the first phase, before the guard was live in the session) and FR-1 on domain tests (removed through the guard's two questions) | It proves a test exists, not that it is strong; mutation testing and review cover strength |
| Red first (`/red-first`) | Tests written after the code mirror the code | The history shows each failing test committed before the code that passes it; the reviewer checked that no test changed between the two | 12 pairs, listed below | A wrong specification gives confidently wrong tests |
| Red runs | A test that cannot fail proves nothing | The guard itself is removed as a temporary edit, the test is watched failing, the guard is restored and `git diff` on the source is checked empty | See "Red runs" | Done by hand, for chosen guards |
| Mutation testing (mutmut, domain only) | Agent-written tests that run code but check little | Small deliberate bugs in the domain's functions; each surviving one becomes a stronger test, seen failing first | 46 weak spots, all closed (see "Mutation testing") | The domain only, and a report, not a gate |
| Read-only reviewer agent (`.claude/agents/rules-reviewer.md`, `/review-rules`) after each phase | A solo developer has no second reviewer | It may only read; each finding is fixed or rejected with a reason, checked against the files first | 40 findings over eight passes, below | The same model family shares blind spots; its own invariant on the order of checks was stale until F7 |
| Automated security reviews of the first phase | Ways for an agent to dodge the gates | Reviews of the guardrail commits and of the first push | The first two found sidesteps, fixed in `f8b04c1` and `b9a0678`; a third found more ways for code inside the test process to fool the gates, which is where the scans stop; the review of the push found the demo port open on every network interface, fixed in `e3b78c9` | The point where we stopped adding scans is explained below |
| Independent audit against the client's needs | The agents and the reviewer share the design's assumptions | Another model, in a fresh clone, with no design document | 43 findings, 8 medium, none high; every one checked against the files before it was accepted (appendix) | One audit, by a model of the same vendor |
| Release check | The README reads well to the people who wrote it, who know what it leaves out; a secret or private file reaches the public history | A side session with nothing but a fresh clone followed the README word for word and timed it. gitleaks scanned a mirror of every branch and pull request, and a sweep read every path, file, commit message and pull-request text for private files and words; each scanner was first shown catching a planted case | Every step passed in under 4 minutes on a warm machine; 6 unclear places in the README, none blocking, all fixed; both scans clean | One run, on one machine, with the images already downloaded |
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

Red commits carry a skeleton with no behaviour, so a test fails on the missing behaviour rather than an import
error. The first red commit's message overstated this (F35): of its 69 failing cases, 51 failed on the skeleton's
`NotImplementedError` and 18 on an assertion. Both are the missing behaviour, but the message said every one failed
on its own assertion. History is never rewritten, so this page states it precisely.

In three pairs (`4ec0c00` and `655af94`, `9b30136` and `a47f338`, `41b07e9` and `81a47a0`) both commits were made
seconds apart in one group the human approved, so the history shows their order but not a separate run; the
failing run was watched before the commit.

Where the behaviour already existed (the race tests, the backstops, the demo scenario), the tests went in green and
red runs showed they bite.

## Red runs

| Guard removed (temporary edit) | Result |
| --- | --- |
| `FOR NO KEY UPDATE` dropped from the row lock | 6 of the 11 race cases failed. Two reviewers: a 500, from the decision index. **Three silent lost updates, each answered 200**: an old revision approved over an amendment, an approval overwritten by an amendment, and a change after a decline landing as an amendment instead of a resubmission |
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
  reported. A shell script also emptied a private working document while writing it; it was restored from the backup
  made minutes earlier. The rule now: test edits go through the file-editing tools only, and new tests are formatted
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

## Identifiers you will meet in the history

Commit messages and code comments name phases, decisions and audit findings.

| Phase | What it built | Pull request, merge commit |
| --- | --- | --- |
| P1 | The skeleton and every guardrail | #1, `1a53c0e` |
| P2 | The pure domain | #2, `6ee5dc1` |
| P3 | Create with replay, get with history, the queue, the approved discount, the identity stand-in, problem details, the seed | #3, `4bcd63a` |
| P4 | Revise and decide under a row lock | #4, `22e9ce1` |
| P5 | The race, time-limit and backstop tests | #5, `e8fa2ab` |
| P7 | The page, and `permissions` in every response | #6, `108d583` |
| P8a | The audit's fixes, mutation testing, the final review | #7, `34ca7b3` |
| P6 | These documents, the demo scenario and the cited-commit check | #8, `7164e98` |
| P8b | The release check: a timed run from a fresh clone, a secret and privacy scan of the whole history, two demo rehearsals | the pull request that adds this line |

P6 was paused after its decisions, so the documents could describe the finished system.

| Design decision | Where it lives now |
| --- | --- |
| D1 stakeholder conflict, D2 revision rules | [ADR 0001](adr/0001-revisioned-requests.md) |
| D3 idempotent create, D5 one request per application | [ADR 0002](adr/0002-idempotent-create.md) |
| D4 concurrency control | [ADR 0003](adr/0003-concurrency-control.md) |
| D6 identity stand-in, D13 architecture style | [ADR 0004](adr/0004-stack-and-stand-ins.md), and [architecture](architecture.md), "Layers" |
| D7 error format | [architecture](architecture.md), "Errors" |
| D8 data model | [architecture](architecture.md), "Data model" |
| D9 test strategy | [architecture](architecture.md), "Testing" |
| D10 agentic SDLC | This page |
| D11 the page | [architecture](architecture.md), "The page" |
| D12 the documents | The README, "Where to read more" |
| D14 business rules left out | [Production readiness](production-readiness.md), and the README's scope choices |

F1 to F43 are the audit's findings, below. FR-n and Nn are the requirements in [`requirements.md`](requirements.md).

## Appendix: the audit's findings

The audit ran on the repository at `108d583`, judged against the client's needs alone. Every gate passed and every
need was met; it raised 43 findings, 8 medium (F1, F3, F4, F6, F7, F13, F14, F19) and none high. Each was checked
against the files, at the exact line, before it was accepted.

| # | Finding | Outcome |
| --- | --- | --- |
| F1 | A fresh database had four free applications; a walk-through ran out | Fixed: 20 applications, 16 free (`41b07e9`, `81a47a0`) |
| F2 | Every `request-exists` said "revise it instead", wrong for approved requests and other managers | Fixed: the detail states only the fact (same commits) |
| F3 | One request per application, for its whole life | Kept, defended in [ADR 0002](adr/0002-idempotent-create.md); production readiness item 4 |
| F4 | A replay answers 201 with the request as it is now | Kept, explained in ADR 0002 |
| F5 | `requirements.md` claimed a CI step checks latency (N10) | Fixed: N10 is stated as not measured (`6552f88`) |
| F6 | About 113 comments cited a design document that does not ship | Fixed: each points to a shipped document or gives its reason (this pull request) |
| F7 | `AGENTS.md` and the reviewer put the lock before the role | Fixed (`6552f88`) |
| F8 | `/` was a JSON 404; Swagger needs internet | Fixed: `/` redirects to the page; the README says Swagger needs internet |
| F9 | Swagger showed none of the real errors or headers | Fixed: every route declares its problems and headers (`41b07e9`, `81a47a0`) |
| F10 | No browser test of the page | Rejected: a manual checklist, run in full; a browser test is on the later list |
| F11 | The checklist's "last run" note was inaccurate | Fixed: all 20 steps rerun at `771b692` (`66be5de`) |
| F12 | No lookup by application id; unknown query parameters ignored | Production readiness items 3 and 10 |
| F13 | A change written before a decline lands as a resubmission | Kept, defended in [ADR 0001](adr/0001-revisioned-requests.md) |
| F14 | Declined terms can come back unchanged to another reviewer | Kept, defended in ADR 0001; refusing them is on the later list |
| F15 | `latest_decision` shows an earlier decline beside PENDING_REVIEW | Explained in [architecture](architecture.md), "Reading a response" |
| F16 | Limits the client never set are enforced as constants | Stated in the README's assumptions |
| F17 | No database backstop for "approved never changes" | Production readiness item 5 |
| F18 | CI's start check fetched only `/health` | Fixed: a signed-in call and the page too (`6552f88`) |
| F19 | Three one-line edits could hide a failing test from every gate | Fixed red first (`4ec0c00`, `655af94`); an early `return` stays a stated risk |
| F20 | A retry test sent the wrong terms | Fixed (`c7771ca`) |
| F21 | The applications' label is unused | Rejected: harmless; loan origination replaces the table |
| F22 | NONE covers pending, declined and no request alike | Explained in architecture, "What the mortgage process reads" |
| F23 | The history test never showed two decisions | Fixed (`c7771ca`) |
| F24 | Who and when appear under three pairs of names | Explained in architecture, "Reading a response" |
| F25 | A replayed create wrote no log line | Fixed: one INFO line (`41b07e9`, `81a47a0`) |
| F26 | A retried revise or decide gets 409 | Kept, explained in ADR 0002: `current` shows whether the change landed |
| F27 | The migrations are found next to the source (editable install only) | Production readiness item 8 |
| F28 | Prerequisites, a minimum uv version, no Windows-on-ARM build | Stated in the README |
| F29 | The integration tests need Docker | Stated in the README |
| F30 | The container's entry point listens on every interface | Rejected: it runs in a container; [architecture](architecture.md) says what to run outside one |
| F31 | A resubmitted request keeps its place in the queue | Explained in architecture, "Listing" |
| F32 | The pool time limit was never exceeded in a test | Fixed: a pool-exhaustion test (`c7771ca`) |
| F33 | More requirement markers | Rejected: a test claims only the requirement it proves on its own |
| F34 | `AGENTS.md` overstated the finish check | Fixed (`6552f88`) |
| F35 | A red commit message overstated its evidence | Stated precisely above, under "Red first, in the history" |
| F36 | Nothing asked before changing `uv.lock` | Fixed: approval rules added (`6552f88`) |
| F37 | The page sent the application id untrimmed; the error showed a regular expression | Fixed (`81a47a0`, `253f91d`) |
| F38 | Quick switches could draw stale rows or another user's buttons | Fixed (`253f91d`) |
| F39 | The page has no requirement id | Rejected: no listed requirement covers it; the checklist is its proof |
| F40 | Every start migrates and seeds | Production readiness item 8 |
| F41 | The database container had no restart policy | Fixed (`6552f88`) |
| F42 | The seed test never reseeded after a change | Fixed (`41b07e9`) |
| F43 | The size of the guardrails | Answered on this page: each control is tied to the failures it prevents |
