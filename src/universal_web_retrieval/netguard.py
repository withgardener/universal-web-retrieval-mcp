"""Optional pre-flight check on ``web_fetch`` targets: ``WEB_RETRIEVAL_BLOCK_PRIVATE``.

**Off by default.** ``web_fetch`` keeps the 0.3.0 behaviour of fetching whatever
the host can reach, including intranet, ``localhost`` and metadata endpoints;
that is a deliberate capability for a local agent tool. Setting
``WEB_RETRIEVAL_BLOCK_PRIVATE=1`` (the name this README has promised since
0.1.0) turns the check in this module on for deployments that want it.

When enabled, the check refuses the obvious private targets *before* any
provider sees the URL:

* An **IP literal** (including bracketed IPv6, IPv4-mapped IPv6 and the decimal
  ``2130706433`` form) is decided exactly, with no DNS.
* A **hostname** goes through ``socket.getaddrinfo`` — the platform resolver, so
  ``/etc/hosts``, systemd-resolved and every host-level control still apply. We
  deliberately do not implement our own DNS client: bypassing the dependent
  host's resolution policy would defeat its existing security constraints.
* **Every** returned address must be globally routable. A hostname that answers
  with one public and one private address is rejected: that is what a rebinding
  attempt looks like from here.

What this is not
----------------
It is **not SSRF protection**, and no document, error string or tool description
may say that it is. The fetch itself happens on the provider's network, so there
is nothing here to bind a connection to:

* **Redirects are followed by the provider**, not by this process. We check the
  target we send and re-check the final URL a provider reports
  (``check_url``) — a redirect to a private host fails the call closed for the
  caller, but the request has already left. Preventing that hop outright needs a
  local fetching proxy, which this server does not have.
* **DNS rebinding** between our check and the provider's connect cannot be
  excluded, for the same reason: only a design that resolves, connects and then
  verifies the peer address (full connection binding) can claim that.
* **A hostname that does not resolve here is allowed** (logged at DEBUG). The
  provider resolves it on its own network, so our resolver's failure proves
  nothing. Residual gap: an internal-only name that only resolves inside the
  private network is not blocked.

For untrusted agent input in a hardened deployment, keep network isolation
(container, netns, egress firewall/deny-list at the provider) as the outer
boundary. This check is one cheap deny of easy targets on top, nothing more.
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
        f"addresses are blocked because WEB_RETRIEVAL_BLOCK_PRIVATE=1 is set; "
        f"unset it (or set it to 0) to allow intranet fetches")


def check_url(url: str, *, block_private: bool | None = None) -> None:
    """Raise ``InvalidInputError`` when ``url`` must not be fetched.

    No-op unless ``WEB_RETRIEVAL_BLOCK_PRIVATE`` is on, so the default path does
    no parsing, no DNS and no work at all. ``block_private`` overrides the
    environment for one call (tests, embedding).
    """
    enabled = config.block_private_network() if block_private is None else block_private
    if not enabled:
        return

    _scheme, host, port = _host_and_port(url)

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
