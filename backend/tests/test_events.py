"""Recording what happens to an animal, and what that does to its status."""
from tests.conftest import LONG_AGO, RECENTLY


def test_event_is_recorded_against_the_tag_the_field_app_read(client):
    """The client sends a tag, not an animal_id - it knows the ear tag it
    just read, not this database's internal id."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    animal_id = client.get("/api/animals/A1").json()["id"]
    r = client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                         "event_date": RECENTLY.isoformat(), "value": 310.5})
    assert r.status_code == 200, r.text
    assert r.json()["animal_id"] == animal_id and r.json()["value"] == 310.5


def test_event_for_an_unknown_animal_is_refused(client):
    r = client.post("/api/events", json={"tag": "NOPE", "kind": "treatment",
                                         "event_date": RECENTLY.isoformat()})
    assert r.status_code == 404


def test_a_weight_event_must_carry_a_weight(client):
    """A blank weight event is worse than no event at all: the dashboard's
    "needs weighing" list is driven by the date of the most recent weight
    event, so an empty one silences the reminder for another six months
    while recording nothing. Both apps send `parseFloat(input) || null`, so
    an empty box, "abc" and a typed 0 all arrive here as null."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    for bad in (None, 0, -5):
        r = client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                             "event_date": RECENTLY.isoformat(), "value": bad})
        assert r.status_code == 422, f"a weight of {bad!r} was accepted"
    assert client.get("/api/animals/A1/events").json() == []

    # Every other kind is fine without one - only a weight event means a number.
    r = client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                         "event_date": RECENTLY.isoformat(), "note": "Dectomax"})
    assert r.status_code == 200


def test_death_and_sale_move_the_animal_out_of_the_herd(client):
    """A farmer capturing a death in the field shouldn't have to also
    remember to go and flip a separate status field."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    before = client.get("/api/animals/A1").json()
    assert before["status"] == "alive"

    client.post("/api/events", json={"tag": "A1", "kind": "death",
                                     "event_date": RECENTLY.isoformat()})
    after = client.get("/api/animals/A1").json()
    assert after["status"] == "dead"
    # The animal really did change, so its updated_at has to say so. It used
    # to be left alone, making a death or sale the one edit invisible to
    # anything comparing timestamps.
    assert after["updated_at"] > before["updated_at"]


def test_ordinary_events_leave_the_status_alone(client):
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    for kind in ("birth", "weight", "treatment", "movement"):
        body = {"tag": "A1", "kind": kind, "event_date": RECENTLY.isoformat()}
        if kind == "weight":
            body["value"] = 300
        client.post("/api/events", json=body)
    assert client.get("/api/animals/A1").json()["status"] == "alive"


def test_an_animals_history_is_newest_first_and_stable(client):
    """Event dates are days, not timestamps, so several events sharing one
    date is the normal case - weighed and treated at the same muster. Without
    an id tiebreaker SQLite is free to return them in a different order each
    time the screen is opened."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    same_day = RECENTLY.isoformat()
    client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                     "event_date": LONG_AGO.isoformat(), "value": 200})
    client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                     "event_date": same_day, "note": "first"})
    client.post("/api/events", json={"tag": "A1", "kind": "movement",
                                     "event_date": same_day, "location": "Camp 3"})

    events = client.get("/api/animals/A1/events").json()
    assert [e["event_date"] for e in events] == [same_day, same_day, LONG_AGO.isoformat()]
    assert [e["id"] for e in events] == sorted((e["id"] for e in events), reverse=True)
    assert client.get("/api/animals/A1/events").json() == events, "order is not stable"


def test_an_animals_history_is_only_its_own(herd):
    assert [e["kind"] for e in herd.get("/api/animals/C3/events").json()] == ["sale"]


def test_the_herd_wide_feed_is_newest_first(herd):
    events = herd.get("/api/events").json()
    dates = [e["event_date"] for e in events]
    assert dates == sorted(dates, reverse=True)
    assert len(events) == 3


def test_the_feed_can_be_filtered_by_kind(herd):
    weights = herd.get("/api/events?kind=weight").json()
    assert len(weights) == 2 and all(e["kind"] == "weight" for e in weights)
    assert herd.get("/api/events?kind=nonsense").status_code == 422


