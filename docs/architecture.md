# Architecture

How the service is built, what each response means, and the rules nobody may break when changing it. The decisions
behind it are in [`docs/adr/`](adr/); what changes before production is in
[production readiness](production-readiness.md).

## Components

```mermaid
flowchart TB
  RM["Relationship manager"] --> UI
  RV["Reviewer"] --> UI
  MP["Mortgage application process<br/>svc.mortgage, API only"]
  subgraph SVC["pricing-exceptions · one Python process"]
    UI["Static page · /ui/"]
    API["api<br/>routes · schemas · identity · problems"]
    SRV["service<br/>one transaction per use case"]
    DOM["domain<br/>pure rules and state machine"]
    DBL["db<br/>SQL through text()"]
  end
  UI -->|"fetch, X-User-Id"| API
  MP -->|"GET approved-discount"| API
  API --> SRV
  SRV --> DOM
  SRV --> DBL
  DBL --> PG[("PostgreSQL 18<br/>requests · events<br/>users · applications")]
  IDP["Identity provider<br/>production only"] -. "replaces the users table" .-> API
  LOS["Loan origination<br/>production only"] -. "replaces the applications table" .-> DBL
```

People and the mortgage process use the same API. The domain depends on nothing; only `db` talks to PostgreSQL.
The dashed systems exist only in production ([ADR 0004](adr/0004-stack-and-stand-ins.md)).

## Layers

```text
src/pricing_exceptions/
  domain/    model.py  rules.py  errors.py        frozen dataclasses, enums, pure functions: every business rule
  service/   commands.py  queries.py  errors.py   one transaction per use case (with engine.begin())
  db/        engine.py  requests.py  reference.py  seed.py  schema.py
  api/       app.py  routes.py  schemas.py  problems.py  identity.py  cursor.py  ui.py  ui/
  main.py    startup.py  settings.py              wiring: engine, clock, app; the container's entry point
migrations/                                       Alembic, one hand-written migration
tests/       domain/  integration/  agentic/      domain and agentic need no Docker
```

import-linter enforces three contracts: the domain imports no web or database library (and `scripts/check.py`
holds it to the standard library); the layers run `api`, `service`, `db`, `domain`; and `api` never imports `db`,
SQLAlchemy or psycopg directly, so it cannot run SQL itself. Even the identity lookup goes through the service.

Service and domain functions share names: `commands.decide()` opens the transaction, locks the row and calls the
pure `rules.decide()`. The domain's `check_*` functions hold each action's access and status checks, and
`rules.permissions()` runs the same functions to say what a caller may do, so the two cannot disagree.

## State machine

Three states and five transitions; APPROVED is the only final state, and every transition writes exactly one
history event. The diagram and the reasons are in [ADR 0001](adr/0001-revisioned-requests.md).

| From | Action | Who | To | Event |
| --- | --- | --- | --- | --- |
| (none) | create | a relationship manager | PENDING_REVIEW, revision 1 | SUBMITTED |
| PENDING_REVIEW | revise | the requester | PENDING_REVIEW, revision + 1 | AMENDED |
| PENDING_REVIEW | decide | a reviewer who is not the requester | APPROVED or DECLINED (same revision) | APPROVED or DECLINED |
| DECLINED | revise | the requester | PENDING_REVIEW, revision + 1, decision cleared | RESUBMITTED |
| APPROVED | anything | anyone | refused: 409 `invalid-state` | none |

## Data model

| Table | Holds |
| --- | --- |
| `users` | id, display name, roles (RELATIONSHIP_MANAGER, REVIEWER, MORTGAGE_PROCESS); seeded |
| `mortgage_applications` | id and a fictional label; seeded (MA-1001 to MA-1020) |
| `pricing_exception_requests` | One row per request: application, requester, status, current revision and terms, the decision on the current revision (who, when, comment), `created_at` |
| `pricing_exception_events` | The history: one row per change, append-only, with the actor, the UTC time, the revision and the terms it applied to, and the comment of a decision |

The database backs up most rules, so a bug or a manual SQL statement cannot break them. Two hold by code only: an
approved request never changes, and history is only ever added to; no code path updates either, and
[production readiness](production-readiness.md), item 5, adds the database's own guard.

