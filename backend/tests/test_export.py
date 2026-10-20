"""CSV export: the herd and its history as files anyone can open."""
import csv
import io

from tests.conftest import RECENTLY


def _rows(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    text = response.text
    assert text.startswith("\ufeff"), "Excel needs the byte-order mark to read UTF-8"
    return list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))


def test_animals_csv_carries_the_record_and_its_context(herd):
    herd.post("/api/events", json={"tag": "A1", "kind": "movement",
                                   "event_date": RECENTLY.isoformat(), "location": "Bo-kamp"})
    rows = {r["tag"]: r for r in _rows(herd.get("/api/export/animals.csv"))}
    assert set(rows) == {"A1", "B2", "C3"}
    a1 = rows["A1"]
    assert (a1["name"], a1["breed"], a1["sex"], a1["status"], a1["camp"]) == ("Bessie", "Nguni", "female", "alive", "Bo-kamp")
    assert (a1["last_weight_kg"], a1["class"]) == ("310.0", "heifer")
    assert rows["C3"]["status"] == "sold" and rows["C3"]["camp"] == ""
    assert rows["B2"]["last_weight_kg"] == "640.0"


def test_events_csv_names_the_animal_on_every_row_oldest_first(herd):
    rows = _rows(herd.get("/api/export/events.csv"))
    assert [(r["tag"], r["kind"]) for r in rows] == [("A1", "weight"), ("B2", "weight"), ("C3", "sale")]
    assert rows[0]["weight_kg"] == "310.0" and rows[2]["note"] == "Vleissentraal"
    assert set(rows[0]) >= {"camp", "product", "dose", "withdrawal_days", "temp_min_c", "rain_mm", "recorded_at"}


def test_an_empty_herd_exports_a_header_only(client):
    assert _rows(client.get("/api/export/animals.csv")) == []
    assert _rows(client.get("/api/export/events.csv")) == []


def test_the_dashboard_counts_the_herd_by_camp(client):
    client.post("/api/camps", json={"name": "Onder"})
    for tag in ["A1", "A2", "A3"]:
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
    client.post("/api/events/movement/bulk", json={"tags": ["A1", "A2"], "event_date": RECENTLY.isoformat(), "location": "Bo"})
    counts = client.get("/api/dashboard").json()["camp_counts"]
    assert counts == [{"camp": "Onder", "alive": 0}, {"camp": "Bo", "alive": 2}, {"camp": None, "alive": 1}]
