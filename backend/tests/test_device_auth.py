"""The second way in: a paired device's cookie, and the tunnel it arrives by.

Two things are pinned down here. That a cookie is believed only when this
farm signed it, it has not expired, its device has not been revoked and its
role is one Kudde admits. And that opening a road in from the internet (the
Cloudflare Tunnel, whose connector reaches us over loopback just as
`tailscale serve` does) did not turn the Tailscale check's own signals into
something a stranger can type.
"""
import time

import pytest
from fastapi.testclient import TestClient

import device_auth
import main
import security
from tests.conftest import LOOPBACK_PEER, TAILNET_PEER, FromPeer

GATED_API = "/api/animals"
SECRET = "s" * 40

# What the tunnel's connector adds to every request; a visitor cannot remove
# it. The peer is loopback because the connector runs on the farm PC.
VIA_TUNNEL = {"cf-ray": "8a1b2c3d4e5f-JNB", "cf-connecting-ip": "41.13.200.7"}


@pytest.fixture()
def farm_secret(tmp_path, monkeypatch):
    """A farm whose Boord has paired at least one device: the secret exists."""
    (tmp_path / device_auth.SECRET_FILE).write_text(SECRET + "\n")
    monkeypatch.setenv(device_auth.AUTH_DIR_ENV, str(tmp_path))
    return tmp_path


def cookie(role="field", device_id="tablet-1", expires_in=device_auth.LIFETIME_SECONDS,
           secret=SECRET, **extra):
    payload = dict({"id": device_id, "role": role, "exp": int(time.time()) + expires_in}, **extra)
    return {"cookie": device_auth.COOKIE_NAME + "=" + device_auth.sign(payload, secret.encode())}


def through_tunnel(headers=None):
    """One request as it arrives from the internet, with whatever else the
    visitor chose to send."""
    with TestClient(FromPeer(main.app, LOOPBACK_PEER)) as c:
        return c.get(GATED_API, headers=dict(VIA_TUNNEL, **(headers or {})))


def test_a_paired_field_device_is_let_in_from_the_internet(farm_secret):
    assert through_tunnel(cookie("field")).status_code == 200


def test_a_paired_admin_device_is_let_in_too(farm_secret):
    assert through_tunnel(cookie("admin")).status_code == 200


def test_an_unpaired_device_is_refused_and_told_about_pairing(farm_secret):
    """Not the Tailscale message: that would send somebody looking for an app
    this road exists to spare them."""
    r = through_tunnel()
    assert r.status_code == 403
    assert "paired" in r.text and "Tailscale" not in r.text


def test_a_dashboard_only_device_does_not_get_the_herd(farm_secret):
    """The farm manager's pairing is for Boord's dashboard. It is a real,
    correctly signed cookie - and still not a key to this app."""
    assert through_tunnel(cookie("dashboard")).status_code == 403


def test_without_a_secret_no_cookie_is_believed(tmp_path, monkeypatch):
    """A farm that has paired nothing has no secret file, and must behave
    exactly as it did when Tailscale was the only way in."""
    monkeypatch.setenv(device_auth.AUTH_DIR_ENV, str(tmp_path))
    assert through_tunnel(cookie()).status_code == 403


@pytest.mark.parametrize("contents", ["", "short", " " * 64])
def test_an_empty_or_short_secret_signs_nothing(tmp_path, monkeypatch, contents):
    """An empty file is the dangerous one: HMAC under "" is a signature
    anybody can make for themselves."""
    (tmp_path / device_auth.SECRET_FILE).write_text(contents)
    monkeypatch.setenv(device_auth.AUTH_DIR_ENV, str(tmp_path))
    assert through_tunnel(cookie(secret=contents.strip())).status_code == 403


def test_a_cookie_signed_by_somebody_else_is_refused(farm_secret):
    assert through_tunnel(cookie(secret="x" * 40)).status_code == 403


def test_a_cookie_edited_after_signing_is_refused(farm_secret):
    """Promote yourself from dashboard to admin, keep the old signature."""
    honest = cookie("dashboard")["cookie"].split("=", 1)[1]
    forged = cookie("admin")["cookie"].split("=", 1)[1].split(".")[0] + "." + honest.split(".")[1]
    assert through_tunnel({"cookie": device_auth.COOKIE_NAME + "=" + forged}).status_code == 403


