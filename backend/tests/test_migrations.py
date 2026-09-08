"""The migration path.

Worth testing on its own because it is the one piece of this app that runs
against a farm's only copy of its herd records, at the exact moment a release
changes what those records look like, on a machine nobody is watching.

Carried over from Boord's scripts/selftest.py, which covers the same ground
for an app with a good deal more schema history behind it.
"""
import os
import sqlite3
import tempfile

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlmodel import SQLModel

import backup
import migrate


def _table_shape(path):
    """The structure of every table in a SQLite file.

    Compared as structure rather than as CREATE TABLE text on purpose:
    Alembic emits foreign keys alphabetically and SQLModel.create_all emits
    them in the order the model declares them, so two identical schemas have
    different SQL. Column order is left out for the same reason.
    """
    insp = inspect(create_engine(f"sqlite:///{path}"))
    shape = {}
    for table in sorted(insp.get_table_names()):
        if table == "alembic_version":
            continue
        shape[table] = {
            "columns": {c["name"]: (str(c["type"]), c["nullable"]) for c in insp.get_columns(table)},
            "pk": tuple(sorted(insp.get_pk_constraint(table)["constrained_columns"])),
            "fks": sorted((tuple(f["constrained_columns"]), f["referred_table"],
                           tuple(f["referred_columns"])) for f in insp.get_foreign_keys(table)),
            "indexes": sorted((i["name"], tuple(i["column_names"]), bool(i["unique"]))
                              for i in insp.get_indexes(table)),
        }
    return shape


def _drift(target_engine):
    with target_engine.connect() as conn:
        return compare_metadata(MigrationContext.configure(conn), SQLModel.metadata)


def test_migrations_build_what_the_models_describe(tmp_path):
    """A new install gets its schema from the migrations, not from
    create_all - so the migrations have to produce exactly what create_all
    would have. If they ever stop agreeing, a fresh farm quietly gets a
    different database from an upgraded one, and the difference shows up as a
    "no such column" months later on whichever of the two nobody tested."""
    by_migration = tmp_path / "migrated.db"
    by_models = tmp_path / "created.db"
    migrate.run_migrations(create_engine(f"sqlite:///{by_migration}"), snapshot=False)
    SQLModel.metadata.create_all(create_engine(f"sqlite:///{by_models}"))

    migrated, created = _table_shape(by_migration), _table_shape(by_models)
    assert set(migrated) == set(created), \
        f"tables only one way builds: {set(migrated) ^ set(created)}"
    for table in migrated:
        assert migrated[table] == created[table], (
            f"{table} differs\n  migrations: {migrated[table]}\n  models:     {created[table]}")

    assert not _drift(create_engine(f"sqlite:///{by_migration}")), \
        "a database built from the migrations does not match models.py - a migration is missing"


def test_the_running_server_is_at_head_with_no_drift(client):
    """The database this suite's own requests ran against - built by
    main.py's startup exactly as a farm's is."""
    assert migrate.current_revision() == migrate.head_revision()
    assert not _drift(migrate.default_engine)


def test_migrating_twice_changes_nothing(tmp_path):
    """run_migrations() is called on every single server start, so the
    second and thousandth call have to be no-ops."""
    path = tmp_path / "twice.db"
    engine = create_engine(f"sqlite:///{path}")
    migrate.run_migrations(engine, snapshot=False)
    first = _table_shape(path)
    migrate.run_migrations(engine, snapshot=False)
    assert _table_shape(path) == first
    assert migrate.current_revision(engine) == migrate.head_revision()


