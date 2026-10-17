"""Fictional seed data. Every insert is ON CONFLICT DO NOTHING, so running it again changes nothing.

The container runs it on every start (design doc 9.3). P1 seeds the users and applications; P3 adds the four
seeded requests and their history.

    python -m pricing_exceptions.db.seed     # uses DATABASE_URL
"""

from sqlalchemy import Engine, text

USERS = [
    ("rm.anna", "Anna, relationship manager", ["RELATIONSHIP_MANAGER"]),
    ("rm.bram", "Bram, relationship manager", ["RELATIONSHIP_MANAGER"]),
    ("rv.dana", "Dana, reviewer", ["REVIEWER"]),
    ("rv.erik", "Erik, reviewer", ["REVIEWER"]),
    (
        "lead.fatima",
        "Fatima, team lead: relationship manager and reviewer",
        ["RELATIONSHIP_MANAGER", "REVIEWER"],
    ),
    ("svc.mortgage", "Mortgage application process (a service)", ["MORTGAGE_PROCESS"]),
]
APPLICATIONS = [(f"MA-{n}", f"Fictional mortgage application {n}") for n in range(1001, 1009)]


def seed(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, display_name, roles) VALUES (:id, :display_name, :roles) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            [{"id": i, "display_name": name, "roles": roles} for i, name, roles in USERS],
        )
        conn.execute(
            text(
                "INSERT INTO mortgage_applications (id, label) VALUES (:id, :label) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            [{"id": i, "label": label} for i, label in APPLICATIONS],
        )


if __name__ == "__main__":
    from pricing_exceptions.db.engine import make_engine
    from pricing_exceptions.settings import Settings

    seed(make_engine(Settings.from_env().database_url))
