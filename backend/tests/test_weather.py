"""Stamping each event with the weather it happened in.

Nothing here reaches the network: the lookup is stubbed at the seam
farmweather.py uses (the one place every event, from either router, gets
its weather), and weather.py's own tests stub httpx instead. A farm
server is behind Tailscale and often has no internet at all, which is the
same reason fetch_daily_weather never raises.
"""
from datetime import date, timedelta

import pytest

import farmweather
import weather
from tests.conftest import LONG_AGO, RECENTLY


@pytest.fixture()
def looked_up(monkeypatch):
    """Records every weather lookup and answers with a fixed day."""
    calls = []

    def fake(lat, lng, on):
        calls.append((lat, lng, on))
        return weather.DailyWeather(temp_max=31.2, temp_min=12.0, precipitation=4.5)

    monkeypatch.setattr(farmweather, "fetch_daily_weather", fake)
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
    monkeypatch.setattr(farmweather, "fetch_daily_weather", lambda *a: None)
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


# --- a whole camp moving at once --------------------------------------------

def test_a_camp_move_carries_the_weather_too(client, looked_up):
    """A move recorded one animal at a time carried weather; the same move
    recorded as a camp did not, until both went through one seam."""
    _at(client)
    for tag in ("A1", "A2", "A3"):
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
    r = client.post("/api/events/movement/bulk",
                    json={"tags": ["A1", "A2", "A3"], "event_date": RECENTLY.isoformat(),
                          "location": "Onder-kamp", "note": "after the rain"})
    assert r.status_code == 200, r.text
    assert [e["weather_precipitation"] for e in r.json()] == [4.5, 4.5, 4.5]
    # One lookup for the camp, not one per head of cattle.
    assert looked_up == [(-33.9249, 18.4241, RECENTLY)]


def test_a_replayed_camp_move_is_not_looked_up_again(client, looked_up):
    _at(client)
    for tag in ("A1", "A2"):
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
    body = {"tags": ["A1", "A2"], "event_date": RECENTLY.isoformat(),
            "location": "Onder-kamp", "client_uuid": "batch-1"}
    first = client.post("/api/events/movement/bulk", json=body)
    again = client.post("/api/events/movement/bulk", json=body)
    assert [e["id"] for e in again.json()] == [e["id"] for e in first.json()]
    assert len(looked_up) == 1


def test_a_camp_move_with_no_farm_position_still_moves_the_camp(client, looked_up):
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    r = client.post("/api/events/movement/bulk",
                    json={"tags": ["A1"], "event_date": RECENTLY.isoformat(),
                          "location": "Onder-kamp"})
    assert r.status_code == 200, r.text
    assert r.json()[0]["weather_temp_max"] is None
    assert looked_up == []


# --- one answer per day, not one per animal ----------------------------------

def test_a_days_weather_is_looked_up_once_for_the_whole_herd(client, looked_up):
    """A weigh session is forty head on one day, and a calving records two
    or three events on one date. Every one of them used to go out to
    Open-Meteo inside the request the phone was waiting on."""
    _at(client)
    for tag in ("A1", "A2", "A3"):
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
    for tag in ("A1", "A2", "A3"):
        r = client.post("/api/events", json={"tag": tag, "kind": "weight",
                                             "event_date": RECENTLY.isoformat(), "value": 300})
        assert r.json()["weather_temp_max"] == 31.2
    r = client.post("/api/calvings", json={"dam_tag": "A1", "event_date": RECENTLY.isoformat(),
                                           "calf": {"tag": "K1", "sex": "male"}, "camp": "Bo-kamp"})
    assert r.status_code == 200, r.text
    assert r.json()["dam_event"]["weather_temp_max"] == 31.2
    assert looked_up == [(-33.9249, 18.4241, RECENTLY)]
    # A different day, or a moved farm, is a different question.
    client.post("/api/events", json={"tag": "A2", "kind": "treatment", "event_date": LONG_AGO.isoformat()})
    _at(client, lat=-25.0)
    client.post("/api/events", json={"tag": "A2", "kind": "treatment", "event_date": RECENTLY.isoformat()})
    assert looked_up == [(-33.9249, 18.4241, RECENTLY), (-33.9249, 18.4241, LONG_AGO), (-25.0, 18.4241, RECENTLY)]


def test_a_failed_lookup_is_not_retried_on_every_save(client, monkeypatch):
    """With no internet at the farm each lookup waits out its whole timeout,
    so a dead connection cost one slow save per animal. A miss is remembered
    briefly, then asked again - the connection may be back."""
    calls = []
    answer = {"weather": None}

    def fake(lat, lng, on):
        calls.append(on)
        return answer["weather"]

    monkeypatch.setattr(farmweather, "fetch_daily_weather", fake)
    clock = {"now": 1000.0}
    monkeypatch.setattr(farmweather.time, "monotonic", lambda: clock["now"])
    _at(client)
    for tag in ("A1", "A2"):
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
        r = client.post("/api/events", json={"tag": tag, "kind": "treatment", "event_date": RECENTLY.isoformat()})
        assert r.status_code == 200 and r.json()["weather_temp_max"] is None
    assert len(calls) == 1
    # Once the miss has aged out, the next save asks again and gets the answer.
    clock["now"] += farmweather.KEEP_MISS_S + 1
    answer["weather"] = weather.DailyWeather(temp_max=31.2, temp_min=12.0, precipitation=4.5)
    r = client.post("/api/events", json={"tag": "A1", "kind": "treatment", "event_date": RECENTLY.isoformat()})
    assert r.json()["weather_temp_max"] == 31.2 and len(calls) == 2


def test_the_lookup_gives_up_before_the_phone_does():
    """The lookup runs inside the request that records the event, and the
    field app gives up on a request after 8 seconds and queues it. A longer
    timeout here made every save on a farm with no internet look like an
    offline save - while the server had in fact recorded it."""
    assert weather._TIMEOUT < 8


def test_a_farm_position_off_the_globe_is_refused(client):
    """Nothing downstream catches it: Open-Meteo answers with an error that
    weather.py swallows, so every event from then on simply had no weather."""
    for lat, lng in ((338.9, 18.4), (-33.9, 181.0), (-91.0, 18.4)):
        r = client.put("/api/farm", json={"gps_lat": lat, "gps_lng": lng})
        assert r.status_code == 422, (lat, lng)
    assert client.get("/api/farm").json()["gps_lat"] is None
    assert client.put("/api/farm", json={"gps_lat": -33.9, "gps_lng": 18.4}).status_code == 200
