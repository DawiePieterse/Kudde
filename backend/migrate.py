"""Schema migrations for the Kudde database.

Kudde has no pre-Alembic installs to catch up - every farm starts on Alembic
from day one - so this stays much smaller than Boord's equivalent
(backend/migrate.py), which carries a whole legacy catch-up-and-stamp path
for farms whose databases predate migrations. Two things it does carry over,
because both protect a farm's only copy of its herd records:

1. **The database is copied before anything alters it.** A full consistent
   copy, kept in data/backups/. If that copy cannot be written, the
   migration does not run. See _snapshot_or_refuse().

2. **Drift is reported.** After migrating, the models are compared against
   the live schema. A mismatch means somebody changed models.py without
   writing a migration to go with it, and without this it surfaces on a farm
   as a "no such column" at runtime rather than on the dev machine at deploy
   time. It is printed, never raised: a farm server has to boot.

Run by main.py at startup.
"""
import os

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlmodel import SQLModel

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


def _current_revision(target_engine):
    with target_engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def current_revision(target_engine=None):
    """The revision a database is stamped at, or None if it has never run one."""
    return _current_revision(target_engine or default_engine)


def head_revision() -> str:
    """The newest revision the checked-out code carries."""
    return ScriptDirectory.from_config(_config(default_engine)).get_current_head()


def _has_app_tables(target_engine) -> bool:
    """True if this database holds anything of the farm's.

    alembic_version does not count - a database holding only that is one
    somebody stamped and never populated, and it should still be built from
    the migrations.
    """
    return bool(set(inspect(target_engine).get_table_names()) - {"alembic_version"})


def _snapshot_or_refuse(target_engine, label: str) -> None:
    """Copy the database before a migration touches it, or stop.

    Refusing is the deliberate choice. The alternative is altering a farm's
    only copy of its herd records with no way back, and "the server did not
    start" is recoverable in a way "the migration half-finished" is not -
    nothing has changed yet at this point, so checking the previous release
    back out is a complete fix.
    """
    from backup import snapshot_before_migration  # here, to keep db <- backup <- migrate acyclic

    db_path = target_engine.url.database
    if not db_path or not os.path.exists(db_path):
        return  # an in-memory or not-yet-created database has nothing to lose

    try:
        path = snapshot_before_migration(label, db_path)
    except Exception as e:
        rule = "=" * 60
        print(f"\n{rule}\n"
              f" MIGRATION STOPPED - could not back up the database first.\n"
              f"\n"
              f"     {e!r}\n"
              f"\n"
              f" Nothing has been changed. The database is exactly as it was,\n"
              f" so the previous release still runs against it.\n"
              f"\n"
              f" Usually this is a full disk. Free some space in data\\backups\\\n"
              f" and start the server again.\n"
              f"{rule}\n", flush=True)
        raise
    print(f"[migration] database copied to {os.path.basename(path)} before migrating", flush=True)


def _report_drift(target_engine) -> None:
    """Say so if models.py and the live schema have parted company.

    Only reachable when somebody edits a model and does not write the
    migration for it. On the dev machine that is a line in the console
    before the release is cut; on a farm it is the explanation for a "no
    such column" error that would otherwise take an afternoon.
    """
    with target_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)
    if not diff:
        return
    print("[migration] WARNING: the database does not match models.py:", flush=True)
    for entry in diff:
        print(f"[migration]   {entry}", flush=True)
    print("[migration] Usually this means models.py was changed without a migration to "
          "go with it. Write one with:", flush=True)
    print("[migration]   cd backend && python -m alembic revision --autogenerate "
          "-m \"what changed\"", flush=True)


def run_migrations(target_engine=None, snapshot: bool = True) -> None:
    """Bring the database to the newest revision. Safe to call on every start.

    target_engine/snapshot exist for the tests, which run the whole thing
    against throwaway databases. Everything on a farm uses the defaults.
    """
    target_engine = target_engine or default_engine
    cfg = _config(target_engine)
    head = ScriptDirectory.from_config(cfg).get_current_head()

    if not _has_app_tables(target_engine):
        # A new install. Nothing to lose and nothing to copy: build the
        # schema from the migrations themselves, so a fresh farm exercises
        # the same code path an upgrade does rather than a create_all that
        # would hide a broken migration until the first customer upgrade.
        print("[migration] new database - building the schema from migrations", flush=True)
        command.upgrade(cfg, "head")
        print(f"[migration] at {head}", flush=True)
        _report_drift(target_engine)
        return

    current = _current_revision(target_engine)
    if current == head:
        _report_drift(target_engine)
        return

    print(f"[migration] {current} -> {head}", flush=True)
    if snapshot:
        _snapshot_or_refuse(target_engine, f"{current}_to_{head}")
    command.upgrade(cfg, "head")
    print(f"[migration] at {head}", flush=True)
    _report_drift(target_engine)


if __name__ == "__main__":
    run_migrations()
    print("[migration] done", flush=True)
