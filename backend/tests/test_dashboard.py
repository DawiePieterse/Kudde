"""The dashboard: the herd at a glance, and the animals falling through the
cracks."""
from datetime import date, timedelta

from tests.conftest import LONG_AGO, RECENTLY

from routers.dashboard import _STALE_WEIGHT_DAYS


def test_an_empty_herd_still_answers(client):
    """A farm on its first morning: every figure has to be a zero, not a
    missing key the admin screen then renders as "undefined"."""
    body = client.get("/api/dashboard").json()
    assert body["herd_counts"] == {"alive": 0, "dead": 0, "sold": 0}
    assert body["total_alive"] == 0
    assert body["recent_events"] == [] and body["needs_weighing"] == []


def test_herd_counts_cover_every_status(herd):
    body = herd.get("/api/dashboard").json()
    # Every status is present even at zero - the counts come out of a GROUP BY
    # that only returns the statuses actually in use.
    assert body["herd_counts"] == {"alive": 2, "dead": 0, "sold": 1}
    assert body["total_alive"] == 2


def test_recent_events_name_the_animal(herd):
    """Joined to the animal rather than returned bare: an activity feed that
    says "sale - 9/6/2026" without naming the animal is close to useless, and
    the alternative is the admin app making a lookup request per row."""
    events = herd.get("/api/dashboard").json()["recent_events"]
    assert events, "the herd fixture recorded three events"
    assert all("tag" in e and "name" in e for e in events)
    sale = next(e for e in events if e["kind"] == "sale")
    assert sale["tag"] == "C3"
    dates = [e["event_date"] for e in events]
    assert dates == sorted(dates, reverse=True)


def test_needs_weighing_finds_the_overdue_and_the_never_weighed(client):
    """The three cases this list exists to separate: weighed recently (not
    listed), weighed long ago (listed), never weighed at all (listed)."""
    for tag in ("A1", "B2", "C3"):
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
    client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                     "event_date": RECENTLY.isoformat(), "value": 300})
    client.post("/api/events", json={"tag": "B2", "kind": "weight",
                                     "event_date": LONG_AGO.isoformat(), "value": 280})
    # C3 has never been weighed.

    overdue = {a["tag"] for a in client.get("/api/dashboard").json()["needs_weighing"]}
    assert overdue == {"B2", "C3"}


def test_the_weighing_cutoff_compares_dates_as_dates(client):
    """The comparison is `max(event_date) < cutoff`, and max() over a Date
    column is exactly the kind of expression that comes back as a string if
    the type does not travel with it - at which point every animal weighed
    this century is compared as text and the list is silently wrong. One day
    either side of the cutoff is the only way to see that from outside."""
    client.post("/api/animals", json={"tag": "IN", "sex": "female"})
    client.post("/api/animals", json={"tag": "OUT", "sex": "female"})
    just_inside = date.today() - timedelta(days=_STALE_WEIGHT_DAYS - 1)
    just_outside = date.today() - timedelta(days=_STALE_WEIGHT_DAYS + 1)
    client.post("/api/events", json={"tag": "IN", "kind": "weight",
                                     "event_date": just_inside.isoformat(), "value": 300})
    client.post("/api/events", json={"tag": "OUT", "kind": "weight",
                                     "event_date": just_outside.isoformat(), "value": 300})

    overdue = {a["tag"] for a in client.get("/api/dashboard").json()["needs_weighing"]}
    assert overdue == {"OUT"}


def test_only_the_most_recent_weighing_counts(client):
    """An animal weighed years ago AND last week is not overdue. The query
    takes max(event_date) per animal, so an older row must not drag it back
    onto the list."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                     "event_date": LONG_AGO.isoformat(), "value": 250})
    client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                     "event_date": RECENTLY.isoformat(), "value": 310})
    assert client.get("/api/dashboard").json()["needs_weighing"] == []


def test_a_sold_or_dead_animal_is_never_asked_for_a_weight(client):
    """Only living animals can be weighed, so listing one that left the herd
    is an errand nobody can run."""
    client.post("/api/animals", json={"tag": "C3", "sex": "female"})
    assert {a["tag"] for a in client.get("/api/dashboard").json()["needs_weighing"]} == {"C3"}
    client.post("/api/events", json={"tag": "C3", "kind": "sale",
                                     "event_date": RECENTLY.isoformat()})
    assert client.get("/api/dashboard").json()["needs_weighing"] == []


def test_a_non_weight_event_does_not_count_as_a_weighing(client):
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                     "event_date": RECENTLY.isoformat()})
    assert {a["tag"] for a in client.get("/api/dashboard").json()["needs_weighing"]} == {"A1"}
