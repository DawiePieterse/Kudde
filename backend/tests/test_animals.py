"""The animal register: identity, creation, and correction."""
from tests.conftest import RECENTLY


def test_a_new_herd_is_empty_not_an_error(client):
    r = client.get("/api/animals")
    assert r.status_code == 200 and r.json() == []


def test_create_and_read_back(client):
    r = client.post("/api/animals", json={"tag": "A1", "name": "Bessie", "breed": "Nguni",
                                          "sex": "female", "birth_date": "2022-03-15",
                                          "sire_tag": "S9", "dam_tag": "D4"})
    assert r.status_code == 200, r.text
    body = r.json()
    # birth_date is the one that used to break: a table=True SQLModel does not
    # coerce "2022-03-15" to a date, and SQLite's Date column rejects the str
    # at insert time. See the AnimalCreate comment in routers/animals.py.
    assert body["birth_date"] == "2022-03-15"
    assert body["status"] == "alive"     # a new animal is alive by default
    assert body["id"] is not None

    assert client.get("/api/animals/A1").json()["name"] == "Bessie"


def test_a_tag_can_only_be_used_once(client):
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.post("/api/animals", json={"tag": "A1", "sex": "male"})
    assert r.status_code == 409
    assert "already in use" in r.json()["detail"]


def test_a_tag_is_required_and_cannot_be_blank(client):
    """The tag is the identity key: every event is recorded against it and
    it is the only thing a farmer can read off the animal. A blank one used
    to be accepted, producing an animal nothing could refer to."""
    for blank in ("", "   ", "\t"):
        r = client.post("/api/animals", json={"tag": blank, "sex": "female"})
        assert r.status_code == 422, f"{blank!r} was accepted"
    assert client.get("/api/animals").json() == []


def test_a_padded_tag_is_the_same_tag(client):
    """" A1 " used to land beside "A1" as a second animal - identical on
    every screen, impossible to tell apart, and quietly splitting one
    animal's history in two."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.post("/api/animals", json={"tag": " A1 ", "sex": "male"})
    assert r.status_code == 409, "a padded tag was accepted as a second animal"

    client.post("/api/animals", json={"tag": "  B2  ", "sex": "male"})
    assert {a["tag"] for a in client.get("/api/animals").json()} == {"A1", "B2"}

    # And an event captured against the padded form finds the same animal.
    r = client.post("/api/events", json={"tag": " B2 ", "kind": "treatment",
                                         "event_date": RECENTLY.isoformat()})
    assert r.status_code == 200, r.text


def test_parent_tags_are_tidied_too(client):
    """Parentage is free text rather than a foreign key (the sire may have
    been sold, or never recorded here at all), so nothing downstream would
    ever catch a stray space."""
    r = client.post("/api/animals", json={"tag": "A1", "sex": "female",
                                          "sire_tag": " S9 ", "dam_tag": "   "})
    assert r.json()["sire_tag"] == "S9"
    assert r.json()["dam_tag"] is None


def test_unknown_sex_is_refused(client):
    r = client.post("/api/animals", json={"tag": "A1", "sex": "steer"})
    assert r.status_code == 422


def test_missing_animal_is_a_404_not_a_crash(client):
    assert client.get("/api/animals/NOPE").status_code == 404
    assert client.patch("/api/animals/NOPE", json={"name": "x"}).status_code == 404
    assert client.get("/api/animals/NOPE/events").status_code == 404


def test_patch_only_touches_what_it_is_sent(herd):
    """The whole point of PATCH here: a field-app edit must not clobber an
    admin edit made around the same time."""
    before = herd.get("/api/animals/A1").json()
    r = herd.patch("/api/animals/A1", json={"name": "Bessie II"})
    assert r.status_code == 200
    after = r.json()
    assert after["name"] == "Bessie II"
    assert after["breed"] == before["breed"] == "Nguni"
    assert after["birth_date"] == before["birth_date"]
    assert after["updated_at"] > before["updated_at"]


def test_patch_cannot_rename_a_tag(client):
    """The tag is the identity key and AnimalUpdate has no field for it, so
    a client sending one is ignored rather than obeyed."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.patch("/api/animals/A1", json={"tag": "ZZ", "name": "renamed"})
    assert r.status_code == 200
    assert r.json()["tag"] == "A1"
    assert client.get("/api/animals/ZZ").status_code == 404


def test_status_filter_and_search(herd):
    alive = {a["tag"] for a in herd.get("/api/animals?status=alive").json()}
    assert alive == {"A1", "B2"}
    assert {a["tag"] for a in herd.get("/api/animals?status=sold").json()} == {"C3"}
    assert herd.get("/api/animals?status=zombie").status_code == 422

    # q matches the START of a tag, or anywhere in a name - the two things a
    # farmer has in hand when looking for an animal.
    assert {a["tag"] for a in herd.get("/api/animals?q=a").json()} == {"A1"}
    assert {a["tag"] for a in herd.get("/api/animals?q=BESS").json()} == {"A1"}
    assert herd.get("/api/animals?q=zzz").json() == []


def test_animals_are_listed_by_tag(herd):
    tags = [a["tag"] for a in herd.get("/api/animals").json()]
    assert tags == sorted(tags)
