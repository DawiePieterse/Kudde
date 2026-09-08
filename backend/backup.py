"""The copy of the database taken before a migration alters it.

Deliberately not Boord's whole backup module. Boord (backend/backup.py)
also runs a nightly archive of the database plus worker photos, prunes to
fourteen, and copies each one to an off-site folder; Kudde has none of that
yet and this file is not the place to grow it. What is here is the one part
that cannot wait for it: a farm's herd records are a single SQLite file, and
a migration is the one moment a release rewrites that file in place.

Two things are carried over from Boord exactly, because both were learned
the expensive way:

- The copy is taken with SQLite's own backup API, never by copying the .db
  file. In `delete` journal mode a write in flight leaves the .db partially
  updated with the rollback data in a separate -journal file, so a plain
  copy taken mid-transaction is unrecoverable - and it fails silently, the
  file writing perfectly well and only turning out to be corrupt when
  somebody tries to restore it.
- A failure to take the copy raises. See migrate._snapshot_or_refuse():
  a caller that cannot get this copy must not migrate, so a quiet failure
  here would defeat the whole point of taking it.
"""
import os
import sqlite3
from datetime import datetime

from db import DATA_DIR, DB_PATH

BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
PRE_MIGRATION_PREFIX = "pre_migration_"
# Enough to walk back through a few bad releases, not so many that a farm's
# data folder fills with copies of the herd. Each is the size of the
# database itself, which for a herd is a few hundred KB.
PRE_MIGRATION_KEEP = 5


def _pre_migration_filenames() -> list:
    """Existing snapshots, oldest first. The timestamp is the leading part of
    the name and is fixed-width, so sorting by name sorts by age."""
    try:
        names = os.listdir(BACKUPS_DIR)
    except OSError:
        return []
    return sorted(f for f in names
                  if f.startswith(PRE_MIGRATION_PREFIX) and f.endswith(".db"))


def _snapshot_db(source_path: str, destination: str) -> None:
    """A consistent copy of a live SQLite database, via sqlite3's backup API,
    which coordinates with any concurrent writer."""
    source = sqlite3.connect(source_path)
    try:
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def snapshot_before_migration(label: str, db_path: str = None) -> str:
    """Full consistent copy of the database, returned as its path.

    Raises rather than returning None on any failure - see the module
    docstring.
    """
    source_path = db_path or DB_PATH
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    path = os.path.join(BACKUPS_DIR, f"{PRE_MIGRATION_PREFIX}{timestamp}_{safe}.db")

    _snapshot_db(source_path, path)

    # Only prune once the new copy is safely on disk. Pruning first would, on
    # a full disk, delete the oldest rollback point and then fail to write
    # the new one - leaving the farm with fewer copies than it started with
    # at the exact moment it was about to need one.
    for stale in _pre_migration_filenames()[:-PRE_MIGRATION_KEEP]:
        try:
            os.remove(os.path.join(BACKUPS_DIR, stale))
        except OSError:
            pass  # the copy that matters is already written; tidying can wait
    return path
