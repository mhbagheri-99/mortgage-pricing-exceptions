# UI checklist

The page at <http://localhost:8000/ui/> is built for laptop and desktop screens: the request queue on the left, the
open request in the middle and its actions on the right, each scrolling on its own. It is checked by hand, in two
browser tabs, against the Compose app. The automated tests in `tests/integration/test_ui.py` cover what the server
adds: `/demo/users`, the security headers on `/ui` only, and the page's own files (no inline script or style, and
no way for server text to become markup). This list covers what only a browser shows.

Start from fresh seed data: `docker compose down -v`, then `docker compose up --build`. Each tab remembers its
own "Acting as" choice (it lives in the tab's `sessionStorage`), so tab A and tab B can be two users. Open tab B
by typing the address into a new tab; "Duplicate tab" copies the choice.

| # | Tab, acting as | Do | Expect |
| --- | --- | --- | --- |
| 1 | A, nobody | Open `/ui/` | The yellow demo-identity strip; "Choose who you are acting as"; the queue asks who you are acting as |
| 2 | A, lead.fatima | Choose lead.fatima | "No request open"; the Pending review tab, oldest first: MA-1002 (revision 1, 20 bps) and MA-1003 (revision 2, 20 bps) |
| 3 | A, lead.fatima | New request: MA-1006, 25, a reason; Create request | The form closes; MA-1006 opens at revision 1 with 25 bps and "Created the request for MA-1006"; the queue has three requests |
| 4 | A, lead.fatima | Per page 2, at the bottom of the queue, then Load more | MA-1002 and MA-1003 with Load more; then MA-1006 is added and Load more disappears |
| 5 | A, lead.fatima | New request again (the form still holds what was sent); Create request | "Already created: the same request came back as a replay, not a second request"; still one request for MA-1006 |
| 6 | A, lead.fatima | New request for MA-1006 at 30 bps | Inside the form: 409 "This application already has a request", what is current now, and "Open the existing request", which closes the form and opens it |
| 7 | A, lead.fatima | Approve MA-1006, her own request | The Approve button is there; the answer is 403 "You cannot decide your own request", under the buttons |
| 8 | B, rv.dana | New tab; choose rv.dana; open MA-1006 | "Decide revision 1" with "Your decision applies to revision 1 only: 25 bps (0.25%)" |
| 9 | A, lead.fatima | Revise: 30 bps, a new reason; Send revision | "Sent: the request is now at revision 2" |
| 10 | B, rv.dana | Type a comment; Approve (the page still shows revision 1) | 409 "The request changed since you read it", "You sent revision 1; the current revision is 2", "Now: revision 2, 30 bps (0.30%), pending review", and Reload request. Nothing else on the page changes |
| 11 | B, rv.dana | Reload request; Approve | Revision 2 at 30 bps, the comment still typed, and the Revise form showing the new 30 bps; then "Approved: revision 2", the latest decision names rv.dana and the time, the history reads Submitted, Amended, Approved, and MA-1006 leaves the pending queue |
| 12 | A, lead.fatima | Send revision on MA-1006 | 409 "Not allowed in the request's current status": approved is final |
| 13 | B, rv.dana | The Approved tab; then the Declined tab, and open MA-1005 | Approved: MA-1004 and MA-1006. Declined: MA-1005, "Declined by rv.erik at 2026-10-15 11:10:00 UTC, on revision 1", with its comment, and a red marker in the history |
| 14 | B, svc.mortgage | Choose svc.mortgage | 403 "You may not do this" on the queue and on the open request |
| 15 | B, rm.anna | New request on MA-1001 whose reason is `<img src=x onerror="document.title='pwned'">` | The reason shows as that exact text; no image appears and the tab title stays "Pricing exceptions" |
| 16 | A and B, rm.anna | Both tabs open MA-1001. B sends 35 bps. A changes only the reason and sends: 409. A clicks Reload request | A's Revise form shows B's 35 bps and keeps A's new reason: a reload keeps only what was edited, never the old terms |
| 17 | B, rm.anna | New request on MA-1007 with the discount `abc` | Inside the form: 422 with "Discount: Input should be a valid integer"; nothing is created |
| 18 | n/a | `/docs` | Swagger loads and works: the page's security policy is not sent there |
| 19 | n/a | A 1366 by 768 window, then 1440 by 900 | Queue, request and actions side by side; the Decide buttons stay in view while the history scrolls; nothing scrolls sideways |
| 20 | n/a | Run a second copy with the demo identity off (below), open <http://localhost:8001/ui/> | "The demo identity is off, so this page cannot act as anyone"; the switcher and New request are disabled |

Step 20 starts a temporary copy of the service on port 8001 and stops it afterwards:

```bash
docker compose run --rm -d --name pricing-identity-off -e DEMO_IDENTITY=false --publish 127.0.0.1:8001:8000 api
```

```bash
docker stop pricing-identity-off
```

Phones are not a target: below about 820 pixels wide the panes stack one after another and stay usable.

Last run: 18 October 2026, every step as expected: steps 3 and 7 on the page's first layout, the rest on this
one. MA-1006 was already used in that database, so the two-tab steps ran on MA-1007 and MA-1001, and steps 15 and
16 revised an existing request instead of creating one.
