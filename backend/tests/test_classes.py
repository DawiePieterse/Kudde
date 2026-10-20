"""Cow, heifer, bull, steer, calf: derived from the record, never typed."""
from datetime import date, timedelta

TODAY = date.today()


from tests.conftest import add_animal as _add


def _classes(client):
    return {a["tag"]: a["class"] for a in client.get("/api/animals").json()}


def test_sex_age_and_castration_give_the_class(client):
    young = (TODAY - timedelta(days=100)).isoformat()
    grown = (TODAY - timedelta(days=800)).isoformat()
    _add(client, "HC", sex="female", birth_date=young)
    _add(client, "H", sex="female", birth_date=grown)
    _add(client, "H2", sex="female")                       # no birth date: grown
    _add(client, "BC", sex="male", birth_date=young)
    _add(client, "B", sex="male", birth_date=grown)
    _add(client, "SC", sex="male", birth_date=young, castrated=True)
    _add(client, "S", sex="male", birth_date=grown, castrated=True)
    assert _classes(client) == {"HC": "heifer calf", "H": "heifer", "H2": "heifer",
                                "BC": "bull calf", "B": "bull", "SC": "steer calf", "S": "steer"}


def test_a_heifer_becomes_a_cow_when_she_calves(client):
    grown = (TODAY - timedelta(days=1000)).isoformat()
    _add(client, "C1", birth_date=grown)
    assert _classes(client)["C1"] == "heifer"
    r = client.post("/api/calvings", json={"dam_tag": "C1", "event_date": TODAY.isoformat(),
                                           "calf": {"tag": "C1-26", "sex": "female"}})
    assert r.status_code == 200, r.text
    assert _classes(client) == {"C1": "cow", "C1-26": "heifer calf"}


def test_a_calf_recorded_by_dam_tag_alone_makes_her_a_cow(client):
    """A bought-in cow entered with her calf: no calving event, but the
    calf's dam tag says it all."""
    _add(client, "C1")
    _add(client, "K1", birth_date=(TODAY - timedelta(days=30)).isoformat(), dam_tag="c1")
    assert _classes(client)["C1"] == "cow"


def test_her_own_birth_event_does_not_make_her_a_cow(client):
    """The calving screen gives the calf a birth event of its own, dated at
    its birth. That is its birth, not a calving."""
    born = TODAY - timedelta(days=500)
    _add(client, "K1", birth_date=born.isoformat())
    client.post("/api/events", json={"tag": "K1", "kind": "birth", "event_date": born.isoformat()})
    assert _classes(client)["K1"] == "heifer"
    # A birth event well after her own birth is a calving (the old manual
    # way of recording one on a cow).
    client.post("/api/events", json={"tag": "K1", "kind": "birth",
                                     "event_date": (born + timedelta(days=400)).isoformat()})
    assert _classes(client)["K1"] == "cow"


def test_castrated_can_be_set_later(client):
    _add(client, "B1", sex="male")
    assert _classes(client)["B1"] == "bull"
    assert client.patch("/api/animals/B1", json={"castrated": True}).status_code == 200
    assert _classes(client)["B1"] == "steer"


def test_the_dashboard_counts_the_live_herd_by_class(client):
    grown = (TODAY - timedelta(days=800)).isoformat()
    _add(client, "H1", birth_date=grown); _add(client, "H2", birth_date=grown)
    _add(client, "B1", sex="male", birth_date=grown)
    _add(client, "S1", sex="male", birth_date=grown, castrated=True)
    client.post("/api/events", json={"tag": "S1", "kind": "sale", "event_date": TODAY.isoformat()})
    counts = client.get("/api/dashboard").json()["class_counts"]
    assert counts == {"cow": 0, "heifer": 2, "bull": 1, "steer": 0,
                      "heifer calf": 0, "bull calf": 0, "steer calf": 0}