| Constraint | Rule it backs |
| --- | --- |
| `uq_request_application`, a plain unique constraint on `application_id` | One request per application; the create's conflict target ([ADR 0002](adr/0002-idempotent-create.md)) |
| `ux_event_one_decision_per_revision`, partial unique index on decisions | A revision gets at most one decision |
| `ux_event_one_revision_event`, partial unique index on SUBMITTED, AMENDED, RESUBMITTED | A revision number is created once: no lost update |
| `CHECK (decided_by <> requested_by)` | Four-eyes |
| `ck_request_decision_matches_status` | Pending has no decision; approved or declined records who and when; a decline has its comment. Without it, a NULL `decided_by` would slip past four-eyes |
| `CHECK (reason ~ '[^[:space:]]')` on both tables | A reason is never blank |
| `CHECK (discount_bps > 0)`, `CHECK (revision_no >= 1)`, value lists for status and event type | Basic shape |
| Foreign keys to users and applications | Ids are real |

The first six backstops each have a test that bypasses the domain with raw SQL and expects that exact constraint to
refuse it; the shape checks are tested on both tables, the foreign keys are not tested on their own.
Conventions: every column is `NOT NULL` except the three decision columns and the event comment; enumerations are
`text` with a check, mirrored by a Python enum; money is integer basis points everywhere; times are `timestamptz`
written from the injected clock, never `now()`; `created_at` is set once. Two indexes serve the reads:
`(status, created_at, id)` for the queue and `(request_id, id)` for a request's history.

The policy limits live once, in `domain/rules.py`, not in the database: 1 to 100 bps, and text (reason, comment)
that is not blank, holds no NUL character, is valid Unicode, and is at most 1,000 characters. Text is stored
exactly as sent.

## API

JSON in `snake_case`. "Staff" means a relationship manager or a reviewer.

### Endpoints

| Method and path | Who | What | Success |
| --- | --- | --- | --- |
| `POST /api/v1/pricing-exception-requests` | A relationship manager | Create; safe to retry | 201 with `Location`; a replay adds `Idempotent-Replayed: true` |
| `GET /api/v1/pricing-exception-requests?status=&limit=&cursor=` | Staff | One status (default `PENDING_REVIEW`), oldest first, a page at a time | 200: `items`, `next_cursor`, `permissions.create` |
| `GET /api/v1/pricing-exception-requests/{id}` | Staff | The request, its latest decision, its full history and the caller's `permissions` | 200 |
| `POST /api/v1/pricing-exception-requests/{id}/revisions` | The requester | Change the terms: `{revision, discount_bps, reason}`, all required | 200, the request |
| `POST /api/v1/pricing-exception-requests/{id}/decisions` | A reviewer who is not the requester | `{revision, outcome, comment}`; a decline needs its comment | 200, the request |
| `GET /api/v1/mortgage-applications/{application_id}/approved-discount` | Anyone signed in | What the mortgage process reads | 200 |
| `GET /demo/users` | Anyone; only with the demo identity on; not in the API description | The staff users, for the page's switcher | 200 |
| `GET /health` | Anyone | The process and its database | 200 or 503 |
| `GET /` | Anyone | Redirects to the page | 307 to `/ui/` |

Every body is checked strictly: unknown fields are refused, and `discount_bps` and `revision` must be whole JSON
numbers (`"25"` and `25.0` are 422). `revision` is the revision the caller saw, at least 1.

### Order of checks

The same input always gets the same answer, even during a race, because the checks run in a fixed order and
everything that depends only on the body runs before any lock.

| Step | Revise and decide | Create | Reads |
| --- | --- | --- | --- |
| 1 | The body is JSON (400) | The body is JSON (400) | Identity (401) |
| 2 | Identity (401) | Identity (401) | Parameters (422) |
| 3 | Shape, then values (422) | Shape, then values (422) | The staff role (403) |
| 4 | The role (403), before any database access | The relationship-manager role (403) | The lookup (404) |
| 5 | Lock and load the request (404) | The application exists (422 `application-not-found`) | |
| 6 | Requester or four-eyes (403) | Insert; replay (201) or 409 `request-exists` | |
| 7 | Status (409 `invalid-state`) | | |
| 8 | Revision (409 `stale-revision`) | | |

The role depends only on who asks, so checking it first takes no lock and lets nobody without the role probe which
ids exist: svc.mortgage gets 403 even for an id that does not exist. A 422 always means the input is wrong on its
own, whatever the request's state. The approved-discount read asks no role, because every role may read it.

