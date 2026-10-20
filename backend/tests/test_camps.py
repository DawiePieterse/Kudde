"""Camps as a managed list: set up in Settings, picked from chips, and kept
in step with the plain-text locations on the events."""
import sqlite3

from sqlmodel import create_engine

import migrate
from tests.conftest import RECENTLY, add_animal as _add, move as _move


def test_camps_are_created_renamed_ordered_and_archived(client):
    a = client.post("/api/camps", json={"name": "  Bo-kamp "}).json()
    b = client.post("/api/camps", json={"name": "Onder-kamp"}).json()
    assert a["name"] == "Bo-kamp" and a["alive_count"] == 0
    assert [c["name"] for c in client.get("/api/camps").json()] == ["Bo-kamp", "Onder-kamp"]

    # The farmer's order, not the alphabet.
    client.put("/api/camps/order", json={"ids": [b["id"], a["id"]]})
    assert [c["name"] for c in client.get("/api/camps").json()] == ["Onder-kamp", "Bo-kamp"]

    assert client.patch(f"/api/camps/{a['id']}", json={"name": "Bokamp"}).json()["name"] == "Bokamp"
    assert client.patch(f"/api/camps/{a['id']}", json={"archived": True}).status_code == 200
    assert [c["name"] for c in client.get("/api/camps").json()] == ["Onder-kamp"]
    assert [c["name"] for c in client.get("/api/camps?include_archived=true").json()] == ["Onder-kamp", "Bokamp"]
    assert client.post("/api/camps", json={"name": ""}).status_code == 422
    assert client.patch("/api/camps/999", json={"name": "x"}).status_code == 404


def test_a_camp_name_is_one_camp_whatever_its_capitals(client):
    client.post("/api/camps", json={"name": "Noordkamp"})
    r = client.post("/api/camps", json={"name": "noordkamp"})
    assert r.status_code == 409, r.text
    client.post("/api/camps", json={"name": "Suid"})
    camps = {c["name"]: c for c in client.get("/api/camps").json()}
    assert client.patch(f"/api/camps/{camps['Suid']['id']}", json={"name": "NOORDKAMP"}).status_code == 409


def test_a_typed_location_snaps_to_the_spelling_in_settings(client):
    """The pickers offer chips, but the Other chip, an older build's outbox
    and the Admin form all still send text. Whatever is typed lands as the
    camp Settings knows, so "noordkamp" never becomes a second camp."""
    client.post("/api/camps", json={"name": "Noordkamp"})
    _add(client, "A1")
    r = client.post("/api/events", json={"tag": "A1", "kind": "movement",
                                         "event_date": RECENTLY.isoformat(), "location": "  NOORDKAMP "})
    assert r.json()["location"] == "Noordkamp"
    assert _move(client, ["A1"], "noordkamp")[0]["location"] == "Noordkamp"
    assert len(client.get("/api/camps").json()) == 1


def test_a_location_nobody_set_up_is_added_to_settings(client):
    """The Other chip: a worker at a gate is never blocked by a camp the
    office has not set up. It appears in Settings for the admin to tidy."""
    _add(client, "A1")
    _move(client, ["A1"], "Rivierkamp")
    camps = client.get("/api/camps").json()
    assert [(c["name"], c["alive_count"]) for c in camps] == [("Rivierkamp", 1)]


def test_an_archived_camp_comes_back_when_used_again(client):
    camp = client.post("/api/camps", json={"name": "Winterveld"}).json()
    client.patch(f"/api/camps/{camp['id']}", json={"archived": True})
    _add(client, "A1")
    _move(client, ["A1"], "winterveld")
    camps = client.get("/api/camps").json()
    assert [c["name"] for c in camps] == ["Winterveld"], "restored under its old row, not duplicated"
    # It is active again, so adding it by name in Settings is now a duplicate.
    assert client.post("/api/camps", json={"name": "WINTERVELD"}).status_code == 409


