"""0.3.1 — private-network access is opt-in, checked at the only choke point.

``web_fetch`` used to validate the scheme and nothing else, so
``http://127.0.0.1:9222``, ``http://169.254.169.254`` or an intranet hostname
was handed to a third-party extraction service as-is. Default policy now:
reject, unless ``UWR_ALLOW_PRIVATE_NETWORK=1`` says the operator wants intranet
fetches.

What this can and cannot prove (also in the module docstring/README):

* An **IP literal** is decided exactly — no network, no DNS.
* A **hostname** is resolved through the platform resolver (so /etc/hosts,
  systemd-resolved and any host-level control still apply) and rejected when
  ANY returned address is non-public. Mixed answers are rejected too: that is
  what a rebinding attempt looks like.
* A hostname that does not resolve here is allowed and logged — the fetch
  happens at the provider, so our inability to resolve it proves nothing. This
  is the documented residual gap for internal-only names.
* **Redirects** are followed by the provider's own network, which we cannot
  bind to. We check the initial target and re-check the final URL a provider
  reports, but the request has already left; that re-check fails closed for the
  caller, it does not undo the transfer. Claiming more would be a lie.
"""
from __future__ import annotations

import socket

import pytest

from universal_web_retrieval import netguard
from universal_web_retrieval.errors import (AuthMode, Capability, InvalidInputError,
                                            Provider, ProviderError, FetchResult)
from universal_web_retrieval.providers.router import ProviderRouter


PUBLIC = "93.184.216.34"


def _patch_dns(monkeypatch, *addresses):
    """Answer DNS for the test through the seam netguard uses (addresses only)."""
    def fake(host, port):
        return list(addresses)
    monkeypatch.setattr(netguard, "_resolve", fake)


def _resolve_to(monkeypatch, *addresses):
    _patch_dns(monkeypatch, *addresses)


def _resolve_fails(monkeypatch):
    def fake(host, port):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(netguard, "_resolve", fake)


# ---------------------------------------------------------------- IP literals
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9222/json",
    "http://127.0.0.1/",
    "http://10.1.2.3/",
    "http://172.16.0.1/",
    "http://192.168.1.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://0.0.0.0/",
    "http://100.64.0.1/",
    "http://[::1]/",
    "http://[fe80::1]/",
    "http://[fd00::1]/",
    "http://[::ffff:127.0.0.1]/",
])
def test_private_ip_literals_are_rejected(url):
    with pytest.raises(InvalidInputError):
        netguard.check_url(url)


def test_public_ip_literal_is_allowed():
    netguard.check_url("https://93.184.216.34/")


def test_decimal_ipv4_literal_is_resolved_and_rejected(monkeypatch):
    """2130706433 == 127.0.0.1; the platform resolver turns it into that."""
    def fake(host, port):
        assert host == "2130706433"
        return ["127.0.0.1"]
    monkeypatch.setattr(netguard, "_resolve", fake)
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://2130706433/")


# ---------------------------------------------------------------- hostnames
def test_public_hostname_is_allowed(monkeypatch):
    _resolve_to(monkeypatch, PUBLIC)
    netguard.check_url("https://example.com/a")


def test_hostname_resolving_to_a_private_address_is_rejected(monkeypatch):
    _resolve_to(monkeypatch, "10.1.2.3")
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://intranet.example/")


def test_mixed_answers_are_rejected(monkeypatch):
    """One public + one private address is a rebinding attempt, not a public host."""
    _resolve_to(monkeypatch, PUBLIC, "127.0.0.1")
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://rebind.example/")


def test_localhost_is_rejected(monkeypatch):
    _resolve_to(monkeypatch, "127.0.0.1")
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://localhost:8080/")


def test_unresolvable_hostname_is_allowed_and_logged(monkeypatch, caplog):
    _resolve_fails(monkeypatch)
    with caplog.at_level("DEBUG", logger="universal_web_retrieval.netguard"):
        netguard.check_url("http://only-internal.example/")
    assert "only-internal.example" in caplog.text


