"""A calving: the cow's birth event and the new calf, as one record."""
from tests.conftest import RECENTLY, add_animal


def _cow(client, tag="C1", **extra):
    add_animal(client, tag, breed="Nguni", **extra)


def _calve(client, **override):
    body = {"dam_tag": "C1", "event_date": RECENTLY.isoformat(),
            "calf": {"tag": "C1-24", "sex": "male", "breed": "Nguni"},
            "camp": "Bo-kamp", "note": "pulled, up and sucking", "client_uuid": "cap-1"}
    body.update(override)
    return client.post("/api/calvings", json=body)


def test_a_calving_records_the_cow_and_creates_the_calf(client):
    _cow(client)
    r = _calve(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["replayed"] is False
    assert (body["calf"]["tag"], body["calf"]["dam_tag"], body["calf"]["birth_date"], body["calf"]["status"]) \
        == ("C1-24", "C1", RECENTLY.isoformat(), "alive")

    cow_events = client.get("/api/animals/C1/events").json()
    assert [(e["kind"], e["location"], e["note"]) for e in cow_events] == [
        ("birth", "Bo-kamp", "Calf C1-24 - pulled, up and sucking")]
    calf_events = client.get("/api/animals/C1-24/events").json()
    assert [(e["kind"], e["location"], e["note"]) for e in calf_events] == [("birth", "Bo-kamp", "Born to C1")]

    # The calf is in the camp from day one, and the camp is in Settings.
    rows = {a["tag"]: a["camp"] for a in client.get("/api/animals").json()}
    assert rows == {"C1": "Bo-kamp", "C1-24": "Bo-kamp"}
    assert [c["name"] for c in client.get("/api/camps").json()] == ["Bo-kamp"]


def test_a_replayed_calving_records_once(client):
    """The phone gives up after 8s and replays; the second arrival must not
    make a second calf or a second birth."""
    _cow(client)
    assert _calve(client).status_code == 200
    r = _calve(client)
    assert r.status_code == 200 and r.json()["replayed"] is True
    assert len(client.get("/api/animals").json()) == 2
    assert len(client.get("/api/animals/C1/events").json()) == 1


def test_a_taken_calf_tag_leaves_the_cow_untouched(client):
    _cow(client)
    _cow(client, tag="C1-24")
    r = _calve(client)
    assert r.status_code == 409 and "already in use" in r.json()["detail"]
    assert client.get("/api/animals/C1/events").json() == []


def test_only_a_cow_can_calve(client):
    client.post("/api/animals", json={"tag": "B1", "sex": "male"})
    assert _calve(client, dam_tag="B1").status_code == 422
    assert _calve(client, dam_tag="nobody").status_code == 404
    _cow(client)
    assert _calve(client, calf={"tag": "  ", "sex": "male"}).status_code == 422


def test_a_calving_without_a_camp_still_works(client):
    _cow(client)
    assert _calve(client, camp="", note="").status_code == 200
    assert client.get("/api/animals/C1/events").json()[0]["note"] == "Calf C1-24"
    assert {a["tag"]: a["camp"] for a in client.get("/api/animals").json()} == {"C1": None, "C1-24": None}
