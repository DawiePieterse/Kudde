"""Test fixtures.

KUDDE_DATA_DIR is set here BEFORE `db` (and therefore `backup`, `migrate`,
`main`) is imported, because those modules read it at import time and build
the engine from it. Nothing in the suite ever touches a real farm's
data/ directory.

Each test gets a database of its own, built by the migrations rather than by
create_all - the same path a new farm takes on first boot, so a broken
migration fails here instead of on the first install.
"""
import os
import shutil
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="kudde-tests-")
os.environ["KUDDE_DATA_DIR"] = _TMP

import db  # noqa: E402
import main  # noqa: E402

# Tailnet IPv4 lives in 100.64.0.0/10, which is what security.py accepts.
TAILNET_PEER = ("100.64.0.1", 41234)
LOOPBACK_PEER = ("127.0.0.1", 41234)
FARM_WIFI_PEER = ("192.168.1.50", 41234)


class FromPeer:
    """Present the app with a chosen peer address.

    Every request in this suite has to arrive from somewhere, because
    security.py refuses anything that did not come over Tailscale - and
    TestClient's transport reports a peer of ("testclient", 50000), which is
    not an address at all, so the whole suite is refused by default.

    Rewriting the ASGI scope is deliberate, and better than the two
    alternatives: setting KUDDE_ALLOW_LOOPBACK would test the dev bypass
    instead of the gate, and exempting the test client in main.py would put
    a hole in production code to suit the tests. This changes only what the
    transport reports, which is exactly what differs between a phone on the
    tailnet and one on the farm wifi - the gate itself stays under test, and
    test_security.py drives this same wrapper from the other addresses to
    prove it still refuses them.
    """

    def __init__(self, app, peer=TAILNET_PEER):
        self.app = app
        self.peer = peer

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=self.peer)
        await self.app(scope, receive, send)

# The two dates the "needs weighing" cutoff sits between. Written relative to
# today rather than as fixed dates: _STALE_WEIGHT_DAYS is a rolling window,
# so hardcoded dates would start failing on their own six months from now.
from datetime import date, timedelta  # noqa: E402

from routers.dashboard import _STALE_WEIGHT_DAYS  # noqa: E402

LONG_AGO = date.today() - timedelta(days=_STALE_WEIGHT_DAYS + 30)
RECENTLY = date.today() - timedelta(days=7)


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    # A fresh database per test. Disposing the engine first matters on
    # Windows, where an open connection keeps the file locked and the
    # removal fails silently, leaving the next test looking at the previous
    # one's herd.
    db.engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(db.DB_PATH + suffix)
        except OSError:
            pass

    # And a fresh photo directory, because photos are the half of a farm's
    # data that is NOT in the database. Animal ids restart at 1 with the new
    # database, so without this the first animal of one test inherits the
    # photo files of the first animal of the last one.
    shutil.rmtree(db.PHOTOS_DIR, ignore_errors=True)
    os.makedirs(db.PHOTOS_DIR, exist_ok=True)
    with TestClient(FromPeer(main.app)) as c:  # startup: run_migrations
        yield c


@pytest.fixture()
def herd(client):
    """A small herd with a history, for the tests that need one to look at.

    Deliberately not one animal in one state: A1 is alive and overdue a
    weighing, B2 is alive and recently weighed, C3 has been sold. Those are
    the three cases every dashboard figure has to tell apart.
    """
    client.post("/api/animals", json={"tag": "A1", "name": "Bessie", "breed": "Nguni",
                                      "sex": "female", "birth_date": "2022-03-15"})
    client.post("/api/animals", json={"tag": "B2", "name": "Bull", "sex": "male"})
    client.post("/api/animals", json={"tag": "C3", "sex": "female"})
    client.post("/api/events", json={"tag": "A1", "kind": "weight",
                                     "event_date": LONG_AGO.isoformat(), "value": 310.0})
    client.post("/api/events", json={"tag": "B2", "kind": "weight",
                                     "event_date": RECENTLY.isoformat(), "value": 640.0})
    client.post("/api/events", json={"tag": "C3", "kind": "sale",
                                     "event_date": RECENTLY.isoformat(), "note": "Vleissentraal"})
    return client
