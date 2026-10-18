# UI checklist

The page at <http://localhost:8000/ui/> is built for laptop and desktop screens: the request queue on the left, the
open request in the middle and its actions on the right, each scrolling on its own. The actions come from the
server: every response that shows a request says what its caller may do (`permissions`), so the page offers only
those forms and lists every other action with the server's reason. The server still checks every rule when an
action is tried.

The automated tests cover what the server adds: `/demo/users`, the security headers on `/ui` only, the page's
own files (no inline script or style, and no way for server text to become markup) in
`tests/integration/test_ui.py`, and the permissions in `tests/domain/test_permissions.py` and
`tests/integration/test_api_permissions.py`. This list covers what only a browser shows.

Start from fresh seed data: `docker compose down -v`, then `docker compose up --build`. Each tab remembers its
own "Acting as" choice (it lives in the tab's `sessionStorage`), so tab A and tab B can be two users. Open tab B
by typing the address into a new tab; "Duplicate tab" copies the choice.

| # | Tab, acting as | Do | Expect |
| --- | --- | --- | --- |
| 1 | A, nobody | Open `/ui/` | The yellow demo-identity strip; "Choose who you are acting as"; the queue asks who you are acting as |
| 2 | A, lead.fatima | Choose lead.fatima | "No request open"; the Pending review tab, oldest first: MA-1002 (revision 1, 20 bps) and MA-1003 (revision 2, 20 bps) |
| 3 | A, lead.fatima | New request: MA-1006, 25, a reason; Create request | The form closes; MA-1006 opens at revision 1 with 25 bps and "Created the request for MA-1006"; the queue has three requests |
| 4 | A, lead.fatima | Look at MA-1006's actions | "Change the terms" only. No Approve button: under "Not available to you", "Approve or decline: You cannot decide a request you made" (four-eyes, from the server) |
| 5 | A, lead.fatima | Per page 2, at the bottom of the queue, then Load more | MA-1002 and MA-1003 with Load more; then MA-1006 is added and Load more disappears |
| 6 | A, lead.fatima | New request again (the form still holds what was sent); Create request | "Already created: the same request came back as a replay, not a second request"; still one request for MA-1006 |
| 7 | A, lead.fatima | New request for MA-1006 at 30 bps | Inside the form: 409 "This application already has a request", what is current now, and "Open the existing request", which closes the form and opens it |
| 8 | B, rv.dana | New tab; choose rv.dana; open MA-1006 | "Approve or decline revision 1" with "Your decision applies to revision 1 only: 25 bps (0.25%)"; "Change the terms: Only a relationship manager can revise a request" under "Not available to you" |
| 9 | A, lead.fatima | Change the terms: 30 bps, a new reason; Send changes | "Sent: the request is now at revision 2" |
| 10 | B, rv.dana | Type a comment; Approve (the page still shows revision 1) | 409 "The request changed since you read it", "You sent revision 1; the current revision is 2", "Now: revision 2, 30 bps (0.30%), pending review", and Reload request. Nothing else on the page changes |
| 11 | B, rv.dana | Reload request; Approve | Revision 2 at 30 bps with the comment still typed; then "Approved: revision 2" and "Approved is final", no action forms, each action listed with why it is not available; the history reads Submitted, Amended, Approved; MA-1006 leaves the pending queue |
| 12 | A, lead.fatima | Send changes on the form still open from before the approval | 409 "Not allowed in the request's current status"; Reload request: no forms, and "Change the terms: A request that is APPROVED cannot be revised" |
| 13 | B, rv.dana | The Approved tab; then the Declined tab, and open MA-1005 | Approved: MA-1004 and MA-1006. Declined: MA-1005, "Declined by rv.erik at 2026-10-15 11:10:00 UTC, on revision 1", with its comment and a red marker in the history; "Approve or decline: A request that is DECLINED cannot be decided" |
| 14 | B, rm.anna | Open MA-1005, her declined request; then MA-1003, rm.bram's | MA-1005: "Resubmit with new terms". MA-1003: no forms, "Only a reviewer can decide a request" and "Only the relationship manager who made the request can revise it" |
| 15 | B, svc.mortgage | Choose svc.mortgage | 403 "You may not do this" on the queue and on the open request |
| 16 | B, rm.anna | New request on MA-1001 whose reason is `<img src=x onerror="document.title='pwned'">` | The reason shows as that exact text; no image appears and the tab title stays "Pricing exceptions". Reset the database afterwards (`docker compose down -v`) |
| 17 | A and B, rm.anna | Both tabs open MA-1001. B sends 35 bps. A changes only the reason and sends: 409. A clicks Reload request | A's form shows B's 35 bps and keeps A's new reason: a reload keeps only what was edited, never the old terms |
| 18 | B, rm.anna | New request on MA-1007 with the discount `abc` | Inside the form: 422 with "Discount: Input should be a valid integer"; nothing is created |
| 19 | n/a | `/docs` | Swagger loads and works: the page's security policy is not sent there; each request in its answers carries `permissions` |
| 20 | n/a | A 1366 by 768 window, then 1440 by 900 | Queue, request and actions side by side; the actions stay in view while the history scrolls; nothing scrolls sideways |
| 21 | n/a | Run a second copy with the demo identity off (below), open <http://localhost:8001/ui/> | "The demo identity is off, so this page cannot act as anyone"; the switcher and New request are disabled |

Step 21 starts a temporary copy of the service on port 8001 and stops it afterwards:

```bash
docker compose run --rm -d --name pricing-identity-off -e DEMO_IDENTITY=false --publish 127.0.0.1:8001:8000 api
```

```bash
docker stop pricing-identity-off
```

Phones are not a target: below about 820 pixels wide the panes stack one after another and stay usable.

Last run: 18 October 2026, on fresh seed data with this page: steps 1 to 4 and 8 to 14 as listed, and step 19's
`permissions` in the API description. Steps 5 to 7 and 15 to 21 ran earlier the same day, on the page before the
server reported permissions; their code is unchanged.