def test_a_farm_upgrades_without_losing_its_herd(tmp_path):
    """The path an existing farm actually takes: a database built by the
    PREVIOUS release, with real rows in it, brought to today's head.

    Building it from scratch (as the test above does) only proves the
    migrations agree with the models - it never exercises an ALTER against
    data. On SQLite an ALTER is a rebuild-and-copy, which is exactly where
    rows go missing.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    path = tmp_path / "farm.db"
    engine = create_engine(f"sqlite:///{path}")
    cfg = migrate._config(engine)
    revisions = [r.revision for r in
                 ScriptDirectory.from_config(cfg).walk_revisions()][::-1]
    if len(revisions) < 2:
        pytest.skip("only one revision exists - nothing to upgrade from yet")

    # Stop one short of head: that is what the farm is running.
    command.upgrade(cfg, revisions[-2])
    con = sqlite3.connect(path)
    con.execute("INSERT INTO animal (tag, name, breed, sex, status, created_at, updated_at) "
                "VALUES ('A1', 'Bessie', 'Nguni', 'female', 'alive', '2026-01-01', '2026-01-01')")
    con.execute("INSERT INTO event (animal_id, kind, event_date, value, note, location, created_at) "
                "VALUES (1, 'weight', '2026-01-02', 310.0, '', '', '2026-01-02')")
    con.commit()
    con.close()

    migrate.run_migrations(engine, snapshot=False)

    assert migrate.current_revision(engine) == migrate.head_revision()
    assert not _drift(engine), "the upgraded database does not match models.py"
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT tag, name FROM animal").fetchall() == [("A1", "Bessie")]
        assert con.execute("SELECT value FROM event").fetchall() == [(310.0,)]
        # Everything already on the farm keeps its NULL for a column added
        # after it was recorded.
        assert con.execute("SELECT client_uuid FROM event").fetchall() == [(None,)]
    finally:
        con.close()


def test_a_farm_upgrade_is_snapshotted_first(tmp_path, monkeypatch):
    """The copy is taken because the database has something to lose - the
    case the refusal test below is the other half of."""
    from alembic import command
    from alembic.script import ScriptDirectory

    path = tmp_path / "farm.db"
    engine = create_engine(f"sqlite:///{path}")
    cfg = migrate._config(engine)
    revisions = [r.revision for r in
                 ScriptDirectory.from_config(cfg).walk_revisions()][::-1]
    if len(revisions) < 2:
        pytest.skip("only one revision exists - nothing to upgrade from yet")
    command.upgrade(cfg, revisions[-2])

    taken = []
    monkeypatch.setattr(backup, "snapshot_before_migration",
                        lambda label, db=None: taken.append(label) or "copy.db")
    migrate.run_migrations(engine, snapshot=True)
    assert taken, "an existing database was migrated without being copied first"


def test_a_new_database_is_not_snapshotted(tmp_path, monkeypatch):
    """There is nothing to lose on a first boot, and taking a copy of a
    database that does not exist yet would fail on every new install."""
    called = []
    monkeypatch.setattr(backup, "snapshot_before_migration",
                        lambda *a, **k: called.append(a) or "x")
    migrate.run_migrations(create_engine(f"sqlite:///{tmp_path / 'new.db'}"), snapshot=True)
    assert called == []


def test_the_snapshot_is_a_usable_copy_of_the_data(tmp_path):
    """Taken through SQLite's own backup API rather than by copying the file:
    in `delete` journal mode a write in flight leaves the .db partially
    updated with the rollback data in a separate -journal file, so a plain
    copy taken mid-transaction is unrecoverable - and it fails silently."""
    source = tmp_path / "live.db"
    con = sqlite3.connect(source)
    con.execute("CREATE TABLE animal (tag TEXT)")
    con.execute("INSERT INTO animal VALUES ('A1')")
    con.commit()
    con.close()

    path = backup.snapshot_before_migration("test", str(source))
    assert os.path.exists(path)
    copy = sqlite3.connect(path)
    try:
        assert copy.execute("SELECT tag FROM animal").fetchall() == [("A1",)]
    finally:
        copy.close()


def test_a_migration_that_cannot_be_backed_up_does_not_run(tmp_path, monkeypatch):
    """Refusing is the deliberate choice. The alternative is altering a
    farm's only copy of its herd records with no way back, and "the server
    did not start" is recoverable in a way "the migration half-finished" is
    not - nothing has changed at this point, so checking the previous release
    back out is a complete fix."""
    path = tmp_path / "existing.db"
    engine = create_engine(f"sqlite:///{path}")
    # An existing database at no revision: whatever the head is, this one has
    # to travel, so run_migrations reaches the snapshot step.
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE animal (tag TEXT)")
    con.commit()
    con.close()
    before = _table_shape(path)

    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "snapshot_before_migration", full_disk)
    with pytest.raises(OSError):
        migrate.run_migrations(engine, snapshot=True)
    assert _table_shape(path) == before, "the database was altered despite the refusal"


def test_snapshots_are_pruned_but_never_before_the_new_one_lands(tmp_path, monkeypatch):
    """Pruning first would, on a full disk, delete the oldest rollback point
    and then fail to write the new one - leaving the farm with fewer copies
    than it started with at the exact moment it was about to need one."""
    backups = tmp_path / "backups"
    monkeypatch.setattr(backup, "BACKUPS_DIR", str(backups))
    source = tmp_path / "live.db"
    sqlite3.connect(source).close()

    stamps = iter(f"2026090{i}_120000" for i in range(1, 9))
    monkeypatch.setattr(backup, "datetime", type("D", (), {
        "now": staticmethod(lambda: type("T", (), {"strftime": staticmethod(lambda _: next(stamps))})())
    }))
    for _ in range(backup.PRE_MIGRATION_KEEP + 2):
        backup.snapshot_before_migration("upgrade", str(source))

    kept = backup._pre_migration_filenames()
    assert len(kept) == backup.PRE_MIGRATION_KEEP
    assert kept == sorted(kept)[-backup.PRE_MIGRATION_KEEP:], "the oldest copies should go first"
