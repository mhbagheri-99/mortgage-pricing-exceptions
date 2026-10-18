"""The browser page at /ui and the user list its acting-as switcher reads (design doc D11, 6.6).

The page is three static files in `ui/`. It calls only the public /api/v1 endpoints, plus /demo/users, and
decides no access: the actions it offers come from the server's `permissions`, and every refusal it shows is
the server's own wording.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

from pricing_exceptions.api.schemas import DemoUser
from pricing_exceptions.service import Engine, queries

UI_DIR = Path(__file__).parent / "ui"

# Sent with the page's files only (design doc 7.5). default-src 'self' lets the page load its own script,
# style sheet and API calls and nothing else, so no inline script can run even if text were ever turned into
# markup. The other three directives are the ones default-src does not cover. nosniff makes the browser trust
# each file's declared type instead of guessing. no-cache makes it ask again on every load (a cheap 304 when
# nothing changed), so after a rebuild it never runs an old app.js against the new API (P7 review).
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-cache",
}


class PageFiles(StaticFiles):
    """StaticFiles that adds the security headers to every file it serves. Only /ui is served this way, so
    Swagger at /docs, which loads its files from a CDN and starts with an inline script, keeps working."""

    def file_response(
        self, full_path: PathLike, stat_result: os.stat_result, scope: Scope, status_code: int = 200
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers.update(SECURITY_HEADERS)
        return response


def install(app: FastAPI, engine: Engine, *, demo_identity: bool) -> None:
    if demo_identity:
        # Registered only with the demo identity on: off, it is a 404 like any unknown path, so it gives away
        # nothing. Left out of the API description, because it is not part of the contract (design doc 5.1).
        @app.get("/demo/users", include_in_schema=False)
        def demo_users() -> list[DemoUser]:
            """Every seeded user, for the page's acting-as switcher. No X-User-Id: nobody is chosen yet."""
            return [DemoUser.of(user) for user in queries.list_users(engine)]

    app.mount("/ui", PageFiles(directory=UI_DIR, html=True), name="ui")
