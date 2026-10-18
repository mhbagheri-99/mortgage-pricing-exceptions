"""The two settings, read once from the environment."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    demo_identity: bool  # the X-User-Id stand-in works only when this is true (design doc D6)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> Settings:
        url = environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set, for example postgresql+psycopg://user:password@host/db"
            )
        return cls(database_url=url, demo_identity=environ.get("DEMO_IDENTITY", "").lower() == "true")
