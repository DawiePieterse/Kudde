"""Treatments: what was given, and the withdrawal period that follows."""
from datetime import date, timedelta

from tests.conftest import RECENTLY, add_animal as _add

TODAY = date.today()


def _treat(client, tag, on, **fields):
    r = client.post("/api/events", json={"tag": tag, "kind": "treatment", "event_date": on.isoformat(), **fields})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_treatment_records_product_dose_and_withdrawal(client):
    _add(client, "A1")
    e = _treat(client, "A1", RECENTLY, product="  Ivermectin ", dose="5 ml", withdrawal_days=28, note="ticks")
    assert (e["product"], e["dose"], e["withdrawal_days"], e["note"]) == ("Ivermectin", "5 ml", 28, "ticks")
    # Still optional: a treatment that is only a note is still a treatment.
    e = _treat(client, "A1", RECENTLY, note="dipped")
    assert (e["product"], e["dose"], e["withdrawal_days"]) == ("", "", None)
    r = client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                         "event_date": RECENTLY.isoformat(), "withdrawal_days": -1})
    assert r.status_code == 422


def test_treatment_fields_are_dropped_from_other_kinds(client):
    """A form that carried a product across a kind change must not fail the
    save, and must not record a withdrawal on a weighing either."""
    _add(client, "A1")
    r = client.post("/api/events", json={"tag": "A1", "kind": "weight", "event_date": RECENTLY.isoformat(),
                                         "value": 300, "product": "Ivermectin", "withdrawal_days": 28})
    assert r.status_code == 200
    assert (r.json()["product"], r.json()["withdrawal_days"]) == ("", None)
    assert client.get("/api/animals").json()[0]["withdrawal_until"] is None


def test_the_list_says_until_when_an_animal_is_in_withdrawal(client):
    _add(client, "A1"); _add(client, "A2"); _add(client, "A3")
    _treat(client, "A1", TODAY - timedelta(days=10), product="X", withdrawal_days=28)   # 18 days to go
    _treat(client, "A2", TODAY - timedelta(days=40), product="X", withdrawal_days=28)   # clear
    _treat(client, "A3", TODAY, product="Y", withdrawal_days=0)                         # clear today, so flagged today
    rows = {a["tag"]: a["withdrawal_until"] for a in client.get("/api/animals").json()}
    assert rows == {"A1": (TODAY + timedelta(days=18)).isoformat(), "A2": None, "A3": TODAY.isoformat()}


def test_the_longest_withdrawal_wins(client):
    _add(client, "A1")
    _treat(client, "A1", TODAY - timedelta(days=1), product="Long", withdrawal_days=60)
    _treat(client, "A1", TODAY, product="Short", withdrawal_days=7)
    assert client.get("/api/animals").json()[0]["withdrawal_until"] == (TODAY + timedelta(days=59)).isoformat()


def test_the_dashboard_lists_who_is_in_withdrawal_soonest_clear_first(client):
    _add(client, "B10"); _add(client, "B2"); _add(client, "C1")
    _treat(client, "B10", TODAY, product="X", withdrawal_days=5)
    _treat(client, "B2", TODAY, product="X", withdrawal_days=5)
    _treat(client, "C1", TODAY, product="Y", withdrawal_days=2)
    # A sold animal is not on the list: it has already gone.
    _add(client, "D1")
    _treat(client, "D1", TODAY, product="Y", withdrawal_days=30)
    client.post("/api/events", json={"tag": "D1", "kind": "sale", "event_date": TODAY.isoformat()})
    rows = [(r["tag"], r["until"]) for r in client.get("/api/dashboard").json()["in_withdrawal"]]
    five = (TODAY + timedelta(days=5)).isoformat()
    assert rows == [("C1", (TODAY + timedelta(days=2)).isoformat()), ("B2", five), ("B10", five)]


def test_products_are_the_bottles_used_before_most_recent_first(client):
    _add(client, "A1")
    _treat(client, "A1", TODAY - timedelta(days=30), product="Ivermectin", dose="5 ml", withdrawal_days=28)
    _treat(client, "A1", TODAY - timedelta(days=20), product="Terramycin", dose="10 ml", withdrawal_days=21)
    _treat(client, "A1", TODAY - timedelta(days=10), product="ivermectin", dose="6 ml", withdrawal_days=28)
    _treat(client, "A1", TODAY, note="dipped")   # no product: not a bottle
    products = client.get("/api/products").json()
    # One entry per product ignoring case, carrying what it was LAST given as.
    assert products == [
        {"product": "ivermectin", "dose": "6 ml", "withdrawal_days": 28},
        {"product": "Terramycin", "dose": "10 ml", "withdrawal_days": 21},
    ]
