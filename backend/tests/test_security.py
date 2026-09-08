"""The gate itself: who may reach Kudde at all.

security.py decides this from the network path a request arrived on, since
there is no login to check. That makes it the one module whose behaviour
cannot be seen from the outside on a dev machine - every request there looks
local - so it is worth pinning down here rather than discovering on a farm
that either everyone is refused or nobody is.

Each test drives the app from a different peer address, which is exactly
what differs between a phone on the tailnet, a phone on the farm wifi, and
somebody sitting at the server's console over AnyDesk.
"""
import pytest
from fastapi.testclient import TestClient

import main
import security
# tests/ is a package, so this is the very module pytest already loaded as
# the conftest - not a second copy with a second database engine.
from tests.conftest import FARM_WIFI_PEER, LOOPBACK_PEER, TAILNET_PEER, FromPeer

# One endpoint that needs the herd (any gated route would do) and one page,
# so the middleware is shown to cover the API and the served screens alike.
GATED_API = "/api/animals"
GATED_PAGE = "/field/"


@pytest.fixture()
def from_peer():
    """A client that arrives from a chosen address."""
    def make(peer, **kwargs):
        return TestClient(FromPeer(main.app, peer), **kwargs)
    return make


def test_a_tailnet_device_is_let_in(from_peer):
    with from_peer(TAILNET_PEER) as c:
        assert c.get(GATED_API).status_code == 200
        assert c.get(GATED_PAGE).status_code == 200


def test_the_farm_wifi_is_refused(from_peer):
    """The whole point: being on the farm's own network is not a credential."""
    with from_peer(FARM_WIFI_PEER) as c:
        r = c.get(GATED_API)
        assert r.status_code == 403
        # The message has to name the fix - the person reading it is the one
        # user, and the answer is always the same address.
        assert "Tailscale" in r.text
        assert c.get(GATED_PAGE).status_code == 403


def test_the_servers_own_console_is_refused_too(from_peer):
    """No loopback exemption, deliberately: AnyDesk into this PC is how a
    farm server is normally reached, and such a session would inherit any
    exemption localhost was given."""
    with from_peer(LOOPBACK_PEER) as c:
        assert c.get(GATED_API).status_code == 403


def test_a_forged_host_header_does_not_help_from_the_farm_wifi(from_peer):
    """Anyone can send any Host they like. It only counts once the peer is
    already loopback, which a LAN device cannot fake."""
    with from_peer(FARM_WIFI_PEER) as c:
        r = c.get(GATED_API, headers={"host": "kudde.example.ts.net"})
        assert r.status_code == 403


@pytest.mark.parametrize("header", ["host", "x-forwarded-host"])
def test_tailscale_serve_reaches_us_over_loopback(from_peer, header):
    """`tailscale serve` proxies from the machine itself, so its connections
    land on loopback carrying the tailnet name. Which header holds that name
    is the proxy's choice, so both are accepted."""
    with from_peer(LOOPBACK_PEER) as c:
        r = c.get(GATED_API, headers={header: "kudde.example.ts.net"})
        assert r.status_code == 200


def test_a_tailnet_name_with_a_port_still_counts(from_peer):
    """A Host header carries the port when the address does. Dropping it is
    what makes .ts.net match at all."""
    with from_peer(LOOPBACK_PEER) as c:
        assert c.get(GATED_API, headers={"host": "kudde.example.ts.net:8030"}).status_code == 200


def test_a_lookalike_name_is_not_a_tailnet_name(from_peer):
    """Endswith, not contains: notreally-ts.net.evil.example is not a tailnet."""
    with from_peer(LOOPBACK_PEER) as c:
        r = c.get(GATED_API, headers={"host": "kudde.ts.net.evil.example"})
        assert r.status_code == 403


def test_healthz_answers_from_anywhere(from_peer):
    """install.ps1 polls this from the console before a tailnet exists, so it
    has to answer - and say nothing about the herd while doing it."""
    for peer in (FARM_WIFI_PEER, LOOPBACK_PEER, TAILNET_PEER):
        with from_peer(peer) as c:
            r = c.get("/healthz")
            assert r.status_code == 200, peer
            assert r.json() == {"status": "ok"}


def test_a_request_with_no_peer_at_all_is_refused(from_peer):
    """An ASGI transport that sets no peer must count as untrusted. Read the
    other way - unknown means allowed - this would open the whole app."""
    with from_peer(None) as c:
        assert c.get(GATED_API).status_code == 403


def test_the_dev_bypass_needs_both_loopback_and_the_env_var(from_peer, monkeypatch):
    """The escape hatch exists for local development. The launcher install.ps1
    writes does not set it, so it cannot quietly become the way in on a farm -
    and even set, it does nothing for a device that is not on loopback."""
    monkeypatch.setenv(security.DEV_LOOPBACK_ENV, "1")
    with from_peer(LOOPBACK_PEER) as c:
        assert c.get(GATED_API).status_code == 200
    with from_peer(FARM_WIFI_PEER) as c:
        assert c.get(GATED_API).status_code == 403
