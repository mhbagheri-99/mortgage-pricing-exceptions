# ADR 0004: The stack, and stand-ins for the systems around it

Accepted, 17 October 2026.

## Context

The service must run on a laptop with one command and nothing to buy or sign up for, and its
guarantees (no duplicate request, an approval bound to its revision, exactly one winner in a race) must hold in
the database, where they can be tested. Users and mortgage applications belong to other systems in a real bank.

## Decision

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.15 | The latest stable release; every package used has builds for it. The design is language-neutral: SQL guarantees, a pure domain, HTTP semantics |
| Web | FastAPI with sync endpoints, served by uvicorn | Validation and the API description (Swagger at `/docs`) built in. Transactions are short and there is no fan-out, so async would add a driver and test complexity for nothing; sync endpoints run on a thread pool |
| Input | Pydantic v2, strict money and revision fields, unknown fields refused | Lax mode turns `"25"`, `25.0` and `true` into numbers, which is wrong for money; a client cannot send `status` |
| SQL | SQLAlchemy 2.1 Core with hand-written SQL through `text()`, psycopg 3 | The guarantees are a few specific statements; an ORM would hide when they run. Bound parameters only |
| Database | PostgreSQL 18 (17 or later is required, for `transaction_timeout`) | A unique check that waits for an uncommitted duplicate, a row lock whose waiter gets the newest row, a fresh snapshot per statement. SQLite has none of these, so tests on it would prove nothing |
| Migrations | Alembic, one hand-written migration with plain DDL | The constraints and checks are the backstops; they are written out, not generated |
| Errors | RFC 9457 problem details (`application/problem+json`) | A standard body with a `type` per problem, so clients branch on it ([architecture](../architecture.md), "Errors") |
| Shape | One service, internally layered: a pure domain, a service layer with one transaction per use case, a SQL layer, an HTTP layer; import-linter enforces it | The guarantees live in one database transaction. Splitting into services would turn it into a distributed one |
| Page | One static page at `/ui/`, plain HTML, CSS and JavaScript, calling the public API | No build step and no second toolchain; it holds no business rule, and the server checks everything |
| Tooling | uv with a lock file, ruff, mypy (strict on `src`), pytest with testcontainers | One lock file for every machine; types and lint as gates for agent-written code |

**Stand-ins**, each behind one seam that production replaces:

- **Identity.** A caller names a seeded user in the `X-User-Id` header. It works only when `DEMO_IDENTITY=true`;
  otherwise every call is 401. Compose switches it on and publishes the port on the loopback address only, because
  with it on anyone who reaches the port can act as anyone, and the service logs a warning at start-up. Production:
  OIDC sign-in, roles from token claims, and a client-credentials token for the mortgage process. The seam is
  `api/identity.py`.
- **Mortgage applications.** A seeded table of ids and fictional labels; "existing" means the id is in it.
  Production: the loan-origination system, called before the transaction, never under a lock. The seam is
  `db/reference.py`.
- **Users.** Six seeded users with roles (README, "Explore it").

## Consequences

- Python 3.15 and uv 0.13 are recent. The locked `psycopg-binary` has no build for Windows on ARM, so a local
  install there fails; Docker still works.
- Swagger at `/docs` loads its files from a public CDN, so it needs internet access; the page at `/ui/` does not.
- Reviewers used to Java read Python less fluently. The layers and names follow the same ideas as a Spring service:
  routes, services, repositories, a domain model.
- [Production readiness](../production-readiness.md) lists what each stand-in becomes.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Java with Spring Boot, or C# | Equally able; any language was allowed, and every line here can be explained fastest in Python |
| Async FastAPI | No concurrency gain for short transactions; one blocking call would freeze the event loop |
| An ORM (SQLAlchemy ORM, SQLModel) | Hides when the lock and the conflict insert run |
| Ports and adapters with in-memory fakes | Fakes cannot model row locks or unique waits, so the tests that matter would hit PostgreSQL anyway |
| A React or Streamlit front end | A second toolchain, or a framework to defend, for one page |
| An `Idempotency-Key` table | See [ADR 0002](0002-idempotent-create.md) |
