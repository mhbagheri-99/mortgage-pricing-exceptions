"""The FastAPI application: the problem handlers, the contract routes and /health."""

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from pricing_exceptions.api import problems
from pricing_exceptions.api.identity import caller_dependency
from pricing_exceptions.api.routes import build_router
from pricing_exceptions.domain.model import Clock
from pricing_exceptions.service import Engine, queries

log = logging.getLogger(__name__)


def create_app(engine: Engine, clock: Clock, *, demo_identity: bool) -> FastAPI:
    if demo_identity:
        log.warning(
            "The demo identity is on: anyone who can reach this port can act as any seeded user "
            "by sending X-User-Id. Never run it this way in production (design doc D6)."
        )
    app = FastAPI(title="Pricing exception service", version="0.1.0")
    problems.install(app)
    app.include_router(build_router(engine, clock, caller_dependency(engine, demo_identity=demo_identity)))

    @app.get("/health", tags=["operations"])
    def health() -> JSONResponse:
        """Process and database check: 200 when the database answers, else 503 with Retry-After."""
        if queries.database_ok(engine):
            return JSONResponse({"status": "ok", "database": "ok"})
        return JSONResponse(
            {"status": "unavailable", "database": "unreachable"},
            status_code=503,
            headers={"Retry-After": "1"},
        )

    return app