### Errors

Every error is an RFC 9457 problem (`application/problem+json`) with `type`, `title`, `status` and `detail`.
Clients branch on `type`; `detail` is for people. Problem types live under
`https://pricing-exceptions.example/problems/` (a reserved domain, so the address can never belong to anyone).

| Status | `type` ends with | When |
| --- | --- | --- |
| 400 | `malformed-request` | The body is not JSON, or not readable at all |
| 401 | `unauthenticated` | `X-User-Id` missing or unknown, or the demo identity off; with `WWW-Authenticate` |
| 403 | `forbidden` | The wrong role, or not the requester |
| 403 | `four-eyes-violation` | A reviewer deciding their own request |
| 404 | `about:blank` | Unknown request id, or unknown application in the path |
| 409 | `request-exists` | The application already has a request ([ADR 0002](adr/0002-idempotent-create.md)) |
| 409 | `invalid-state` | Not allowed in the current status, for example approved is final |
| 409 | `stale-revision` | The body's revision is not the current one; also carries `sent_revision` |
| 422 | `validation-failed` | Field errors: `errors: [{pointer or parameter, detail}]`, never the input itself |
| 422 | `application-not-found` | The body names an application that does not exist |
| 503 | `temporarily-unavailable` | A time limit passed or the database is down; `Retry-After: 1` |
| 500 | `about:blank` | A bug; no stack trace in the body |

**Every 409 carries `current`**: the request as it is now, without its history. A client whose answer was lost
can see whether its own change landed, and a losing reviewer sees the winning decision. Clients never retry a 409
blindly; they read `current`, then decide. They may retry a create freely, and a 503 with backoff.

### Reading a response

- **`permissions`.** Every response that shows a request carries what the caller may do with it,
  `{"decide": …, "revise": …}`, each `{allowed, problem, detail}`, computed by the same domain checks the actions
  run. The list carries `permissions.create`. A 409's `current` carries none; reload to get them. The page offers
  only what is allowed and shows the reason for the rest.
- **`latest_decision`** is the newest decision in the history, with the revision it judged. After a decline and a
  resubmission, the request is `PENDING_REVIEW` while `latest_decision` still shows the decline of the earlier
  revision: that is the decline the resubmission answers (audit F15). Compare its `revision` with the request's.
- **Who and when** appear under three names (F24): `actor` and `occurred_at` on each history event,
  `decided_by` and `decided_at` in `latest_decision`, and `approved_by` and `approved_at` in what the mortgage
  process reads. They are the same person and time for the approval.
- **A replayed create** answers with the request as it is now, which may show later revisions or a decision
  ([ADR 0002](adr/0002-idempotent-create.md)); `Idempotent-Replayed: true` marks it.
- Times are UTC, written by the server; clients never send times. Discounts are integer basis points (25 means
  0.25 percentage points off the standard rate).

### Listing

`GET /api/v1/pricing-exception-requests?status=PENDING_REVIEW&limit=50` answers one page, oldest first by
`(created_at, id)`, and an opaque `next_cursor`; pass it back unchanged until it is `null`. `limit` is 1 to 200
(default 50). Each page is one range scan of the `(status, created_at, id)` index, so page 1 and page 50,000 cost
the same, and no request appears twice in a walk.

A request that becomes visible behind the cursor during a walk appears on the next walk, not this one. In
particular a **resubmitted request keeps its original creation time**, so it re-enters the queue at its old
position, ahead of newer requests (F31): a reviewer who starts again from the first page sees it first. Items
carry the list fields only; the history is in `GET /{id}`.

### What the mortgage process reads

```json
{"application_id": "MA-1004", "status": "APPROVED", "discount_bps": 15,
 "request_id": "00000000-0000-4000-8000-000000001004", "revision": 1,
 "approved_by": "rv.dana", "approved_at": "2026-10-15T10:02:44Z"}
```

Without an approval: `{"application_id": "MA-1002", "status": "NONE"}`. **NONE covers no request, pending,
declined and resubmitted alike** (F22): only an approval may change what a borrower pays, and the mortgage process
needs nothing else. `discount_bps` is absent rather than null, so it can never be read as zero. Only an unknown
application gives 404, so a typo never reads as "no discount". The service returns the discount, not a final rate,
because the standard rate belongs to loan origination. The process pulls when it prices an offer; an approval is
final, so what it read never changes.

