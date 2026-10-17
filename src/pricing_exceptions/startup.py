"""Container entry point: apply the migrations, run the idempotent seed, then serve on port 8000.

python -m pricing_exceptions.startup
"""

import uvicorn

from pricing_exceptions.api.app import create_app
from pricing_exceptions.db.engine import make_engine
from pricing_exceptions.db.schema import migrate
from pricing_exceptions.db.seed import seed
from pricing_exceptions.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    seed(engine)
    # Inside the container the app must listen on every interface; Compose publishes only port 8000.
    uvicorn.run(create_app(engine), host="0.0.0.0", port=8000)  # noqa: S104


if __name__ == "__main__":
    main()
