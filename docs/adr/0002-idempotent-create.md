# ADR 0002: One request per application, and an idempotent create

Accepted.

## Context

Upstream systems retry a create when a call times out. A retry must never produce a second request, whether it
arrives seconds later or at the same moment as the first call. The usual answer is an `Idempotency-Key` header
with a table of keys. Here a simpler fact does the job: a mortgage application needs at most one pricing exception
request, because the request already holds every revision and decision ([ADR 0001](0001-revisioned-requests.md)).

## Decision

The application id is the natural key.

1. `uq_request_application`, a plain unique constraint on `application_id`, never partial.
2. The create inserts with `INSERT … ON CONFLICT ON CONSTRAINT uq_request_application DO NOTHING RETURNING id`.
3. When nothing is returned, the application already has a request. A new statement reads it, and the create
   compares the caller, the discount and the reason, **exactly as sent**, with the request's first submission (its
   SUBMITTED event):
   - the same: a **replay**. 201, the same `Location`, the header `Idempotent-Replayed: true`, and the request as it
     is now. One INFO log line marks it;
   - anything else: 409 `request-exists`, with `current`, the existing request.
4. Text is validated but stored exactly as sent, never trimmed, so a genuine retry always compares equal.

Two identical creates at the same moment: the second insert waits for the first transaction (at most 2 seconds,
the lock limit), then finds the committed row, reads it in a fresh statement, and replays. If the first rolls back,
the second inserts. This relies on READ COMMITTED, where each statement sees newly committed rows
([ADR 0003](0003-concurrency-control.md)). Race tests prove both orders on a real PostgreSQL.

The comparison uses the first submission, not the current row, because the row changes with every revision: a
retry that arrives after an amendment must still be recognised as a retry.

**Retries of revise and decide are not replays** ([audit F26](../history.md#the-audits-findings)). A revise or decide that succeeded but lost its
answer gets 409 on retry (`stale-revision`, or `invalid-state` after an approval). Nothing is written twice, and
`current` in the 409 shows the client whether its own change landed. Clients never retry a 409 blindly.

## Consequences

- **A replay answers with the request as it is now** (F4): after an amendment the body shows the new terms, after a
  decision it shows the outcome. The `Idempotent-Replayed` header says it is a replay, and the body is always the
  truth. Storing and replaying the first response would show a state that no longer exists.
- No time window: a retry a week later still replays. That is safe, because nothing is created.
- **An application holds one request for its whole life** (F3). After approval nobody can request another discount
  for it, and a mistaken request occupies its application. The production path is a withdraw or supersede rule
  ([production readiness](../production-readiness.md), item 4).
- **The trigger to add an `Idempotency-Key`:** the first time an application can hold a second request (after a
  withdraw or supersede). The unique constraint then becomes partial, the key becomes a nullable unique column, and
  this record is updated. The test that checks the constraint stays plain and non-partial changes with it.
- A replay and an identical new call cannot be told apart. Nothing is duplicated either way.

## Alternatives considered

| Alternative | Why not, for now |
| --- | --- |
| `Idempotency-Key` header and a key table | The textbook answer. It needs every client to send keys, a table, an expiry, and still a unique constraint for clients that send none. One request per application already gives the guarantee |
| Compare with the current row | A retry after an amendment would get 409 instead of its replay |
| Compare only the requester | A different create from the same manager would silently replay and lose its terms |
| Answer 200, not 201, when the replayed request has changed | Clients would need two success codes for one call; the header already marks the replay |
