"""The FastAPI application.

Red skeleton for P3: the new arguments only; the contract endpoints arrive with the code that makes the tests
pass.
"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from pricing_exceptions.domain.model import Clock
from pricing_exceptions.service import Engine, queries


def create_app(engine: Engine, clock: Clock, *, demo_identity: bool) -> FastAPI:
    app = FastAPI(title="Pricing exception service", version="0.1.0")

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