def test_a_camp_holding_animals_cannot_be_archived(client):
    camp = client.post("/api/camps", json={"name": "Bo-kamp"}).json()
    _add(client, "A1")
    _add(client, "A2")
    _move(client, ["A1", "A2"], "Bo-kamp")
    r = client.patch(f"/api/camps/{camp['id']}", json={"archived": True})
    assert r.status_code == 409 and "2 animals" in r.json()["detail"]
    # Sold animals do not count: the camp is about the live herd.
    client.post("/api/events", json={"tag": "A1", "kind": "sale", "event_date": RECENTLY.isoformat()})
    _move(client, ["A2"], "Elsewhere")
    assert client.patch(f"/api/camps/{camp['id']}", json={"archived": True}).status_code == 200


def test_a_new_animal_can_be_entered_straight_into_a_camp(client):
    """Closes the trap where a herd keyed in from Admin needed one hand-typed
    movement per head before Move camp could see any of it."""
    _add(client, "A1", camp="Bo-kamp")
    _add(client, "A2")
    events = client.get("/api/animals/A1/events").json()
    assert [(e["kind"], e["location"]) for e in events] == [("movement", "Bo-kamp")]
    assert client.get("/api/animals/A2/events").json() == []
    assert [c["name"] for c in client.get("/api/camps").json()] == ["Bo-kamp"]


def test_the_move_picker_shows_the_whole_farm(client):
    """Every camp in Settings order, empty ones included; a camp only the
    events know after them; the animals with no camp last, so they can be
    moved INTO one."""
    client.post("/api/camps", json={"name": "Onder-kamp"})
    client.post("/api/camps", json={"name": "Bo-kamp"})
    _add(client, "B10"); _add(client, "B2"); _add(client, "C1"); _add(client, "D1")
    _move(client, ["B10", "B2"], "bo-kamp")
    _move(client, ["C1"], "Stray")   # auto-added to Settings, so it lists in order
    rows = [(r["location"], [a["tag"] for a in r["animals"]]) for r in client.get("/api/locations").json()]
    assert rows == [("Onder-kamp", []), ("Bo-kamp", ["B2", "B10"]), ("Stray", ["C1"]), (None, ["D1"])]


def test_a_camp_the_events_know_but_settings_does_not_still_lists(client):
    """Records older than the camp list, or a location typed before the
    seed ran, must not drop out of the picker."""
    _add(client, "A1")
    _move(client, ["A1"], "Old place")
    # Remove it from Settings behind the app's back: the case of a location
    # that predates the table, which the migration seeds but a stray edit
    # could still leave unlisted.
    camp = client.get("/api/camps").json()[0]
    assert client.patch(f"/api/camps/{camp['id']}", json={"archived": True}).status_code == 409
    rows = client.get("/api/locations").json()
    assert rows == [{"location": "Old place", "animals": [{"tag": "A1", "name": ""}]}]


def test_the_migration_seeds_camps_from_the_history(tmp_path):
    """A farm that has been moving cattle for a year finds its camps waiting
    in Settings, one per name ignoring case, keeping the first spelling."""
    from alembic import command

    path = tmp_path / "farm.db"
    engine = create_engine(f"sqlite:///{path}")
    cfg = migrate._config(engine)
    command.upgrade(cfg, "c1d84f2a7e13")   # the revision before camps
    con = sqlite3.connect(path)
    con.execute("INSERT INTO animal (tag, name, breed, sex, status, created_at, updated_at) "
                "VALUES ('A1', '', '', 'female', 'alive', '2026-01-01', '2026-01-01')")
    for n, (kind, location) in enumerate([("movement", "Noordkamp"), ("movement", "noordkamp"),
                                          ("birth", " Kraal "), ("weight", "Scale shed"),
                                          ("movement", "")]):
        con.execute("INSERT INTO event (animal_id, kind, event_date, note, location, created_at) "
                    f"VALUES (1, '{kind}', '2026-01-0{n + 2}', '', '{location}', '2026-01-02')")
    con.commit()
    con.close()

    migrate.run_migrations(engine, snapshot=False)
    con = sqlite3.connect(path)
    try:
        assert con.execute("SELECT name, sort_order, archived FROM camp ORDER BY sort_order").fetchall() == [
            ("Kraal", 0, 0), ("Noordkamp", 1, 0)]
        # The events themselves are untouched.
        assert con.execute("SELECT location FROM event WHERE id = 2").fetchone() == ("noordkamp",)
    finally:
        con.close()
