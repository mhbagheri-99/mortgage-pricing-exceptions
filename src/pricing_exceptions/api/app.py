"""The FastAPI application: the problem handlers, the contract routes, /health and the page at /ui."""

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse

from pricing_exceptions.api import problems, ui
from pricing_exceptions.api.identity import caller_dependency
from pricing_exceptions.api.routes import build_router
from pricing_exceptions.domain.model import Clock
from pricing_exceptions.service import Engine, queries

log = logging.getLogger(__name__)


class PricingExceptionsApp(FastAPI):
    def openapi(self) -> dict[str, Any]:
        """The API description, with every error body filed under application/problem+json."""
        first = self.openapi_schema is None
        description = super().openapi()  # built once, then cached
        if first:
            problems.as_problem_json(description)
        return description


def create_app(engine: Engine, clock: Clock, *, demo_identity: bool) -> FastAPI:
    if demo_identity:
        log.warning(
            "The demo identity is on: anyone who can reach this port can act as any seeded user "
            "by sending X-User-Id. Never run it this way in production (ADR 0004)."
        )
    app = PricingExceptionsApp(title="Pricing exception service", version="0.1.0")
    problems.install(app)
    app.include_router(build_router(engine, clock, caller_dependency(engine, demo_identity=demo_identity)))

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        """Someone who types only the address lands on the page, not on a JSON 404."""
        return RedirectResponse("/ui/")

    @app.get(
        "/health",
        tags=["operations"],
        responses={503: {"description": "The database is unreachable", "headers": problems.RETRY_AFTER}},
    )
    def health() -> JSONResponse:
        """Process and database check: 200 when the database answers, else 503 with Retry-After."""
        if queries.database_ok(engine):
            return JSONResponse({"status": "ok", "database": "ok"})
        return JSONResponse(
            {"status": "unavailable", "database": "unreachable"},
            status_code=503,
            headers={"Retry-After": "1"},
        )

    ui.install(app, engine, demo_identity=demo_identity)
    return app
