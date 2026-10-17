# Working in this repository

Rules for any AI coding agent, and for people. The gates in `scripts/check.py` and the hooks in `.claude/`
enforce the important ones; this file explains them.

## What this is

A small service for mortgage pricing exception requests: a relationship manager asks for a discount on the
standard rate of one mortgage application, a reviewer approves or declines it, and the mortgage application
process reads the approved discount. Python 3.15, FastAPI with sync endpoints, SQLAlchemy Core with hand-written
SQL, PostgreSQL 18.

## Commands

- `uv sync` installs everything. `uv run python scripts/check.py --fast` runs the gates that need no Docker;
  `--all` runs every gate, as CI does.
- `docker compose up --build` starts the app at http://localhost:8000 (Swagger at `/docs`).

## Layers (import-linter enforces them)

- `domain`: every business rule, as pure functions over frozen dataclasses. Standard library only.
- `service`: one transaction per use case (`with engine.begin()`); it calls the domain and `db`.
- `db`: SQL through `sqlalchemy.text()` with bound parameters. No ORM.
- `api`: HTTP only. It never imports `db`; even the identity lookup goes through `service`.

## Rules that keep the guarantees

- One request per application: `uq_request_application` stays a plain, non-partial unique constraint.
- READ COMMITTED stays pinned on the engine. Lock a request by primary key only, with `FOR NO KEY UPDATE`.
- No network calls while a row lock is held.
- Every response that shows a request is built by `db.requests.read_request()`.
- Times come from the injected clock, as `datetime.now(UTC)`. Money is integer basis points.
- Requests are checked in a fixed order: JSON (400), identity (401), shape and values (422), then the lock (404),
  role and four-eyes (403), status (409 `invalid-state`) and revision (409 `stale-revision`).

## Tests

- Work test-first on every rule (`/red-first`): show the test failing for the right reason, then write the code.
- A test that proves a requirement carries `@pytest.mark.req(id="FR-3")`; the ids live in `docs/requirements.md`.
- Database behaviour is tested on real PostgreSQL through testcontainers, never on SQLite or mocks.
- Never skip, xfail or weaken a test to make it pass. Edit tests only with the file-editing tools, so the
  test-integrity guard sees the change. The guard counts asserts, tests and requirement markers; it cannot see
  an assert weakened in place (`==` becoming `>=`) or swapped for a weaker one, so reviews look for that.
- No fixed sleeps: wait for a condition, with a deadline.

## Guardrails

- Editing `.claude/`, `scripts/check.py`, `.github/`, `pyproject.toml` or `docs/requirements.md`, adding or
  removing a package, committing and pushing all need the human's approval. Force push, hard reset and force-add
  are refused.
- When you stop, the finish check runs the fast gates and keeps you working while one fails.
- Never change a guardrail to get past it. Say what blocked you and ask.
- These rules see the file-editing tools and the command text only. A shell command that writes a file (`sed -i`,
  a redirect, `Set-Content`) or an unusual git spelling can slip past them, so never use one on a guarded file.
  CI repeats every gate, and `scripts/check.py` also fails on a config file that would override
  `pyproject.toml`, on a domain import outside the standard library, and on a `noqa` that hides a banned API.

## Data and wording

- Seed data is fictional. Keep real company, client and personal names out of code, data, docs and commits.
- Commit messages follow Conventional Commits and name requirement ids, for example `feat(FR-1): create a request`.