## Time limits

Every wait has a limit, and in the normal case the server gives up before a client's 10-second timeout, so a timeout
is rarely ambiguous:

| Limit | Value | When it passes |
| --- | --- | --- |
| `pool_timeout`: waiting for a pooled connection | 3 s | 503 |
| `connect_timeout`: opening a new connection | 3 s | 503 |
| `lock_timeout`: waiting for a row lock or a unique check | 2 s | 503, SQLSTATE 55P03 |
| `statement_timeout`: any one statement | 3 s | 503, SQLSTATE 57014 |
| `transaction_timeout`: the whole transaction (PostgreSQL 17 and later) | 5 s | 503; the session ends and the pool replaces it |

A call waits at most 3 seconds for its connection, then its transaction is capped at 5: about 8 seconds in all.
With the demo identity, the user lookup first takes a pooled connection of its own, so when the pool is full the
worst case is about 11 seconds, past a 10-second client; a production token check reads no database, which
removes that wait. Every limit is set once, in `db/engine.py`, together
with READ COMMITTED and UTC, and a test asserts them on a pooled connection. Timeout tests prove the 503 for the
lock, the pool and the transaction limits.

## Logging

- One INFO line per state change: request id, actor, event type, revision. One INFO line per replayed create.
- One WARNING line per refused write, written by the error handler: the actor (`none` before the identity check
  passed), the action, the request id, the problem and the status. Rejected attempts go to the log, not the
  history, because the history records business facts.
- One WARNING line per 503 and one ERROR line per unexpected database error, with the error's class and SQLSTATE
  only. Any other 500 is logged by uvicorn with its traceback; the client sees no trace.
- A WARNING at start-up when the demo identity is on.
- **Never** the reason, the comment or a request body: they are free text and may hold personal data. The engine
  hides bound parameters from error messages, and tests check that no log line holds the text.

## Security

| Concern | Here | Production |
| --- | --- | --- |
| Authentication | `X-User-Id` against seeded users, only with `DEMO_IDENTITY=true`; Compose publishes the port on `127.0.0.1` only | OIDC; client credentials for the mortgage process |
| Authorisation | Roles, requester-only changes and four-eyes, all in the domain, checked on every call | The same, plus an authority matrix and assignment |
| Least privilege | svc.mortgage may only read approved discounts | A narrow scope |
| Mass assignment | Unknown fields refused: a client cannot send `status` or `decided_by` | Same |
| SQL injection | Bound parameters only; ruff flags string-built SQL | Same |
| Cross-site scripting | The page inserts server text with `textContent` only, and tests refuse `innerHTML` and other markup sinks; `/ui/` sends `Content-Security-Policy: default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` and `nosniff` | Same |
| Cross-site request forgery | Not applicable: a custom header, no cookies, no cross-origin access | Needed with cookie sessions |
| Personal data | The reason and comment are never logged | Classification, retention, a hint against borrower data |
| Secrets | None needed; none in the repository | A secrets manager |

The page's policy is sent on `/ui/` only: Swagger at `/docs` loads its files from a CDN and starts with an inline
script, which the policy would block.

## Testing

Tests run on a real PostgreSQL 18 in a throwaway container (testcontainers), never on SQLite or mocks, because the
guarantees are database behaviour. Before every integration test the requests and their history are emptied and
the seed runs again, so each test starts from the seeded data: 6 users, 20 applications and 4 requests.

| Level | What it proves | Functions (cases) |
| --- | --- | --- |
| Domain | Every status, action and user against its expected outcome and exact error; bounds; four-eyes; revision binding; who and when from a fixed clock; `permissions` agree with the actions | 35 (193) |
| API | Status codes, headers, problem bodies, identity on and off, replay, history, pagination, the consumer's view, log lines, atomicity | 63 (206) |
| Demo scenario | The 12 steps of [`demo.http`](demo.http) on the seeded data, and that the file lists the same steps | 2 (2) |
| Database backstops | Each constraint refuses a bad row written with raw SQL | 9 (26) |
| Races and time limits | Two writers at once, deterministically; 503 at each time limit | 11 (12) |
| Setup | Health, the seed run twice, the engine settings in effect | 4 (4) |
| Page | `/demo/users`, the page's headers, no inline code, no markup sinks | 8 (14) |
| Guardrails | The agent hooks, the requirement gate, the scans, the cited commits | 33 (70) |