def test_the_feed_limit_is_bounded(herd):
    """SQLite reads a NEGATIVE limit as "no limit at all", so ?limit=-1 used
    to hand back the whole event history of the herd - and nothing capped
    the top end either."""
    assert len(herd.get("/api/events?limit=1").json()) == 1
    assert herd.get("/api/events?limit=-1").status_code == 422
    assert herd.get("/api/events?limit=0").status_code == 422
    assert herd.get("/api/events?limit=100000").status_code == 422


# --------------------------------------------------------------------------- #
# Recording an event twice
#
# The field app cannot tell a request that failed from one that succeeded
# slowly: it gives up after 8 seconds and puts the event back in its outbox.
# Nothing else about an event distinguishes that replay from a real second
# event - the same animal genuinely can be treated twice on one day - so the
# id the device captured it with is the only thing that can.
# --------------------------------------------------------------------------- #
def test_replaying_an_event_records_it_once(client):
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    body = {"tag": "A1", "kind": "weight", "event_date": RECENTLY.isoformat(),
            "value": 312.5, "client_uuid": "cafe-1234"}

    first = client.post("/api/events", json=body)
    second = client.post("/api/events", json=body)
    assert first.status_code == second.status_code == 200, second.text
    # The replay is answered with the event already on file, so the device
    # takes it off its outbox rather than retrying forever.
    assert first.json()["id"] == second.json()["id"]
    assert len(client.get("/api/animals/A1/events").json()) == 1


def test_two_genuine_events_on_one_day_are_both_kept(client):
    """The other half of the same rule: an animal really can be treated twice
    in a day, and deduplicating on anything but the capture id would lose the
    second one."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    for capture_id in ("cafe-1", "cafe-2"):
        r = client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                             "event_date": RECENTLY.isoformat(),
                                             "note": "Dectomax", "client_uuid": capture_id})
        assert r.status_code == 200
    assert len(client.get("/api/animals/A1/events").json()) == 2


def test_an_event_without_a_capture_id_is_still_accepted(client):
    """The admin app has no outbox and sends none. Several such events must
    not collide with each other on a unique index over NULLs."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    for _ in range(3):
        r = client.post("/api/events", json={"tag": "A1", "kind": "treatment",
                                             "event_date": RECENTLY.isoformat()})
        assert r.status_code == 200, r.text
    assert len(client.get("/api/animals/A1/events").json()) == 3


def test_a_replay_does_not_re_apply_the_status_change(client):
    """A sale replayed after the admin corrected the status back to alive
    must not silently sell the animal a second time."""
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    body = {"tag": "A1", "kind": "sale", "event_date": RECENTLY.isoformat(),
            "client_uuid": "cafe-sale"}
    client.post("/api/events", json=body)
    assert client.get("/api/animals/A1").json()["status"] == "sold"

    client.patch("/api/animals/A1", json={"status": "alive"})  # the admin corrects a mis-tap
    client.post("/api/events", json=body)                      # the outbox replays it
    assert client.get("/api/animals/A1").json()["status"] == "alive"


# --- Moving a whole camp at once -------------------------------------------

def _camp(client, tags, location, on=None):
    """Put each tag in the herd and place it in `location`."""
    for tag in tags:
        client.post("/api/animals", json={"tag": tag, "sex": "female"})
        client.post("/api/events", json={"tag": tag, "kind": "movement",
                                         "event_date": (on or LONG_AGO).isoformat(),
                                         "location": location})


def test_a_camp_moves_in_one_call(client):
    _camp(client, ["A1", "A2", "A3"], "Bo-kamp")
    r = client.post("/api/events/movement/bulk",
                    json={"tags": ["A1", "A2", "A3"], "event_date": RECENTLY.isoformat(),
                          "location": "Onder-kamp", "note": "after the rain"})
    assert r.status_code == 200, r.text
    assert len(r.json()) == 3
    for tag in ("A1", "A2", "A3"):
        newest = client.get(f"/api/animals/{tag}/events").json()[0]
        assert newest["kind"] == "movement" and newest["location"] == "Onder-kamp"


