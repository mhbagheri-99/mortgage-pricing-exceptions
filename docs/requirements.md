# Requirements

Every requirement the tests must prove, with its id. A test names the requirement it proves with
`@pytest.mark.req(id="FR-3")`. The requirement-id gate (`uv run python scripts/check.py --check-reqs`) fails when
a test names an id that is not listed here, or when a requirement has no test once the phase that first tests it
is finished. "Where it comes from" is the client need behind each row, in our own words. "First tested in" names a
build phase (P1 to P8b, explained in [build history](history.md#identifiers-you-will-meet-in-the-history)).

## Functional requirements

| Id | Requirement | Where it comes from | First tested in |
| --- | --- | --- | --- |
| FR-1 | **Create.** A relationship manager opens a request on a mortgage application that exists, giving its id, a discount in basis points and a reason. | Managers ask for a discount on the standard rate of one application. | P3 |
| FR-2 | **Change.** The manager who made the request changes the discount or the reason after submitting it, until it is approved. Every change is a new, numbered revision. | One stakeholder wants the requested discount to stay changeable after submission. | P4 |
| FR-3 | **Decide.** A reviewer who did not make the request approves or declines one specific revision. The decision records who decided and when. | An authorised reviewer decides, and the outcome names the person and the time. Another stakeholder wants every approval to bind to exactly what was reviewed. | P2 |
| FR-4 | **Consume.** The mortgage application process reads the approved discount for an application, or "none" before approval. | The application process needs the approved discount before it can use it. | P3 |
| FR-5 | **Inspect.** Internal users see a request's current status, every revision and every decision. | Users need each request's history, including earlier decisions. | P3 |
| FR-6 | **Retry safely.** A create retried after a timeout returns the same request with 201. A different create for an application that already has a request gets 409 with the existing request. | Upstream systems retry after timeouts, and that must never create a second request. | P3 |
| FR-7 | **Find work.** Reviewers page through the requests awaiting review, oldest first, and following the cursor reaches every one. | Reviewers need the queue to decide, and the user interface needs it to review. | P3 |

## Non-functional requirements that tests prove

| Id | Requirement | Where it comes from | First tested in |
| --- | --- | --- | --- |
| N1 | **Integrity.** An approval binds to the revision the reviewer saw; a revision gets at most one decision; an application gets at most one request; an approved request never changes. | The second stakeholder; the consumer needs one answer it can trust. | P2 |
| N2 | **Idempotent create.** Identical creates, one after another or at the same moment, give exactly one request, and every caller gets 201 for it. | The retry need behind FR-6. | P3 |
| N3 | **Concurrency safety.** When two writers act on one request at once, exactly one wins and the other gets a precise 409, never a 500 or a lost update. | Two reviewers, or a change racing an approval. | P5 |
| N4 | **Audit.** Every state change writes exactly one history event in the same transaction, and history is only ever added to. | The history need behind FR-5. | P4 |
| N5 | **Access control.** The server enforces roles, requester-only changes and four-eyes; the demo identity is off unless it is switched on. | Our reading of an authorised reviewer: the reviewer role, and never the requester. | P2 |
| N8 | **Consistency over availability.** When the database is unavailable, a write is refused with 503 rather than accepted without its guarantees. | A wrong discount changes what a borrower pays. | P5 |
| N9 | **Bounded waiting.** A call waits at most 3 seconds for a database connection, then its transaction is capped at 5 seconds: about 8 seconds in all. A wait for a lock stops at 2 seconds. The demo identity's user lookup takes a connection of its own, so a full pool can add up to 3 seconds. | A hung call looks like a timeout and starts the retries of FR-6. | P5 |

These three have no tagged test. N6 (runs locally with one command) is checked by the CI step that starts the
app with `docker compose up` and calls it. N7 (evidence for the requirements above) is this gate itself. N10
(latency under 100 ms per call at internal-tool load) is not measured by any test or CI step.
