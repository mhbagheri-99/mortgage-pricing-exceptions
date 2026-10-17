"""Composition root: read the settings, build the engine, create the app.

uvicorn pricing_exceptions.main:app
"""

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.settings import Settings

settings = Settings.from_env()
app = create_app(make_engine(settings.database_url))