def test_a_camp_move_naming_an_unknown_tag_moves_nobody(client):
    """Resolved up front on purpose: moving half a camp and failing partway
    leaves the herd recorded in two places at once."""
    _camp(client, ["A1", "A2"], "Bo-kamp")
    r = client.post("/api/events/movement/bulk",
                    json={"tags": ["A1", "NOPE"], "event_date": RECENTLY.isoformat(),
                          "location": "Onder-kamp"})
    assert r.status_code == 404
    assert client.get("/api/animals/A1/events").json()[0]["location"] == "Bo-kamp"


def test_a_camp_move_needs_at_least_one_animal(client):
    r = client.post("/api/events/movement/bulk",
                    json={"tags": [], "event_date": RECENTLY.isoformat(), "location": "Onder-kamp"})
    assert r.status_code == 400


def test_replaying_a_camp_move_moves_the_camp_once(client):
    """The field app queues a camp move in its outbox and replays it after
    an 8-second timeout, so a slow-but-landed request comes back. Without
    an id per animal in the batch, every animal in the camp gains a second
    identical movement - and where the herd IS is derived from exactly
    those rows."""
    _camp(client, ["A1", "A2"], "Bo-kamp")
    body = {"tags": ["A1", "A2"], "event_date": RECENTLY.isoformat(),
            "location": "Onder-kamp", "client_uuid": "batch-1"}

    first = client.post("/api/events/movement/bulk", json=body)
    replay = client.post("/api/events/movement/bulk", json=body)
    assert first.status_code == replay.status_code == 200, replay.text
    assert [e["id"] for e in first.json()] == [e["id"] for e in replay.json()]

    for tag in ("A1", "A2"):
        moves = [e for e in client.get(f"/api/animals/{tag}/events").json()
                 if e["location"] == "Onder-kamp"]
        assert len(moves) == 1, f"{tag} moved twice"


def test_a_camp_move_half_landed_is_completed_by_the_replay(client):
    """A batch that committed some rows and then lost the connection: the
    replay has to add what is missing without duplicating what is not."""
    _camp(client, ["A1", "A2"], "Bo-kamp")
    client.post("/api/events/movement/bulk",
                json={"tags": ["A1"], "event_date": RECENTLY.isoformat(),
                      "location": "Onder-kamp", "client_uuid": "batch-1"})

    r = client.post("/api/events/movement/bulk",
                    json={"tags": ["A1", "A2"], "event_date": RECENTLY.isoformat(),
                          "location": "Onder-kamp", "client_uuid": "batch-1"})
    assert r.status_code == 200, r.text
    for tag in ("A1", "A2"):
        moves = [e for e in client.get(f"/api/animals/{tag}/events").json()
                 if e["location"] == "Onder-kamp"]
        assert len(moves) == 1, f"{tag} has {len(moves)} moves"


def test_two_genuine_camp_moves_are_both_kept(client):
    """Same camp, same day, no capture id - the admin app has no outbox and
    sends none, and a camp really can be moved and moved back."""
    _camp(client, ["A1"], "Bo-kamp")
    for location in ("Onder-kamp", "Bo-kamp"):
        r = client.post("/api/events/movement/bulk",
                        json={"tags": ["A1"], "event_date": RECENTLY.isoformat(),
                              "location": location})
        assert r.status_code == 200, r.text
    assert len(client.get("/api/animals/A1/events").json()) == 3


def test_locations_group_the_alive_herd_by_where_it_is_now(client):
    _camp(client, ["A1", "A2"], "Bo-kamp")
    _camp(client, ["B1"], "Onder-kamp")
    client.post("/api/events/movement/bulk",
                json={"tags": ["A2"], "event_date": RECENTLY.isoformat(), "location": "Onder-kamp"})

    by_location = {row["location"]: [a["tag"] for a in row["animals"]]
                   for row in client.get("/api/locations").json()}
    assert by_location == {"Bo-kamp": ["A1"], "Onder-kamp": ["A2", "B1"]}


def test_locations_leave_out_animals_that_have_left_the_herd(client):
    _camp(client, ["A1", "A2"], "Bo-kamp")
    client.post("/api/events", json={"tag": "A2", "kind": "sale",
                                     "event_date": RECENTLY.isoformat()})
    assert client.get("/api/locations").json() == [
        {"location": "Bo-kamp", "animals": [{"tag": "A1", "name": ""}]}]
