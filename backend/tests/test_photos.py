"""Photos taken against an animal, and storing each of them once.

The upload path is where a replay is most likely and most expensive: a phone
camera photo is several MB, farm wifi is slow, and the field app's deadline
is 8 seconds - so an upload that timed out but actually landed is ordinary
here rather than rare.
"""
import io
import os

import pytest
from PIL import Image

import db


def _jpeg(size=(1200, 900), colour=(120, 90, 60)):
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    return buf.getvalue()


def _upload(client, tag="A1", client_uuid=None, caption=None, image=None):
    data = {}
    if client_uuid is not None:
        data["client_uuid"] = client_uuid
    if caption is not None:
        data["caption"] = caption
    return client.post(f"/api/animals/{tag}/photos",
                       files={"file": ("photo.jpg", image or _jpeg(), "image/jpeg")},
                       data=data)


def _files_on_disk(client, tag="A1"):
    animal_id = client.get(f"/api/animals/{tag}").json()["id"]
    animal_dir = os.path.join(db.PHOTOS_DIR, str(animal_id))
    return sorted(os.listdir(animal_dir)) if os.path.isdir(animal_dir) else []


@pytest.fixture()
def herd_of_one(client):
    client.post("/api/animals", json={"tag": "A1", "sex": "female"})
    return client


def test_a_photo_is_stored_against_the_animal(herd_of_one):
    r = _upload(herd_of_one, caption="in the bo-kamp")
    assert r.status_code == 200, r.text
    assert r.json()["caption"] == "in the bo-kamp"
    assert herd_of_one.get(r.json()["url"]).status_code == 200


def test_replaying_an_upload_stores_it_once(herd_of_one):
    """The outbox replays an upload that may already have landed. Without a
    capture id nothing tells that retry apart from a second photo, and the
    farmer ends up deleting duplicates by hand."""
    first = _upload(herd_of_one, client_uuid="p-1")
    replay = _upload(herd_of_one, client_uuid="p-1")
    assert first.status_code == replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["url"] == first.json()["url"]

    assert len(herd_of_one.get("/api/animals/A1/photos").json()) == 1
    # And no second copy of the bytes: the check sits before the disk write.
    assert len(_files_on_disk(herd_of_one)) == 1


def test_a_replay_is_answered_without_re_encoding_the_upload(herd_of_one):
    """A replay must not be a second pass through Pillow. Sending bytes that
    are not an image at all on the replay proves the early return fires: a
    real re-encode would refuse them."""
    first = _upload(herd_of_one, client_uuid="p-1")
    replay = herd_of_one.post("/api/animals/A1/photos",
                              files={"file": ("photo.jpg", b"not an image", "image/jpeg")},
                              data={"client_uuid": "p-1"})
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert len(_files_on_disk(herd_of_one)) == 1


def test_two_genuine_photos_of_one_animal_are_both_kept(herd_of_one):
    """A farmer really can take two pictures of the same animal a minute
    apart - different capture ids, so both are stored."""
    a = _upload(herd_of_one, client_uuid="p-1")
    b = _upload(herd_of_one, client_uuid="p-2")
    assert a.json()["id"] != b.json()["id"]
    assert len(herd_of_one.get("/api/animals/A1/photos").json()) == 2
    assert len(_files_on_disk(herd_of_one)) == 2


def test_a_photo_without_a_capture_id_is_still_accepted(herd_of_one):
    """The admin app has no outbox and sends none. Two of them are two
    photos, not a replay - a NULL is not equal to another NULL."""
    a = _upload(herd_of_one)
    b = _upload(herd_of_one)
    assert a.status_code == b.status_code == 200, b.text
    assert a.json()["client_uuid"] is None
    assert len(herd_of_one.get("/api/animals/A1/photos").json()) == 2


def test_a_replayed_upload_is_not_resurrected_after_deletion(herd_of_one):
    """Deleting a photo and then replaying its upload is a real upload again,
    not a no-op - the row it matched is gone."""
    first = _upload(herd_of_one, client_uuid="p-1")
    assert herd_of_one.delete(f"/api/photos/{first.json()['id']}").status_code == 200
    again = _upload(herd_of_one, client_uuid="p-1")
    assert again.status_code == 200, again.text
    # Not the id: SQLite hands the freed primary key straight back. The
    # filename is a fresh uuid and is never reused, so it is what says
    # whether these are the same stored photo.
    assert again.json()["filename"] != first.json()["filename"]
    assert len(herd_of_one.get("/api/animals/A1/photos").json()) == 1
    assert _files_on_disk(herd_of_one) == [again.json()["filename"]]
