"""The browser page at /ui (ADR 0004) and the user list its acting-as switcher reads.

The page is three static files. They are sent with a Content Security Policy, the second line of defence
against cross-site scripting, on /ui only: Swagger at /docs loads its files from a CDN and starts with an
inline script, which the policy would block (docs/architecture.md, "Security"). Two checks read the page's own
files, so the policy can never break the page silently and text from the server can only reach the page as
text.
"""

from html.parser import HTMLParser
from pathlib import Path

import pytest
from calls import REQUESTS, TickingClock, as_user
from fastapi.testclient import TestClient
from sqlalchemy import Engine

import pricing_exceptions.api
from pricing_exceptions.api.app import create_app

UI = Path(pricing_exceptions.api.__file__).parent / "ui"
# default-src covers scripts, styles and fetch; the other three are the directives it does not cover: a
# planted <base> tag, a form posting elsewhere, and another site framing the page.
POLICY = "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
STAFF_USERS = [
    {
        "id": "lead.fatima",
        "display_name": "Fatima, team lead: relationship manager and reviewer",
        "roles": ["RELATIONSHIP_MANAGER", "REVIEWER"],
    },
    {"id": "rm.anna", "display_name": "Anna, relationship manager", "roles": ["RELATIONSHIP_MANAGER"]},
    {"id": "rm.bram", "display_name": "Bram, relationship manager", "roles": ["RELATIONSHIP_MANAGER"]},
    {"id": "rv.dana", "display_name": "Dana, reviewer", "roles": ["REVIEWER"]},
    {"id": "rv.erik", "display_name": "Erik, reviewer", "roles": ["REVIEWER"]},
]


def test_the_demo_user_list_names_every_staff_user(client: TestClient) -> None:
    # No X-User-Id: the switcher reads the list before anyone has been chosen. svc.mortgage is not in it:
    # it is the mortgage process's account, which reads approved discounts through the API, not this page.
    response = client.get("/demo/users")
    assert response.status_code == 200
    assert response.json() == STAFF_USERS


@pytest.mark.req(id="N5")
def test_the_demo_user_list_is_404_with_the_demo_identity_off(engine: Engine, clock: TickingClock) -> None:
    off = TestClient(create_app(engine, clock, demo_identity=False))
    response = off.get("/demo/users")
    assert response.status_code == 404
    assert "rm.anna" not in response.text


def test_the_bare_address_opens_the_page(client: TestClient) -> None:
    """Someone who types only the address lands on the page, not on a JSON 404. Not part of the API."""
    response = client.get("/", follow_redirects=False)
    assert (response.status_code, response.headers["Location"]) == (307, "/ui/")
    assert "/" not in client.get("/openapi.json").json()["paths"]


def test_the_demo_user_list_is_not_in_the_api_description(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert REQUESTS in paths
    assert "/demo/users" not in paths


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        pytest.param("/ui/", "text/html", id="page"),
        pytest.param("/ui", "text/html", id="page without the slash"),
        pytest.param("/ui/app.js", "text/javascript", id="script"),
        pytest.param("/ui/app.css", "text/css", id="style sheet"),
    ],
)
def test_the_page_is_served_with_its_security_policy(client: TestClient, path: str, media_type: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Content-Type"].split(";")[0] == media_type
    assert response.headers["Content-Security-Policy"] == POLICY
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    # Revalidate on every load, so a browser never runs an old app.js against a newer API.
    assert response.headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/docs", id="swagger"),
        pytest.param("/openapi.json", id="api description"),
        pytest.param(REQUESTS, id="json api"),
        pytest.param("/health", id="health"),
    ],
)
def test_the_policy_is_sent_on_the_page_only(client: TestClient, path: str) -> None:
    response = client.get(path, headers=as_user("rv.dana"))
    assert response.status_code == 200
    assert "Content-Security-Policy" not in response.headers


class InlineCode(HTMLParser):
    """Collects what the policy would block in a page: inline scripts and styles, and event-handler
    attributes. It also notes the files the page loads, so an empty page cannot pass."""

    def __init__(self) -> None:
        super().__init__()
        self.blocked: list[str] = []
        self.loads: list[str] = []
        self.in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        for name, value in attrs:
            if name == "style" or name.startswith("on"):
                self.blocked.append(f"<{tag} {name}=...>")
            if (value or "").strip().lower().startswith("javascript:"):
                self.blocked.append(f"<{tag} {name}=javascript:...>")
        if tag == "style":
            self.blocked.append("<style>")
        if tag == "script":
            self.in_script = True
            if not values.get("src"):
                self.blocked.append("<script> without src")
        for name in ("src", "href"):
            if values.get(name):
                self.loads.append(str(values[name]))

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.in_script = False

    def handle_data(self, data: str) -> None:
        if self.in_script and data.strip():
            self.blocked.append("code inside <script>")


def test_the_page_has_no_inline_script_or_style() -> None:
    page = InlineCode()
    page.feed((UI / "index.html").read_text(encoding="utf-8"))
    assert page.blocked == []
    assert "app.js" in page.loads
    assert "app.css" in page.loads


# The ways a script turns text into markup or code. Server text (a reason, a comment) must only ever be set as
# text, through textContent (docs/architecture.md, "Security"). The newer HTML parsers are listed too.
MARKUP_SINKS = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function(",
    "setHTMLUnsafe",
    "parseHTMLUnsafe",
    "createContextualFragment",
    "DOMParser",
    "srcdoc",
    'setTimeout("',
    'setInterval("',
)


def test_the_script_never_writes_markup() -> None:
    script = (UI / "app.js").read_text(encoding="utf-8")
    assert [sink for sink in MARKUP_SINKS if sink in script] == []
    assert "textContent" in script
