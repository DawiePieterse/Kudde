"""The copy of a farm's data taken before a migration alters it.

Deliberately not Boord's whole backup module. Boord (backend/backup.py)
also runs a nightly archive, prunes to fourteen, and copies each one to an
off-site folder; Kudde has none of that yet and this file is not the place
to grow it. What is here is the one part that cannot wait for it: a farm's
herd records are a single SQLite file, and a migration is the one moment a
release rewrites that file in place.

A farm's data is that file AND the photos beside it, so a rollback point
that restores only the database puts the two out of step: every photo
deleted between the snapshot and the rollback comes back as a row pointing
at a file that is no longer there. That window is usually minutes - a
migration goes wrong and the farmer reverts - which is why photos are taken
here as well but are NOT allowed to stop a migration; see below.

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
import shutil
import sqlite3
from datetime import datetime

from db import DATA_DIR, DB_PATH, PHOTOS_DIR

BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
PRE_MIGRATION_PREFIX = "pre_migration_"
# The photos belonging to one snapshot, in a directory beside its .db. The
# .db stays the thing that marks a snapshot as taken: it is written first,
# and it is what _pre_migration_filenames() counts and prunes by.
PRE_MIGRATION_PHOTOS_SUFFIX = "_photos"
# Enough to walk back through a few bad releases, not so many that a farm's
# data folder fills with copies of the herd. Each is the size of the
# database itself, which for a herd is a few hundred KB - the photos beside
# it are hard links, so they add names rather than bytes (see below).
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


def _photos_dir_for(db_snapshot_path: str) -> str:
    """The photo directory belonging to a given .db snapshot."""
    return db_snapshot_path[:-len(".db")] + PRE_MIGRATION_PHOTOS_SUFFIX


def _snapshot_photos(source_dir: str, destination: str) -> tuple:
    """Take the photos into `destination`, and say what happened.

    Hard links, not copies. A photo is written once to a fresh uuid filename
    and never rewritten in place (routers/photos.py), so a second name for
    the same bytes is a complete and permanently correct snapshot of it - at
    no disk cost. Deleting the animal's photo later drops the live name and
    leaves this one, which is the whole point.

    That matters more than tidiness: photos are the bulk of a farm's data,
    and copying them PRE_MIGRATION_KEEP times over is how a farm server's
    disk fills up - at which point taking a snapshot fails, and a failed
    snapshot refuses the migration and the server does not start. Adding
    photos to this must not become a new way for a farm to fail to boot.

    Falls back to a copy per file where the filesystem will not link (a data
    directory on a different volume, or one that has no hard links at all).
    """
    linked = copied = failed = 0
    for root, _dirs, files in os.walk(source_dir):
        relative = os.path.relpath(root, source_dir)
        target_root = destination if relative == "." else os.path.join(destination, relative)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            source = os.path.join(root, name)
            target = os.path.join(target_root, name)
            try:
                os.link(source, target)
                linked += 1
            except OSError:
                try:
                    shutil.copy2(source, target)
                    copied += 1
                except OSError:
                    # Counted and reported, never raised - see
                    # snapshot_before_migration().
                    failed += 1
    return linked, copied, failed


def snapshot_before_migration(label: str, db_path: str = None,
                              photos_dir: str = None) -> str:
    """Full consistent copy of the database, plus the photos, returned as the
    database copy's path.

    The database half raises rather than returning None on any failure - see
    the module docstring: the caller must not migrate without it.

    The photo half never raises, and this is deliberate rather than sloppy.
    A migration rewrites the database in place; it does not touch a single
    photo file, so photos are not what it puts at risk. Refusing to start a
    farm's server because some photos could not be linked would trade a real
    outage for a rollback point that is only marginally better than the one
    already in hand.
    """
    source_path = db_path or DB_PATH
    source_photos = photos_dir or PHOTOS_DIR
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    path = os.path.join(BACKUPS_DIR, f"{PRE_MIGRATION_PREFIX}{timestamp}_{safe}.db")

    _snapshot_db(source_path, path)

    # Photos after the database, not before. Nothing writes to either during
    # a migration - run_migrations() is called from startup, before the app
    # takes its first request - but of the two orders this is the safe one
    # anyway: a photo arriving in between ends up as a file the snapshot's
    # database does not know about, which costs nothing, where the other
    # order would leave a row pointing at a photo that was never taken.
    if os.path.isdir(source_photos):
        linked, copied, failed = _snapshot_photos(source_photos, _photos_dir_for(path))
        if failed:
            print(f"[migration] WARNING: {failed} photo(s) could not be taken into the "
                  f"backup. The database copy is complete and the migration will go "
                  f"ahead - a migration does not alter photos.", flush=True)
        # Worded to stand on its own: migrate.py prints the line naming the
        # database copy after this returns, so this one comes out first.
        how = "linked" if not copied else f"{copied} copied, {linked} linked"
        print(f"[migration] {linked + copied} photo(s) taken into the backup ({how})",
              flush=True)

    # Only prune once the new copy is safely on disk. Pruning first would, on
    # a full disk, delete the oldest rollback point and then fail to write
    # the new one - leaving the farm with fewer copies than it started with
    # at the exact moment it was about to need one.
    for stale in _pre_migration_filenames()[:-PRE_MIGRATION_KEEP]:
        try:
            os.remove(os.path.join(BACKUPS_DIR, stale))
        except OSError:
            pass  # the copy that matters is already written; tidying can wait
        # The photos go with the .db they belong to. Dropping these names
        # frees the bytes only for photos the farmer has since deleted from
        # the herd - a live photo keeps its own name regardless.
        shutil.rmtree(os.path.join(BACKUPS_DIR, _photos_dir_for(stale)), ignore_errors=True)
    return path
