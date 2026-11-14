"""Optional pre-flight check on ``web_fetch`` targets: ``WEB_RETRIEVAL_BLOCK_PRIVATE``.

**Off by default.** ``web_fetch`` keeps the 0.3.0 behaviour of fetching whatever
the host can reach, including intranet, ``localhost`` and metadata endpoints;
that is a deliberate capability for a local agent tool. Setting
``WEB_RETRIEVAL_BLOCK_PRIVATE=1`` (the name this README has promised since
0.1.0) turns the check in this module on for deployments that want it.

When enabled, the check refuses the obvious private targets *before* any
provider sees the URL:

* An **IP literal** is decided exactly, with no DNS. That includes bracketed
  IPv6, IPv4-mapped IPv6, an IPv6 zone id (``[fe80::1%25eth0]``) and every
  legacy IPv4 spelling the C library also accepts — ``127.1``, ``0177.0.0.1``,
  ``0x7f.0.0.1``, ``0x7f000001`` and the integer ``2130706433``. Those forms are
  decoded here rather than handed to the resolver, because
  ``ipaddress.ip_address`` rejects them and a host that reaches the resolver can
  dodge the check. A **numeric-only host that cannot be decoded** (``1.2.3.4.5``,
  ``09.1.1.1``) is refused: it cannot be a domain name, and guessing is the
  wrong direction for a deny check.
* A **loopback name** is refused without DNS: ``localhost``, ``localhost.`` and
  any ``*.localhost`` are reserved for loopback (RFC 6761), so a resolver that
  answers ``NXDOMAIN`` for one must not turn into an allow.
* A **hostname** goes through ``socket.getaddrinfo`` — the platform resolver, so
  ``/etc/hosts``, systemd-resolved and every host-level control still apply. We
  deliberately do not implement our own DNS client: bypassing the dependent
  host's resolution policy would defeat its existing security constraints. The
  lookup is bounded (``WEB_RETRIEVAL_NETGUARD_DNS_TIMEOUT``, default 2 s) and a
  lookup that does not answer in time is **refused**, not treated as allowed: an
  unresponsive resolver is how a target would otherwise skip the check.
* **Every** returned address must be globally routable. A hostname that answers
  with one public and one private address is rejected: that is what a rebinding
  attempt looks like from here.

What this is not
----------------
It is **not SSRF protection**, it is **not a guarantee**, and no document, error
string or tool description may say that it is. The fetch itself happens on the
provider's network, so there is nothing here to bind a connection to:

* **Redirects are followed by the provider**, not by this process. We check the
  target we send and re-check the final URL a provider reports
  (``check_url``) — a redirect to a private host fails the call closed for the
  caller, but the request has already left. Preventing that hop outright needs a
  local fetching proxy, which this server does not have.
* **DNS rebinding** between our check and the provider's connect cannot be
  excluded, for the same reason: only a design that resolves, connects and then
  verifies the peer address (full connection binding) can claim that.
* **A hostname that does not resolve here is still allowed** (logged at DEBUG).
  The provider resolves it on its own network, so a definitive ``NXDOMAIN``
  proves nothing about the provider's view. Residual gap: an internal-only name
  that only resolves inside the private network is not blocked. ``localhost``
  and ``*.localhost`` are the exception and are refused without DNS, as above.
  A *timeout* is different from ``NXDOMAIN`` and is refused above.
* **The resolver is only as trustworthy as the host**: a host with a poisoned or
  overridden resolver gets the answer that resolver gives.

For untrusted agent input in a hardened deployment, keep network isolation
(container, netns, egress firewall/deny-list at the provider) as the outer
boundary. This check is one cheap deny of easy targets on top, nothing more.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
from typing import List, Optional
from urllib.parse import urlsplit

from . import config
from .errors import InvalidInputError

log = logging.getLogger("universal_web_retrieval.netguard")

DEFAULT_PORTS = {"http": 80, "https": 443}

# Hosts written only as numbers, dots and ``0x`` prefixes: legacy IPv4 spellings
# (and typos of them). Deliberately excludes anything with a hostname letter, so
# a domain such as ``abc123.com`` is never treated as a number.
_NUMERIC_HOST_RE = re.compile(
    r"^(?:0[xX][0-9a-fA-F]+|\d+)(?:\.(?:0[xX][0-9a-fA-F]+|\d+))*\.?$")

# Width of each trailing part, inet_aton style: 1 part = whole address,
# 2 parts = a.b, 3 parts = a.b.c, 4 parts = a.b.c.d.
_PART_WIDTHS = {1: (32,), 2: (8, 24), 3: (8, 8, 16), 4: (8, 8, 8, 8)}


class _ResolveTimeout(Exception):
    """The platform resolver did not answer inside the configured budget."""


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


# Names RFC 6761 reserves for loopback. They are decided without DNS so that a
# resolver which fails (or stalls below the check) cannot turn one into an allow.
_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain",
                             "ip6-localhost", "ip6-loopback"})


def _is_loopback_name(host: str) -> bool:
    name = host.rstrip(".").lower()
    return name in _LOOPBACK_NAMES or name.endswith(".localhost")


def _resolve(host: str, port: int) -> List[str]:
    """All addresses the platform resolver returns for ``host``."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses: List[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        address = str(sockaddr[0])
        if address not in addresses:
            addresses.append(address)
    return addresses


