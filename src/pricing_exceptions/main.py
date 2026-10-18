"""Composition root: read the settings, build the engine and the clock, create the app.

uvicorn --factory pricing_exceptions.main:build_app
"""

import logging
from datetime import UTC, datetime

from fastapi import FastAPI

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.settings import Settings


def system_clock() -> datetime:
    """The one place the real time is read; tests inject a fixed or ticking clock instead."""
    return datetime.now(UTC)


def configure_logging() -> None:
    """Our own log lines (design doc 7.9) at INFO and above, with their level, on standard error."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def build_app() -> FastAPI:
    settings = Settings.from_env()
    configure_logging()
    return create_app(make_engine(settings.database_url), system_clock, demo_identity=settings.demo_identity)