Every requirement that [`requirements.md`](requirements.md) says the tests prove has at least one test that
carries its id (`@pytest.mark.req(id="FR-3")`), and a gate fails if one has none; the file names the three checked
another way, or not at all. Race tests hold a lock in the test's own
connection, start the real API call in a thread, and wait until PostgreSQL reports the call waiting on that lock
before letting go: no luck, and no fixed sleeps. The one `time.sleep` in the code base is that barrier's 10 ms
pause between polls, a wait for a condition with a 5-second deadline; the lint rule that bans `time.sleep` is
lifted for its file alone (`tests/integration/races.py`), and a guardrail test checks that the file holds no other
banned call. A mutation report on the domain is described in
[agentic SDLC](agentic-sdlc.md), "Mutation testing". The page is checked by hand with
[`ui-checklist.md`](ui-checklist.md).

## The page

One static page at `/ui/` (`api/ui/index.html`, `app.js`, `app.css`), served by the service itself, for staff on
laptop and desktop screens: the queue, the open request with its history, and its actions. It calls only the
public API, plus `/demo/users` for the "Acting as" switcher, and holds no business rule: what a user may do comes
from `permissions`, and the server checks every action again. Each tab keeps its own user in `sessionStorage`, so
two tabs can be two people. After a 409 it shows the server's message and `current`, with a Reload button; it
never reloads by itself, because a reviewer must see new terms before approving them. Each request shows what the
mortgage process reads for its application. The page's files are sent with `Cache-Control: no-cache`, so a rebuilt
service is never driven by an old script.

## Operating and changing it safely

**Configuration.** Two settings, read from the environment: `DATABASE_URL`
(`postgresql+psycopg://user:password@host/db`) and `DEMO_IDENTITY` (`true` switches the `X-User-Id` stand-in on;
anything else leaves every call 401).

**Start-up.** The container's entry point (`python -m pricing_exceptions.startup`) applies the migrations, runs
the seed, then serves on port 8000. The seed only inserts what is missing, so a restart or a second
`docker compose up` changes nothing, even after a demo changed the seeded requests. To run against a PostgreSQL 17
or later of your own, with `DATABASE_URL` and `DEMO_IDENTITY` set: `uv run alembic upgrade head`, then
`uv run python -m pricing_exceptions.db.seed`, then `uv run uvicorn --factory pricing_exceptions.main:build_app`,
which listens on `127.0.0.1` by default. Do not use `startup.py` outside a container: it listens on every interface,
which a container needs and a laptop with the demo identity on must not do.

**Reset.** `docker compose down -v` deletes the database volume; the next `up` starts from the seed.

**Migrations.** Hand-written Alembic files with plain DDL, in `migrations/versions/`. Change a live schema in steps
(add, backfill, switch, then drop), and add a backstop test for every new constraint.

**Rules nobody may break.** Each one carries a guarantee, and most have a test that fails when it is broken:

1. `uq_request_application` stays a plain, non-partial unique constraint, and it is the create's conflict target
   (`ON CONFLICT ON CONSTRAINT uq_request_application`). A test checks both.
2. READ COMMITTED stays pinned on the engine. The replay and the lock re-check rely on each statement seeing
   newly committed rows.
3. A request is locked by primary key only, with `FOR NO KEY UPDATE`, before any rule runs on it.
4. No network call while a row lock is held; production lookups happen before the transaction.
5. Every response that shows a request with its history (GET, create and its replay, revise, decide), and every
   409's `current`, is built by `db.requests.read_request()`, so a write's answer and a later GET cannot disagree.
6. The decision columns match the status: a resubmission clears them, and the history keeps every decision.
7. Times come from the injected clock, in UTC, read under the lock for a change; money is integer basis points.
8. PostgreSQL 17 or later, for `transaction_timeout`; on 16 every connection fails at start-up.
9. The seed stays idempotent.
10. History is only ever added to: the code has no update or delete for events.

**Adding a transition.** Write the domain rule first, with a row in the transition tests; give it a `check_*`
function so `permissions` follows it; take the lock in the service like `revise()` and `decide()`; add a race test
for it, and a backstop if it needs one.