def test_default_uses_the_platform_resolver():
    """No private DNS client: host-level resolution policy must still apply.

    Asserted structurally: ``_resolve`` must delegate to ``socket.getaddrinfo``,
    so /etc/hosts, systemd-resolved and host firewalling rules apply unchanged.
    Patching getaddrinfo instead would also redirect httpx, so this is a source
    assertion rather than a behavioural one.
    """
    import inspect

    source = inspect.getsource(netguard)      # the module file, not the patched attr
    assert "socket.getaddrinfo(" in source


# ---------------------------------------------------------------- opt-in / scheme
def test_private_access_is_opt_in(monkeypatch):
    monkeypatch.setenv("UWR_ALLOW_PRIVATE_NETWORK", "1")
    netguard.check_url("http://127.0.0.1:9222/")
    _resolve_to(monkeypatch, "10.1.2.3")
    netguard.check_url("http://intranet.example/")


def test_non_http_schemes_are_rejected():
    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"):
        with pytest.raises(InvalidInputError):
            netguard.check_url(url)


# ---------------------------------------------------------------- router wiring
class Fetcher(Provider):
    NAME = "fetcher"
    CAPABILITIES = frozenset({Capability.FETCH})
    TIMEOUT_CONFIG = staticmethod(lambda: 5.0)

    def __init__(self, *, final_url=None):
        self.calls = 0
        self._final_url = final_url

    def api_key(self):
        return ""

    def search(self, query, limit, timeout_override=None):
        raise AssertionError("not used")

    def fetch(self, url, timeout_override=None):
        self.calls += 1
        return FetchResult(url=self._final_url or url, content="body",
                           provider=self.NAME, mode=AuthMode.KEYLESS)


def test_router_rejects_a_private_url_before_any_provider_call(monkeypatch):
    provider = Fetcher()
    r = ProviderRouter(providers={"fetcher": provider})
    r._providers = {Capability.FETCH: [provider]}
    with pytest.raises(InvalidInputError):
        r.fetch("http://169.254.169.254/latest/meta-data/")
    assert provider.calls == 0


def test_router_rejects_a_redirect_to_a_private_host(monkeypatch):
    """A provider that reports an internal final URL fails closed for the caller."""
    provider = Fetcher(final_url="http://10.0.0.5/internal")
    r = ProviderRouter(providers={"fetcher": provider})
    r._providers = {Capability.FETCH: [provider]}
    with pytest.raises(ProviderError) as exc:
        r.fetch("https://example.com/a")
    assert "non-public" in str(exc.value).lower()


def test_router_allows_a_public_final_url(monkeypatch):
    provider = Fetcher(final_url="https://example.com/redirected")
    r = ProviderRouter(providers={"fetcher": provider})
    r._providers = {Capability.FETCH: [provider]}
    assert r.fetch("https://example.com/a").content == "body"


def test_web_fetch_tool_reports_a_private_url_as_a_per_url_error(monkeypatch):
    """A batch stays usable: the blocked URL is one error row, not a crash."""
    import json

    from universal_web_retrieval import server

    out = json.loads(server.web_fetch_tool(url="http://127.0.0.1:9222/json"))
    assert out[0]["url"] == "http://127.0.0.1:9222/json"
    assert out[0]["error"].startswith("invalid input:")
    assert "UWR_ALLOW_PRIVATE_NETWORK" in out[0]["error"]


def test_web_fetch_tool_allows_private_urls_when_opted_in(monkeypatch):
    import json

    from universal_web_retrieval import server

    monkeypatch.setenv("UWR_ALLOW_PRIVATE_NETWORK", "1")

    def boom(*args, **kwargs):
        raise ProviderError("would have fetched the intranet page")

    monkeypatch.setattr(server.router, "fetch", boom)
    out = json.loads(server.web_fetch_tool(url="http://127.0.0.1:9222/json"))
    assert "invalid input" not in out[0]["error"]
