"""Treating a whole group at once."""
from datetime import date, timedelta

from tests.conftest import RECENTLY

TODAY = date.today()


def _herd(client, tags):
    for tag in tags:
        assert client.post("/api/animals", json={"tag": tag, "sex": "female"}).status_code == 200


def test_a_group_treatment_lands_on_every_animal(client):
    _herd(client, ["A1", "A2", "A3"])
    r = client.post("/api/events/treatment/bulk", json={
        "tags": ["A1", "A2"], "event_date": TODAY.isoformat(),
        "product": " Ivermectin ", "dose": "5 ml", "withdrawal_days": 28, "note": "dosing day"})
    assert r.status_code == 200, r.text
    assert [(e["kind"], e["product"], e["dose"], e["withdrawal_days"]) for e in r.json()] == \
        [("treatment", "Ivermectin", "5 ml", 28)] * 2
    until = {a["tag"]: a["withdrawal_until"] for a in client.get("/api/animals").json()}
    assert until == {"A1": (TODAY + timedelta(days=28)).isoformat(),
                     "A2": (TODAY + timedelta(days=28)).isoformat(), "A3": None}
    assert client.get("/api/products").json() == [{"product": "Ivermectin", "dose": "5 ml", "withdrawal_days": 28}]


def test_a_replayed_group_treatment_records_once(client):
    _herd(client, ["A1", "A2"])
    body = {"tags": ["A1", "A2"], "event_date": RECENTLY.isoformat(), "product": "X",
            "withdrawal_days": 7, "client_uuid": "batch-1"}
    assert client.post("/api/events/treatment/bulk", json=body).status_code == 200
    assert client.post("/api/events/treatment/bulk", json=body).status_code == 200
    assert len(client.get("/api/events?kind=treatment").json()) == 2


def test_an_unknown_tag_fails_the_whole_group(client):
    _herd(client, ["A1"])
    r = client.post("/api/events/treatment/bulk", json={"tags": ["A1", "nobody"], "event_date": TODAY.isoformat()})
    assert r.status_code == 404
    assert client.get("/api/animals/A1/events").json() == []
    assert client.post("/api/events/treatment/bulk", json={"tags": [], "event_date": TODAY.isoformat()}).status_code == 400
    assert client.post("/api/events/treatment/bulk", json={"tags": ["A1"], "event_date": TODAY.isoformat(),
                                                           "withdrawal_days": -1}).status_code == 422
