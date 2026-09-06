"""Schema migrations for the Kudde database.

Boord's equivalent (backend/migrate.py) also catches up databases that
predate Alembic, and snapshots the database before every migration. Kudde
has no pre-Alembic installs to catch up - every farm starts on Alembic from
day one - so this stays deliberately smaller. Worth adding the
snapshot-before-migrate step (see Boord's version) once Kudde is running on
real farms and a bad migration would mean losing someone's herd records.

Run by main.py at startup.
"""
import os

from alembic import command
from alembic.config import Config

import models  # noqa: F401 - importing it registers every table on SQLModel.metadata
from db import engine as default_engine

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")
MIGRATIONS_DIR = os.path.join(BACKEND_DIR, "migrations")


def _config(target_engine) -> Config:
    cfg = Config(ALEMBIC_INI)
    # Absolute, because a Scheduled Task starts the server from whatever
    # directory Windows picks and the relative path in alembic.ini only
    # works for someone standing in backend/.
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    cfg.attributes["connectable"] = target_engine
    # env.py reads this. Inside the server process, alembic.ini's logging
    # config would replace uvicorn's and the app would go quiet.
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations(target_engine=None) -> None:
    """Bring the database to the newest revision. Safe to call on every start."""
    command.upgrade(_config(target_engine or default_engine), "head")


if __name__ == "__main__":
    run_migrations()
    print("[migration] done", flush=True)
