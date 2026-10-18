# Mortgage pricing exceptions

A small internal service for **pricing exception requests**. A relationship manager asks for a lower rate on one
mortgage application: a discount, in basis points, off the standard rate. A reviewer who did not make the request
approves or declines it. The mortgage application process reads the approved discount, and staff can see each
request's full history. When a calling system times out and sends the same create again, no second request
appears.

It is an HTTP API (FastAPI, PostgreSQL 18) with a small web page for staff, seed data for a realistic walk-through,
and tests that prove the guarantees on a real database.

## The stakeholder decision

One stakeholder wants managers to keep amending the discount; the other wants every approval tied to precisely
the terms reviewed. Both get what they need: **every change is a new numbered revision, and a reviewer
approves or declines one specific revision.** If the terms changed since the reviewer read them, the decision is
refused with 409 and the current terms, so nobody approves something they did not see. An approved request never
changes. A declined one can be resubmitted with new terms. The reasons and the trade-offs are in
[ADR 0001](docs/adr/0001-revisioned-requests.md).

## Run it

Prerequisites: **Docker** with Compose v2 (Docker Desktop, or Docker Engine on Linux). Nothing else is needed to
run the service. To run the tests and gates you also need **[uv](https://docs.astral.sh/uv/) 0.13.1 or later**;
it installs Python 3.15 for the project by itself.

```bash
docker compose up --build
```

The command keeps running and shows the service's log; Ctrl+C stops it. Use a second terminal for the commands
below, or add `-d` to run it in the background. Then open <http://localhost:8000/> in a browser: it redirects to the
page at `/ui/`. The first build downloads the base images (about 210 MB) and the Python packages; after that, a
build and start take about 20 seconds. The container applies the database migration and the seed data on every
start; both are idempotent.

**Reset** to fresh seed data (this deletes the database):

```bash
docker compose down -v
```

## Explore it

- **The page**, <http://localhost:8000/ui/>: choose who you are "acting as", then create, review and inspect
  requests. Each tab remembers its own user, so two tabs can be two people (for example, a manager and a reviewer).
  The page offers only what the server says you may do (`permissions` in each response) and shows the reason for
  the rest. [`docs/ui-checklist.md`](docs/ui-checklist.md) is a 20-step walk-through.
- **Swagger**, <http://localhost:8000/docs>: every endpoint, its errors and its headers. Swagger loads its files from
  a CDN, so it needs internet access; the page does not.
- **[`docs/demo.http`](docs/demo.http)**: the 12-step demo as plain HTTP calls, one click each in VS Code's REST
  Client extension. A test replays the same steps, so the demo cannot silently break. Without VS Code, send the same
  calls from Swagger, or with curl from Git Bash or any POSIX shell; step 2, for example:

  ```bash
  curl -i http://localhost:8000/api/v1/pricing-exception-requests -H "X-User-Id: lead.fatima" \
    -H "Content-Type: application/json" \
    -d '{"application_id": "MA-1006", "discount_bps": 25, "reason": "Retention: competitor offer 25 bps below standard"}'
  ```

Every call names its user in the `X-User-Id` header, a stand-in for real sign-in that works only because Compose
sets `DEMO_IDENTITY=true` (without it, every call is 401). The seeded users are fictional:

| User | Roles |
| --- | --- |
| `rm.anna`, `rm.bram` | Relationship manager |
| `rv.dana`, `rv.erik` | Reviewer |
| `lead.fatima` | Both, to show that holding the reviewer role is not enough to approve your own request |
| `svc.mortgage` | Mortgage process: the service account that reads approved discounts, through the API only |

Applications **MA-1001 to MA-1020** exist. MA-1002 to MA-1005 already hold a request, one in each situation
(pending, changed and pending, approved, declined), so **16 are free for new requests**: MA-1001 and MA-1006 to
MA-1020. Each application takes one request for good; reset when they run out. The list of requests is the review
queue: it shows pending requests unless you ask for `?status=APPROVED` or `?status=DECLINED`, and the page has a tab
for each.

## Test it

```bash
uv sync
uv run python scripts/check.py --all
```

This runs every gate, as CI does: ruff, mypy, the layer contracts, the requirement-id gate, the scans that stop a
guardrail being dodged, and all the tests. It takes about 2 minutes and prints one PASS line per gate, eight in all,
then `every gate passed`. The integration tests start their own PostgreSQL in Docker, so Docker
must be running; `--fast` runs the gates that need no Docker. [`docs/requirements.md`](docs/requirements.md) lists
each requirement; every test that proves one carries its id.

A mutation report on the domain (229 small deliberate bugs, of which 228 are caught; the one survivor changes no
behaviour) reruns in under a minute from Git Bash or any POSIX shell, in a throwaway Linux container. It prints
the counts as JSON (`"killed": 228`, `"survived": 1`, `"total": 229`), then the name of each survivor, and writes
nothing into the repository:

```bash
git ls-files -z | tar --null -T - -cf - | docker run --rm -i -e UV_LINK_MODE=copy -e UV_PYTHON_DOWNLOADS=never \
  python:3.15-slim-trixie sh -c 'pip install -q --root-user-action=ignore uv==0.13.1 && mkdir /work \
  && tar -x -C /work && cd /work && uv sync --locked -q && uv run mutmut run > /dev/null 2>&1; \
  uv run mutmut export-cicd-stats > /dev/null; cat mutants/mutmut-cicd-stats.json; echo; uv run mutmut results'
```

## Assumptions

The requirements leave these open; each is our reading, with its cost.

| Open point | Our assumption | Cost |
| --- | --- | --- |
| Discount limits | 1 to 100 bps, one constant in `domain/rules.py`, a placeholder for the client's pricing policy | The real limit is unknown |
| Reason and comment | The reason is required; a decline needs a comment; an approval's comment is optional. Text is not blank, at most 1,000 characters, and stored exactly as sent | 1,000 is a placeholder too |
| Changing a request | Only the manager who made it, while pending or after a decline; each change is a revision | Clients send the revision they saw |
| Who may decide | The reviewer role, and never the requester (four-eyes); one person may hold both roles | No limits by amount or product |
| Who may create | Any relationship manager, for any existing application | No assignment of managers to applications |
| Who may read | **All staff read all requests.** No visibility rule was given, and reviewers need the whole queue | Real visibility may be by portfolio |
| What is a duplicate | Any second request for the same application, retried or not | One request per application for good ([ADR 0002](docs/adr/0002-idempotent-create.md)) |
| A retried create | Answers 201 with the request as it is now, marked `Idempotent-Replayed: true` | It may show later revisions |
| Mortgage applications | A seeded table; "existing" means listed; an unknown id is 422 | The real record lives in loan origination |
| What the mortgage process reads | The approved discount, or `NONE` for anything not approved (no request, pending, declined) | It pulls when it prices an offer |
| Identity | `X-User-Id` against seeded users, only with `DEMO_IDENTITY=true` | Anyone can act as anyone in the demo |
| Time and money | UTC times from the server; integer basis points | Percentages only in the page |

How to read `latest_decision`, the three names for who decided and when, and the queue order after a
resubmission: [architecture](docs/architecture.md), "Reading a response" and "Listing".

## Deliberate scope choices

**Not built**, each with its reason in [production readiness](docs/production-readiness.md): withdraw, expiry or
re-pricing after approval; approval limits and manager assignment; real sign-in; an `Idempotency-Key` header (not
needed while an application holds one request); push notifications to the mortgage process; search.

**Built beyond the minimum**, because correctness needed it: paging through the queue (a silent cap would hide
requests from reviewers), deterministic race tests, database constraints behind the domain's rules, time limits on
every wait (503 rather than a hung call), and the page's `permissions`, so the page never guesses a rule.

## Troubleshooting

- **Port 8000 is in use:** stop the other process, or change `127.0.0.1:8000:8000` in `compose.yaml`.
- **`/docs` is blank:** it needs internet access for Swagger's files. Use `/ui/` or `docs/demo.http` offline.
- **Every call is 401:** the `X-User-Id` header is missing, names an unknown user, or `DEMO_IDENTITY` is not `true`.
- **409 `request-exists` on a "free" application:** it was used earlier. Pick another of MA-1006 to MA-1020, or reset.
- **`uv sync` fails on Windows on ARM:** the database driver has no build there; run the service with Docker.
- **The integration tests fail at start-up:** Docker is not running.
- **`curl -I` gets 405:** the service answers GET, and `curl -I` sends HEAD. Use `curl -i`, or a browser.

## Where to read more

| Document | What it holds |
| --- | --- |
| [`docs/adr/`](docs/adr/) | Four decisions: revisioned requests, the idempotent create, the row lock, the stack and its stand-ins |
| [`docs/architecture.md`](docs/architecture.md) | Diagrams, the API in detail, time limits, logging, security, testing, and the rules nobody may break |
| [`docs/production-readiness.md`](docs/production-readiness.md) | What changes before production, ranked by risk |
| [`docs/agentic-sdlc.md`](docs/agentic-sdlc.md) | How AI agents built this under guardrails, how their output was checked, and what risks remain |
| [`AGENTS.md`](AGENTS.md) | The working rules for any agent or person changing the code |
