# Build history

The key to the names that commit messages and code comments use (phases, design decisions, audit findings),
and what became of each finding of the independent audit. How the agents were controlled, and the evidence,
is in [agentic SDLC](agentic-sdlc.md).

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
| D10 agentic SDLC | [Agentic SDLC](agentic-sdlc.md) |
| D11 the page | [architecture](architecture.md), "The page" |
| D12 the documents | The README, "Where to read more" |
| D14 business rules left out | [Production readiness](production-readiness.md), and the README's scope choices |

F1 to F43 are the audit's findings, below. FR-n and Nn are the requirements in [`requirements.md`](requirements.md).

## The audit's findings

The audit ran on the repository at `108d583`, judged against the client's needs alone. Every gate passed and every
need was met; it raised 43 findings, 8 medium (F1, F3, F4, F6, F7, F13, F14, F19) and none high. Each was checked
against the files, at the exact line, before it was accepted.

| # | Finding | Outcome |
| --- | --- | --- |
| F1 | A fresh database had four free applications; a walk-through ran out | Fixed: 20 applications, 16 free (`41b07e9`, `81a47a0`); the release check seeded ten more, all pending, and kept 16 free |
| F2 | Every `request-exists` said "revise it instead", wrong for approved requests and other managers | Fixed: the detail states only the fact (same commits) |
| F3 | One request per application, for its whole life | Kept, defended in [ADR 0002](adr/0002-idempotent-create.md); production readiness item 4 |
| F4 | A replay answers 201 with the request as it is now | Kept, explained in ADR 0002 |
| F5 | `requirements.md` claimed a CI step checks latency (N10) | Fixed: N10 is stated as not measured (`6552f88`) |
| F6 | About 113 comments cited a design document that does not ship | Fixed: each points to a shipped document or gives its reason (`569a302`) |
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