def test_an_expired_cookie_is_refused(farm_secret):
    assert through_tunnel(cookie(expires_in=-1)).status_code == 403


@pytest.mark.parametrize("junk", ["", "x", "a.b", "a.b.c", "....", "e30.e30", "%00.%00"])
def test_junk_in_the_cookie_is_a_refusal_not_a_crash(farm_secret, junk):
    r = through_tunnel({"cookie": device_auth.COOKIE_NAME + "=" + junk})
    assert r.status_code == 403


@pytest.mark.parametrize("payload", [
    {"role": "admin", "exp": 9999999999},                    # no id
    {"id": "", "role": "admin", "exp": 9999999999},          # empty id
    {"id": "t", "role": "admin"},                            # never expires
    {"id": "t", "role": "admin", "exp": "9999999999"},       # exp not a number
    {"id": "t", "role": "admin", "exp": True},               # exp a bool
    {"id": "t", "role": ["admin"], "exp": 9999999999},       # role not a string
])
def test_a_signed_cookie_missing_a_field_is_refused(farm_secret, payload):
    """Signed with the real secret, so only Boord could have made these - but
    a payload this app cannot fully check is one it does not act on."""
    value = device_auth.sign(payload, SECRET.encode())
    assert through_tunnel({"cookie": device_auth.COOKIE_NAME + "=" + value}).status_code == 403


def test_a_lost_phone_is_revoked_alone(farm_secret):
    (farm_secret / device_auth.REVOKED_FILE).write_text("tablet-1   # lost at the dip, Nov\n")
    assert through_tunnel(cookie(device_id="tablet-1")).status_code == 403
    assert through_tunnel(cookie(device_id="tablet-2")).status_code == 200


# --- The tunnel must not be a way round the Tailscale check ---------------

@pytest.mark.parametrize("header", ["host", "x-forwarded-host"])
def test_a_tailnet_name_typed_by_a_stranger_does_not_open_the_door(farm_secret, header):
    """The forgery this file exists for. From loopback, a .ts.net name is
    what `tailscale serve` looks like - and the tunnel's connector is on
    loopback too, carrying whatever headers the internet sent it."""
    assert through_tunnel({header: "kudde.example.ts.net"}).status_code == 403


def test_a_tailnet_address_seen_through_the_tunnel_does_not_count(farm_secret):
    """uvicorn rewrites the peer from X-Forwarded-For when the connection is
    from loopback, so the peer address is also the visitor's to choose."""
    with TestClient(FromPeer(main.app, TAILNET_PEER)) as c:
        assert c.get(GATED_API, headers=VIA_TUNNEL).status_code == 403


def test_the_dev_bypass_does_not_reach_through_the_tunnel(farm_secret, monkeypatch):
    monkeypatch.setenv(security.DEV_LOOPBACK_ENV, "1")
    assert through_tunnel().status_code == 403


def test_tailscale_still_works_exactly_as_before(farm_secret):
    """Either way in is enough: adding the cookie took nothing away."""
    with TestClient(FromPeer(main.app, TAILNET_PEER)) as c:
        assert c.get(GATED_API).status_code == 200


# --- Renewal: a device in use is never logged out mid-season --------------

def test_an_ageing_cookie_is_renewed_on_the_farms_domain(farm_secret):
    r = through_tunnel(cookie(expires_in=100 * 24 * 3600, dom="bekfontein.example"))
    assert r.status_code == 200
    renewed = r.headers["set-cookie"]
    assert renewed.startswith(device_auth.COOKIE_NAME + "=")
    for attribute in ("Domain=bekfontein.example", "HttpOnly", "Secure", "SameSite=lax",
                      "Max-Age=%d" % device_auth.LIFETIME_SECONDS):
        assert attribute in renewed, attribute
    # And what it was given is itself a cookie that gets in, for the same
    # device, good for a full term again.
    value = renewed.split(";")[0].split("=", 1)[1]
    payload = device_auth.verify(value)
    assert payload["id"] == "tablet-1" and payload["role"] == "field"
    assert not device_auth.needs_renewal(payload)


def test_a_fresh_cookie_is_left_alone(farm_secret):
    assert "set-cookie" not in through_tunnel(cookie()).headers


def test_a_refused_cookie_is_never_renewed(farm_secret):
    """Renewal must not be a way to turn an expired cookie into a live one."""
    assert "set-cookie" not in through_tunnel(cookie(expires_in=-1)).headers
