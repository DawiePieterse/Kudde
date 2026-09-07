"""Who is allowed to reach Kudde at all.

There is no login. What used to be a username and password is now the
network path a request arrived on: Kudde answers Tailscale, and nothing
else - Field included. A phone on the farm wifi, or a browser on this PC's
own console reached over AnyDesk, gets the same refusal an outsider would.

That includes the server's own console: browsing http://localhost:8010/
while sitting at the machine is refused like anywhere else, and the one
exemption - the health check below - deliberately says nothing about the
herd. A loopback exemption for anything more is one that any AnyDesk
session to this PC would inherit for free, and AnyDesk is exactly how a
farm server like this is normally reached.

TWO SIGNALS, because only one of them is certain:

  1. The peer address is a Tailscale one. `tailscale serve` proxies from the
     machine itself, so its connections land on loopback - but it sets
     X-Forwarded-For, and uvicorn runs with proxy_headers=True (the default)
     and trusts that header from loopback, so request.client is rewritten to
     the visitor's tailnet IP. A LAN client forging the same header is NOT
     rewritten, because its peer is not loopback.

  2. Failing that, the peer is loopback AND the Host names a .ts.net site.
     This is the belt to (1)'s braces. If a Tailscale version ever stops
     sending X-Forwarded-For, (1) silently stops matching and every request
     looks like it came from the console - which, without this, would lock
     the farm out of its own herd records with no way in short of a
     rollback. A device on the farm wifi cannot reach this branch: it can
     forge a Host header trivially, but it cannot make its peer address
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

# Local development only, and the farm never sets it: there is no dev-preview
# entry point in this repo yet, but the escape hatch is named so it stays
# easy to recognise if one gets added later. The launcher install.ps1 writes
# does not set it, so this cannot quietly become the way in on a real install.
DEV_LOOPBACK_ENV = "KUDDE_ALLOW_LOOPBACK"

# What the server says when a request reaches a gated endpoint from anywhere
# else. Names the fix, because the person reading it is the one user and the
# answer is always the same address.
TAILSCALE_ONLY_MESSAGE = (
    "Kudde is only reachable over Tailscale - open the secure "
    "https://...ts.net/ address, not the farm wifi one and not localhost"
)


def _peer_address(request: Request) -> Optional[_IPAddress]:
    """The address this request came from, or None if there isn't a usable
    one. A missing or unparseable client counts as untrusted rather than as
    an error: an ASGI transport that does not set a peer would otherwise
    open the whole app to everyone."""
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
    """True when this request arrived over Tailscale (or the dev bypass)."""
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
    """Gated endpoints depend on this. 403 rather than 401: there are no
    credentials to go and fetch, so "try again with a token" would be a lie -
    this request came in on the wrong network and always will."""
    if not is_tailscale_client(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, TAILSCALE_ONLY_MESSAGE)
