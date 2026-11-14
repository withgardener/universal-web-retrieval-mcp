"""Where ``web_fetch`` is allowed to point: public networks by default.

Before 0.3.1 only the URL *scheme* was validated, so
``http://127.0.0.1:9222/…``, ``http://169.254.169.254/latest/meta-data/`` and
intranet hostnames were handed to a third-party extraction service unchanged.
Fetching is opt-in for private space now: ``UWR_ALLOW_PRIVATE_NETWORK=1``
restores the old behaviour for operators who really do fetch intranet pages.

What is checked
---------------
* An **IP literal** (including bracketed IPv6, IPv4-mapped IPv6 and the decimal
  ``2130706433`` form) is decided exactly, with no DNS.
* A **hostname** goes through ``socket.getaddrinfo`` — the platform resolver, so
  ``/etc/hosts``, systemd-resolved and every host-level control still apply. We
  deliberately do not implement our own DNS client: bypassing the dependent
  host's resolution policy would defeat its existing security constraints.
* **Every** returned address must be globally routable. A hostname that answers
  with one public and one private address is rejected: that is what a rebinding
  attempt looks like from here.

What cannot be guaranteed, and is therefore not claimed
------------------------------------------------------
* **A hostname that does not resolve here is allowed** (logged at DEBUG). The
  fetch happens on the provider's network, so our resolver's failure proves
  nothing about where the provider will connect. Residual gap: an
  internal-only name that only resolves inside the private network is not
  blocked. Deployments that must not leak internal names should deny-listing
  them at the provider or keep ``UWR_ALLOW_PRIVATE_NETWORK`` unset and treat
  unresolved targets as untrusted.
* **Redirects are followed by the provider**, not by this process, so there is
  no connection binding to inspect. We check the target we send and re-check the
  final URL a provider reports (``check_url``) — a redirect to a private host is
  caught and fails the call closed, but the request has already left. Preventing
  that hop outright needs a local fetching proxy, which this server does not
  have.
* **DNS rebinding** between our check and the provider's connect cannot be
  excluded for the same reason. Only a design that resolves, connects and then
  verifies the peer address (full connection binding) can claim that, and that
  is not what a third-party extraction API gives us.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import List
from urllib.parse import urlsplit

from . import config
from .errors import InvalidInputError

log = logging.getLogger("universal_web_retrieval.netguard")

DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_and_port(url: str) -> tuple:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise InvalidInputError(f"unparseable URL: {url[:60]}") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in DEFAULT_PORTS:
        raise InvalidInputError(f"unsupported URL scheme: {scheme or '(none)'}")
    host = parts.hostname
    if not host:
        raise InvalidInputError("URL has no host")
    try:
        port = parts.port or DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise InvalidInputError("URL has an invalid port") from exc
    return scheme, host, port


def _resolve(host: str, port: int) -> List[str]:
    """All addresses the platform resolver returns for ``host``."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: List[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _reject(host: str, detail: str) -> InvalidInputError:
    return InvalidInputError(
        f"refusing to fetch {host}: {detail} — private, loopback and link-local "
        f"addresses are blocked (set UWR_ALLOW_PRIVATE_NETWORK=1 to allow "
        f"intranet fetches)")


def check_url(url: str, *, allow_private: bool | None = None) -> None:
    """Raise ``InvalidInputError`` when ``url`` must not be fetched.

    ``allow_private`` overrides the environment for one call (tests, embedding).
    """
    allowed = config.allow_private_network() if allow_private is None else allow_private
    _scheme, host, port = _host_and_port(url)
    if allowed:
        return

    literal = None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise _reject(host, f"{literal} is not a public address")
        return

    try:
        addresses = _resolve(host, port)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        # Not resolvable here; the provider resolves it on its own network.
        log.debug("netguard: %s does not resolve locally (%s) — allowing it; "
                  "an internal-only name is not detectable from this host",
                  host, exc)
        return
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise _reject(host, f"unparseable address {address!r}")
        if not ip.is_global:
            raise _reject(host, f"resolves to {ip}")
    return