def _resolve_bounded(host: str, port: int, timeout: float) -> List[str]:
    """Resolve with a hard time bound; raise ``_ResolveTimeout`` when it expires.

    ``socket.getaddrinfo`` takes no timeout and can block for the resolver's own
    retry schedule. A daemon thread is used so an abandoned lookup cannot keep
    the interpreter alive, and the wait is capped by ``timeout``.
    """
    outcome: dict = {}

    def worker() -> None:
        try:
            outcome["addresses"] = _resolve(host, port)
        except Exception as exc:  # noqa: BLE001 — re-raised on the caller thread
            outcome["error"] = exc

    thread = threading.Thread(target=worker, name="uwr-netguard-resolve",
                              daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise _ResolveTimeout()
    if "error" in outcome:
        raise outcome["error"]
    return outcome.get("addresses") or []


def _legacy_ipv4(host: str) -> Optional[ipaddress.IPv4Address]:
    """Decode ``127.1`` / ``0177.0.0.1`` / ``0x7f000001``; ``None`` if invalid."""
    text = host[:-1] if host.endswith(".") else host
    raw_parts = text.split(".")
    widths = _PART_WIDTHS.get(len(raw_parts))
    if widths is None:
        return None
    values: List[int] = []
    for part, width in zip(raw_parts, widths):
        try:
            if part.lower().startswith("0x"):
                value = int(part, 16)
            elif len(part) > 1 and part.startswith("0"):
                value = int(part, 8)
            else:
                value = int(part, 10)
        except ValueError:
            return None
        if value < 0 or value >= (1 << width):
            return None
        values.append(value)
    number = 0
    for value, width in zip(values, widths):
        number = (number << width) | value
    return ipaddress.IPv4Address(number)


def _decide_literal(host: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return the address ``host`` spells, or ``None`` for a real hostname.

    A numeric-only host that cannot be decoded raises ``InvalidInputError``:
    refusing is the conservative direction for an opt-in deny check.
    """
    candidate = host
    if "%" in candidate:
        # IPv6 zone id ("fe80::1%eth0", percent-encoded by urlsplit). The zone
        # names an interface on the *sending* host; the address is what decides.
        candidate = candidate.split("%", 1)[0]
        try:
            return ipaddress.ip_address(candidate)
        except ValueError as exc:
            raise InvalidInputError(
                f"refusing to fetch {host}: unparseable IPv6 literal") from exc
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    if _NUMERIC_HOST_RE.match(host):
        address = _legacy_ipv4(host)
        if address is None:
            raise InvalidInputError(
                f"refusing to fetch {host}: numeric-only host that is not a valid "
                f"IPv4 address — with WEB_RETRIEVAL_BLOCK_PRIVATE=1 an ambiguous "
                f"target is refused instead of looked up")
        return address
    return None


def _reject(host: str, detail: str) -> InvalidInputError:
    return InvalidInputError(
        f"refusing to fetch {host}: {detail} — the opt-in "
        f"WEB_RETRIEVAL_BLOCK_PRIVATE check refuses non-public targets. It is a "
        f"best-effort pre-flight heuristic, not SSRF protection: it cannot stop "
        f"DNS rebinding or a redirect hop the provider makes, and a name that "
        f"does not resolve here is allowed. Unset the variable (or set it to 0) "
        f"to allow intranet fetches")


def _reject_timeout(host: str, timeout: float) -> InvalidInputError:
    return InvalidInputError(
        f"refusing to fetch {host}: the DNS lookup did not answer within "
        f"{timeout:.1f}s. With WEB_RETRIEVAL_BLOCK_PRIVATE=1 an unproven target "
        f"is refused rather than allowed, because a resolver that stalls would "
        f"otherwise let a target skip the check. Raise "
        f"WEB_RETRIEVAL_NETGUARD_DNS_TIMEOUT for a slower resolver, or unset the "
        f"variable to allow intranet fetches")


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
    literal = _decide_literal(host)
    if literal is not None:
        if not literal.is_global:
            raise _reject(host, f"{literal} is not a public address")
        return

    if _is_loopback_name(host):
        # Decided here, not by the resolver: "it did not resolve" must never be
        # the reason a name reserved for loopback is allowed.
        raise _reject(host, "it is a loopback name (localhost / *.localhost)")

    timeout = config.netguard_dns_timeout()
    try:
        addresses = _resolve_bounded(host, port, timeout)
    except _ResolveTimeout:
        # Conservative: an unanswered lookup is not evidence of a public host.
        log.debug("netguard: DNS lookup for %s did not answer within %.1fs — "
                  "refusing it", host, timeout)
        raise _reject_timeout(host, timeout) from None
    except (socket.gaierror, UnicodeError, OSError) as exc:
        # A definitive resolution failure: the provider resolves it on its own
        # network, so our NXDOMAIN proves nothing. Documented residual gap.
        log.debug("netguard: %s does not resolve locally (%s) — allowing it; "
                  "an internal-only name is not detectable from this host",
                  host, exc)
        return
    if not addresses:
        log.debug("netguard: the resolver returned no address for %s — allowing "
                  "it; this host cannot prove the provider's view", host)
        return
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise _reject(host, f"unparseable address {address!r}")
        if not ip.is_global:
            raise _reject(host, f"resolves to {ip}")
