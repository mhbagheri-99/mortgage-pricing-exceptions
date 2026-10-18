# Before production

Where this exercise takes a lighter path than a bank would, and what replaces it. Ranked by risk: the first rows
are the ones that would hurt most if the service went live as it is.

| # | Shortcut here | Production replacement | Why it matters |
| --- | --- | --- | --- |
| 1 | Identity is a header (`X-User-Id`) naming a seeded user, switched on by `DEMO_IDENTITY` | OIDC sign-in with roles from token claims; a client-credentials token for the mortgage process; a `GET /me` for the page instead of `/demo/users`. A token check reads no database, which also removes the demo identity's extra wait for a pooled connection | Today anyone who reaches the port can act as any reviewer; that is why Compose publishes it on the loopback address only |
| 2 | "Authorised" means the reviewer role and not the requester; every relationship manager may create for any application; all staff read all requests | An authority matrix owned by credit risk (limits by amount or product), assignment of managers to applications, and read access by portfolio if the bank requires it | Auditors ask who could approve what, and when |
| 3 | Mortgage applications are a seeded table: an id and a label, existence only | The loan-origination system: existence, status, assignment, called before the transaction with a timeout and a circuit breaker, never under a lock. A lookup of the request by application id for staff (audit F12) | A local copy drifts; approving a discount on a closed application is a defect |
| 4 | One request per application for its whole life; no withdraw; approval is permanent (F3) | Withdraw, expiry and supersede, with the unique constraint made partial and an `Idempotency-Key` added from that day ([ADR 0002](adr/0002-idempotent-create.md)) | A mistaken request occupies its application, and rates move after an approval |
| 5 | History is append-only and an approved request never changes, by code only: no path updates them (F17) | The app's database role gets only INSERT and SELECT on events, and a trigger or column privileges refuse any change to an approved request; migrations run as a separate owner role | A table owner can always grant itself UPDATE; a raw update of an approved row would change what the mortgage process reads while the history shows the reviewed terms |
| 6 | The reason and comment are free text, stored as typed (never logged) | Classified, with an agreed retention period and a hint in the page against entering borrower data | Data-protection minimisation: free text attracts personal data |
| 7 | The mortgage process pulls the approved discount | A transactional outbox written in the decision's transaction, a relay to a broker, consumers that deduplicate by event id; pull kept for reconciliation | Push without a gap between writing the decision and sending the message |
| 8 | One PostgreSQL and one app process in Compose; every start migrates and seeds (F40); the migrations are found next to the source, which works only for an editable install (F27) | Managed PostgreSQL 17 or later (for `transaction_timeout`) with a standby and point-in-time recovery; two or more app instances behind a load balancer; migrations as a release job that takes a lock, shipped inside the package; no seed outside local use | Untested backups are not backups; one process means any restart is an outage; instances that start together would race their migrations |
| 9 | Plain log lines and a health endpoint | JSON logs with correlation ids, metrics (decision latency, 409 and 503 rates, replays), alerts on requests pending too long, traces across the page, the service and loan origination | A disputed decision must be traceable in minutes |
| 10 | No rate limits and no body-size limit; unknown query parameters are ignored | Per-client rate limits and a body-size limit at the gateway; unknown query parameters refused | Unbounded resource use is a top API risk; the page-size limit bounds one call, not a flood of calls |
| 11 | CI runs the gates, the tests and a start of the app | Dependency and image scanning, a software bill of materials, signed images, gated deploys; Swagger's files served by the service instead of a CDN | Financial clients expect supply-chain evidence |
| 12 | The agent guardrails match command text (for example, the rules that refuse a force push) | A hook that parses git commands, so an unusual spelling cannot slip past; branch protection on the server | Text rules can always be dodged by another spelling; today CI and the human's approval of every push are the backstop ([agentic SDLC](agentic-sdlc.md)) |

**Later, when a need appears:**

- Refuse a resubmission whose terms equal the declined ones, or route it to the reviewer who declined (F14).
- Refuse a change sent against a request decided since the requester read it (F13), with a version that decisions
  also advance ([ADR 0001](adr/0001-revisioned-requests.md)).
- Order the review queue by when a request entered review, not when it was created (F31).
- A snapshot of the standard rate and the resulting rate, for audit.
- An application pick list in the page; free-text search; a total count on the list.
- A committed API description with a test that fails when it drifts.
- Property-based state tests, a consumer contract test with the mortgage process, API fuzzing, and a browser test of
  the page (F10); the page is checked by hand today ([`ui-checklist.md`](ui-checklist.md)).
- Notifications, driven by the outbox.
- A build of the database driver for Windows on ARM, or a pure-Python fallback, for local installs there.

**Deliberately absent, even in production:** microservices, event sourcing and a workflow engine. One request with
three states, owned by one team, does not justify them.
