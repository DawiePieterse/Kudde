"""Stamping each event with the weather it happened in.

Nothing here reaches the network: the lookup is stubbed at the seam
routers.events uses, and weather.py's own tests stub httpx instead. A farm
server is behind Tailscale and often has no internet at all, which is the
same reason fetch_daily_weather never raises.
"""
from datetime import date, timedelta

import pytest

import routers.events as events_router
import weather
from tests.conftest import RECENTLY


@pytest.fixture()
def looked_up(monkeypatch):
    """Records every weather lookup and answers with a fixed day."""
    calls = []

    def fake(lat, lng, on):
        calls.append((lat, lng, on))
        return weather.DailyWeather(temp_max=31.2, temp_min=12.0, precipitation=4.5)

    monkeypatch.setattr(events_router, "fetch_daily_weather", fake)
    return calls


def _at(client, lat=-33.9249, lng=18.4241):
    client.put("/api/farm", json={"gps_lat": lat, "gps_lng": lng})


def test_an_event_carries_the_weather_of_the_day_it_happened(client, looked_up):
    _at(client)
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                         "event_date": RECENTLY.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["weather_temp_max"] == 31.2
    assert r.json()["weather_temp_min"] == 12.0
    assert r.json()["weather_precipitation"] == 4.5
    # Looked up for the farm's position, on the event's own date - not today's.
    assert looked_up == [(-33.9249, 18.4241, RECENTLY)]


def test_no_farm_position_means_no_lookup_and_no_weather(client, looked_up):
    """The farm row exists from the moment Settings is opened, with its GPS
    fields still blank - which must not be read as a position at 0,0 in the
    Gulf of Guinea."""
    client.get("/api/farm")
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                         "event_date": RECENTLY.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["weather_temp_max"] is None
    assert looked_up == []


def test_a_failed_lookup_still_records_the_event(client, monkeypatch):
    """Weather is a nicety; the event is the record. Open-Meteo being down,
    or the farm having no internet, must never cost a treatment."""
    monkeypatch.setattr(events_router, "fetch_daily_weather", lambda *a: None)
    _at(client)
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                         "event_date": RECENTLY.isoformat(), "value": 310})
    assert r.status_code == 200, r.text
    assert r.json()["value"] == 310 and r.json()["weather_temp_max"] is None


def test_a_replayed_event_is_not_looked_up_again(client, looked_up):
    """The outbox replays an event that may already have landed. The
    client_uuid check answers from the database before anything here goes
    near the network."""
    _at(client)
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    body = {"tag": "A1", "kind": "treatment", "event_date": RECENTLY.isoformat(),
            "client_uuid": "e-1"}
    first = client.post("/api/events", json=body)
    again = client.post("/api/events", json=body)
    assert again.json()["id"] == first.json()["id"]
    assert len(looked_up) == 1


# --- the lookup itself ------------------------------------------------------

class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _stub_httpx(monkeypatch, payload=None, boom=None):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = params
        if boom is not None:
            raise boom
        return _Response(payload)

    monkeypatch.setattr(weather.httpx, "get", fake_get)
    return seen


_DAY = {"daily": {"temperature_2m_max": [31.2], "temperature_2m_min": [12.0],
                  "precipitation_sum": [4.5]}}


def test_a_recent_date_uses_the_forecast_api(monkeypatch):
    seen = _stub_httpx(monkeypatch, _DAY)
    got = weather.fetch_daily_weather(-33.9, 18.4, date.today() - timedelta(days=3))
    assert got == weather.DailyWeather(31.2, 12.0, 4.5)
    assert seen["url"] == weather._FORECAST_URL


def test_an_old_date_uses_the_archive_api(monkeypatch):
    """The forecast API only reaches ~92 days back; older days live in the
    archive, and asking the wrong one gets nothing."""
    seen = _stub_httpx(monkeypatch, _DAY)
    weather.fetch_daily_weather(-33.9, 18.4, date.today() - timedelta(days=200))
    assert seen["url"] == weather._ARCHIVE_URL


def test_a_failure_is_swallowed_rather_than_raised(monkeypatch):
    _stub_httpx(monkeypatch, boom=OSError("no route to host"))
    assert weather.fetch_daily_weather(-33.9, 18.4, date.today()) is None


def test_an_answer_without_the_daily_block_is_not_a_crash(monkeypatch):
    _stub_httpx(monkeypatch, {"error": True, "reason": "out of range"})
    assert weather.fetch_daily_weather(-33.9, 18.4, date.today()) is None
