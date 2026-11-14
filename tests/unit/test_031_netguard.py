"""0.3.1 — private-network blocking is opt-in; the default is 0.3.0 behaviour.

``web_fetch`` has always fetched whatever the host could reach, including
intranet and ``localhost`` pages. That is a deliberate capability of a local
agent tool, so 0.3.1 keeps it as the default and adds a *pre-flight* check
behind ``WEB_RETRIEVAL_BLOCK_PRIVATE=1`` — the name README has promised since
0.1.0 — for deployments that want it.

What the check can prove, and only that:

* an **IP literal** (bracketed IPv6, IPv4-mapped IPv6, decimal ``2130706433``)
  is decided exactly, with no DNS;
* a **hostname** is resolved through the platform resolver (so ``/etc/hosts``,
  systemd-resolved and host-level controls still apply) and rejected when ANY
  returned address is non-public — mixed answers included;
* a hostname that does not resolve here is allowed and logged, because the
  fetch happens at the provider and our resolver's failure proves nothing.

What it cannot prove, and is therefore never claimed: the fetch runs on the
provider's network, so there is no connection binding. A redirect hop is only
re-checked after it happened, and DNS rebinding between our check and the
provider's connect cannot be excluded. Network isolation stays the outer
boundary. These tests pin the honest version: no test asserts SSRF protection,
and ``test_docs_do_not_overclaim`` refuses that wording in the docs.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from universal_web_retrieval import config, netguard
from universal_web_retrieval.errors import (AuthMode, Capability, FetchResult,
                                            InvalidInputError, Provider,
                                            ProviderError)
from universal_web_retrieval.providers.router import ProviderRouter

PUBLIC = "93.184.216.34"
REPO = Path(__file__).resolve().parents[2]

PRIVATE_URLS = [
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
]


class Fetcher(Provider):
    NAME = "fetcher"
    CAPABILITIES = frozenset({Capability.FETCH})
    TIMEOUT_CONFIG = staticmethod(lambda: 5.0)

    def __init__(self, *, final_url=None):
        self.calls = []
        self._final_url = final_url

    def api_key(self):
        return ""

    def search(self, query, limit, timeout_override=None):
        raise AssertionError("not used")

    def fetch(self, url, timeout_override=None):
        self.calls.append(url)
        return FetchResult(url=self._final_url or url, content="body",
                           provider=self.NAME, mode=AuthMode.KEYLESS)


def _router(provider):
    r = ProviderRouter(providers={"fetcher": provider})
    r._providers = {Capability.FETCH: [provider]}
    return r


def _resolve_to(monkeypatch, *addresses):
    monkeypatch.setattr(netguard, "_resolve", lambda host, port: list(addresses))


def _resolve_fails(monkeypatch):
    def fake(host, port):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(netguard, "_resolve", fake)


def _no_dns(monkeypatch):
    def fake(host, port):
        raise AssertionError(f"the default path must not resolve {host!r}")
    monkeypatch.setattr(netguard, "_resolve", fake)


# ------------------------------------------------------------------ the default
@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_default_allows_private_targets_without_any_dns(monkeypatch, url):
    """0.3.0 behaviour is the default: a private URL passes, no resolver call."""
    _no_dns(monkeypatch)
    netguard.check_url(url)


def test_default_config_is_off():
    assert config.block_private_network() is False


def test_netguard_is_inert_when_off(monkeypatch):
    """Scheme validation stays with the router; netguard adds nothing by default."""
    _no_dns(monkeypatch)
    netguard.check_url("file:///etc/passwd")


def test_default_router_sends_the_private_url_unchanged(monkeypatch):
    _no_dns(monkeypatch)
    provider = Fetcher()
    assert _router(provider).fetch("http://127.0.0.1:9222/json").content == "body"
    assert provider.calls == ["http://127.0.0.1:9222/json"]


def test_default_router_returns_a_private_final_url(monkeypatch):
    """A redirect to an intranet host is 0.3.0 behaviour, not an error."""
    _no_dns(monkeypatch)
    provider = Fetcher(final_url="http://10.0.0.5/internal")
    result = _router(provider).fetch("https://example.com/a")
    assert result.url == "http://10.0.0.5/internal"


# ------------------------------------------------------------- the opt-in switch
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " block ", "enabled"])
def test_opt_in_values(monkeypatch, value):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", value)
    assert config.block_private_network() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "  "])
def test_opt_out_values(monkeypatch, value):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", value)
    assert config.block_private_network() is False


def test_unset_opt_in_is_off(monkeypatch):
    monkeypatch.delenv("WEB_RETRIEVAL_BLOCK_PRIVATE", raising=False)
    assert config.block_private_network() is False


def test_legacy_allow_variable_does_not_enable_blocking(monkeypatch):
    """The abandoned ``UWR_ALLOW_PRIVATE_NETWORK`` must not be a hidden switch."""
    monkeypatch.delenv("WEB_RETRIEVAL_BLOCK_PRIVATE", raising=False)
    monkeypatch.setenv("UWR_ALLOW_PRIVATE_NETWORK", "1")
    assert config.block_private_network() is False
    _no_dns(monkeypatch)
    netguard.check_url("http://127.0.0.1:9222/json")


# ---------------------------------------------------------------- blocking it is
@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_private_ip_literals_are_rejected_when_blocking(monkeypatch, url):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    with pytest.raises(InvalidInputError):
        netguard.check_url(url)


def test_public_ip_literal_is_allowed_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    netguard.check_url("https://93.184.216.34/")


def test_decimal_ipv4_literal_is_rejected_when_blocking(monkeypatch):
    """2130706433 == 127.0.0.1; the platform resolver turns it into that."""
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")

    def fake(host, port):
        assert host == "2130706433"
        return ["127.0.0.1"]
    monkeypatch.setattr(netguard, "_resolve", fake)
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://2130706433/")


def test_public_hostname_is_allowed_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_to(monkeypatch, PUBLIC)
    netguard.check_url("https://example.com/a")


def test_hostname_resolving_private_is_rejected_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_to(monkeypatch, "10.1.2.3")
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://intranet.example/")


def test_mixed_answers_are_rejected_when_blocking(monkeypatch):
    """One public + one private address is what a rebinding attempt looks like here."""
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_to(monkeypatch, PUBLIC, "127.0.0.1")
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://rebind.example/")


def test_localhost_is_rejected_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_to(monkeypatch, "127.0.0.1")
    with pytest.raises(InvalidInputError):
        netguard.check_url("http://localhost:8080/")


def test_unresolvable_hostname_is_allowed_and_logged_when_blocking(monkeypatch, caplog):
    """Documented residual gap: an internal-only name is not detectable here."""
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_fails(monkeypatch)
    with caplog.at_level("DEBUG", logger="universal_web_retrieval.netguard"):
        netguard.check_url("http://only-internal.example/")
    assert "only-internal.example" in caplog.text


def test_error_names_the_opt_in_variable(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    with pytest.raises(InvalidInputError) as exc:
        netguard.check_url("http://127.0.0.1:9222/json")
    assert "WEB_RETRIEVAL_BLOCK_PRIVATE" in str(exc.value)


def test_non_http_schemes_are_rejected_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"):
        with pytest.raises(InvalidInputError):
            netguard.check_url(url)


def test_default_uses_the_platform_resolver():
    """No private DNS client: host-level resolution policy must still apply."""
    import inspect

    assert "socket.getaddrinfo(" in inspect.getsource(netguard)


# ------------------------------------------------------------- router, opt-in on
def test_router_rejects_before_any_provider_call_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    provider = Fetcher()
    with pytest.raises(InvalidInputError):
        _router(provider).fetch("http://169.254.169.254/latest/meta-data/")
    assert provider.calls == []


def test_router_rejects_a_private_redirect_when_blocking(monkeypatch):
    """Re-check of the reported final URL fails closed — after the hop happened."""
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_to(monkeypatch, PUBLIC)
    provider = Fetcher(final_url="http://10.0.0.5/internal")
    with pytest.raises(ProviderError) as exc:
        _router(provider).fetch("https://example.com/a")
    assert "non-public" in str(exc.value).lower()


def test_router_allows_a_public_redirect_when_blocking(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    _resolve_to(monkeypatch, PUBLIC)
    provider = Fetcher(final_url="https://example.com/redirected")
    assert _router(provider).fetch("https://example.com/a").content == "body"


def test_list_providers_reports_the_policy(monkeypatch):
    from universal_web_retrieval import server

    policy = json.loads(server.list_providers_tool())["fetch_policy"]
    assert policy["block_private_network"] is False
    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    policy = json.loads(server.list_providers_tool())["fetch_policy"]
    assert policy["block_private_network"] is True


def test_web_fetch_tool_reports_a_blocked_url_as_a_per_url_error(monkeypatch):
    """A batch stays usable: the blocked URL is one error row, not a crash."""
    from universal_web_retrieval import server

    monkeypatch.setenv("WEB_RETRIEVAL_BLOCK_PRIVATE", "1")
    out = json.loads(server.web_fetch_tool(url="http://127.0.0.1:9222/json"))
    assert out[0]["url"] == "http://127.0.0.1:9222/json"
    assert out[0]["error"].startswith("invalid input:")
    assert "WEB_RETRIEVAL_BLOCK_PRIVATE" in out[0]["error"]


def test_web_fetch_tool_still_fetches_private_urls_by_default(monkeypatch):
    from universal_web_retrieval import server

    def boom(*args, **kwargs):
        raise ProviderError("would have fetched the intranet page")

    monkeypatch.setattr(server.router, "fetch", boom)
    out = json.loads(server.web_fetch_tool(url="http://127.0.0.1:9222/json"))
    assert "invalid input" not in out[0]["error"]


# ------------------------------------------------------------------------ docs
def test_docs_do_not_overclaim():
    """The check cannot stop DNS rebinding or a redirect hop; say so plainly."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    server_src = (REPO / "src" / "universal_web_retrieval" / "server.py").read_text(
        encoding="utf-8")

    assert "WEB_RETRIEVAL_BLOCK_PRIVATE=1" in readme
    assert "UWR_ALLOW_PRIVATE_NETWORK" not in readme
    assert "UWR_ALLOW_PRIVATE_NETWORK" not in server_src

    lowered = readme.lower()
    assert "off by default" in lowered
    assert "dns rebinding" in lowered
    assert "redirect" in lowered
    assert "network isolation" in lowered or "container/netns/firewall" in lowered
    assert "not ssrf protection" in lowered

    for claim in ("prevents ssrf", "protects against ssrf", "blocks ssrf",
                  "ssrf-proof", "prevents dns rebinding", "full ssrf protection"):
        assert claim not in lowered, f"overclaim in README: {claim!r}"

    changelog_lower = changelog.lower()
    assert "WEB_RETRIEVAL_BLOCK_PRIVATE" in changelog
    assert "off by default" in changelog_lower
    assert "dns rebinding" in changelog_lower
