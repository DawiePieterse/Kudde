"""Who is allowed into Kudde at all.

Boord splits its screens: Admin carries worker pay and bank details and is
gated to Tailscale, while Field and Pack House stay open on the farm wifi
because they are phones and tablets that need to work without anyone
setting up a VPN client on them. Kudde has no such split - there is one
farm PC, one person who runs it, and no separate field devices it has to
stay open for - so instead of gating an /admin/ prefix, every request to
this app, static frontend files and /api alike, has to arrive over
Tailscale.

TWO SIGNALS, because only one of them is certain:

  1. The peer address is a Tailscale one. `tailscale serve` proxies from the
     machine itself, so its connections land on loopback - but it sets
     X-Forwarded-For, and uvicorn runs with proxy_headers=True and trusts that
     header from loopback, so request.client is rewritten to the visitor's
     tailnet IP. Verified against uvicorn 0.32.1 (see Boord's security.py,
     which this is adapted from): a LAN client forging the same header is
     NOT rewritten, because its peer is not loopback.

  2. Failing that, the peer is loopback AND the Host names a .ts.net site.
     This is the belt to (1)'s braces. If a Tailscale version ever stops
     sending X-Forwarded-For, (1) silently stops matching and every request
     looks like it came from the console - which, without this, would lock
     the one person who runs Kudde out of their own app with no way in short
     of a rollback. A device on the farm wifi cannot reach this branch: it
     can forge a Host header trivially, but it cannot make its peer address
     loopback.
"""
import ipaddress
import os
from typing import Optional, Union

from fastapi import HTTPException, Request, status

_IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# Tailscale's own address space - what signal (1) above looks for.
_TAILNET_RANGES = (
    ipaddress.ip_network("100.64.0.0/10"),        # CGNAT range - Tailscale IPv4
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),  # Tailscale IPv6
)

# Every tailnet name ends here, so this is what signal (2) matches on. Checked
# only when the peer is already loopback; on its own a Host header proves
# nothing, since anyone can send any Host they like.
_TAILNET_HOST_SUFFIX = ".ts.net"

# Local development only - run_preview.py sets this, because a developer's
# Mac has no `tailscale serve` in front of it and would otherwise be unable
# to open the app at all. start_server.bat - the only thing that launches a
# real Kudde server - does not set it, so this cannot quietly become the way
# in on a real install. Named distinctly from Boord's BOORD_ALLOW_LOOPBACK_ADMIN
# so a machine running both apps can't have one accidentally unlock the other.
DEV_LOOPBACK_ENV = "KUDDE_ALLOW_LOOPBACK"

# What the server says when Kudde is opened from anywhere else. It names the
# fix, because the person reading it is the one who runs the app and the
# answer is always the same address.
TAILSCALE_ONLY_MESSAGE = (
    "Kudde is only reachable over Tailscale - open the secure "
    "https://...ts.net/ address, not the farm wifi one and not localhost"
)


def _peer_address(request: Request) -> Optional[_IPAddress]:
    """The address this request came from, or None if there isn't a usable
    one. A missing or unparseable client counts as untrusted rather than as an
    error: an ASGI transport that does not set a peer would otherwise open
    Kudde to everyone."""
    client = request.client
    if client is None or not client.host:
        return None
    try:
        return ipaddress.ip_address(client.host)
    except ValueError:
        return None


def _asks_for_a_tailnet_site(request: Request) -> bool:
    """Whether this request is addressed to a .ts.net name.

    Both headers are accepted because which one carries the original name is
    the proxy's choice: a reverse proxy may pass the client's Host through
    untouched, or rewrite it to the backend and move the original into
    X-Forwarded-Host. Only ever consulted for a request already known to have
    arrived on loopback."""
    for header in ("host", "x-forwarded-host"):
        value = request.headers.get(header, "")
        if not value:
            continue
        # Take the first entry (X-Forwarded-Host can be a list), drop any
        # :port, and unwrap an IPv6 literal's brackets.
        name = value.split(",")[0].strip().rsplit(":", 1)[0].strip("[]").lower()
        if name.endswith(_TAILNET_HOST_SUFFIX):
            return True
    return False


def is_tailscale_client(request: Request) -> bool:
    """True when this request arrived over Tailscale (or the dev loopback
    escape hatch). Usable as a dependency in its own right for endpoints -
    like the install/health check - that need to see the outcome without
    aborting the request."""
    address = _peer_address(request)
    if address is None:
        return False
    if any(address in network for network in _TAILNET_RANGES):
        return True
    if address.is_loopback:
        if os.environ.get(DEV_LOOPBACK_ENV) == "1":
            return True
        return _asks_for_a_tailnet_site(request)
    return False


def require_tailscale_client(request: Request) -> None:
    """403 rather than 401: there are no credentials to go and fetch, so "try
    again with a token" would be a lie - this request came in on the wrong
    network and always will."""
    if not is_tailscale_client(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, TAILSCALE_ONLY_MESSAGE)
