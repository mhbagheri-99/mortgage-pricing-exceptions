"""Use cases: one transaction per command, plus the read queries."""

# The database handle the api passes to the service. Re-exported here so the api can name it without
# importing SQLAlchemy itself: the api never runs SQL (import-linter contract 3).
from sqlalchemy import Engine as Engine
