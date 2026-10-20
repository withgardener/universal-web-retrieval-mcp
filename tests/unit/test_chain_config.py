"""Configurable chain: env parsing, enable/disable, per-call override, attempts,
and list_providers redaction. No network."""
import json
import logging
import time

import pytest

from universal_web_retrieval import config, server
from universal_web_retrieval.errors import (AuthMode, Capability, FetchResult,
                                            InvalidInputError, Provider,
                                            ProviderError, SearchResult)
from universal_web_retrieval.providers.router import ProviderRouter


# ---------------------------------------------------------------- helpers
class StubProvider(Provider):
    """Minimal real Provider (not a Mock) so capability/keyed checks behave."""

    def __init__(self, name, *, keyed_only=False, key="", caps=(Capability.SEARCH,),
                 error=None, kind="search"):
        self.NAME = name
        self.KEYED_ONLY = keyed_only
        self.CAPABILITIES = frozenset(caps)
        self.KIND = kind
        self._key = key
        self._error = error
        self.calls = 0

    def api_key(self):
        return self._key

    def search(self, query, limit, timeout_override=None):
        self.calls += 1
        if self._error:
            raise self._error
        return SearchResult(results=[{"title": "t", "url": "u", "snippet": "s"}],
                            provider=self.NAME, mode=self.mode())

    def fetch(self, url, timeout_override=None):
        self.calls += 1
        if self._error:
            raise self._error
        return FetchResult(url=url, content="c", provider=self.NAME, mode=self.mode())


def router_with(*providers):
    return ProviderRouter(providers={p.NAME: p for p in providers})


# ---------------------------------------------------------------- defaults
def test_defaults_match_v01_behaviour():
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH) == ["anysearch", "tavily", "ddgs"]
    assert r.resolve_names(Capability.FETCH) == ["anysearch", "tavily", "ddgs"]
    assert config.DEFAULT_SEARCH_CHAIN == ("anysearch", "tavily", "ddgs")
    assert config.DEFAULT_FETCH_CHAIN == ("anysearch", "tavily", "ddgs")


# ---------------------------------------------------------------- env chains
def test_env_chain_order_is_preserved(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "bocha, anysearch ,DDGS")
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH) == ["bocha", "anysearch", "ddgs"]


def test_env_chains_are_independent(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "yuanbao,ddgs")
    monkeypatch.setenv("UWR_FETCH_CHAIN", "anysearch")
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH) == ["yuanbao", "ddgs"]
    assert r.resolve_names(Capability.FETCH) == ["anysearch"]


def test_unknown_names_dropped_with_one_warning(monkeypatch, caplog):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "nosuch1,anysearch")
    r = ProviderRouter()
    with caplog.at_level(logging.WARNING, logger="universal_web_retrieval.router"):
        assert r.resolve_names(Capability.SEARCH) == ["anysearch"]
        # same chain resolved again -> no second warning
        assert r.resolve_names(Capability.SEARCH) == ["anysearch"]
    warnings = [rec for rec in caplog.records if "nosuch1" in rec.getMessage()]
    assert len(warnings) == 1, [rec.getMessage() for rec in caplog.records]
    assert "unknown provider" in warnings[0].getMessage()


def test_provider_without_capability_is_dropped(monkeypatch, caplog):
    monkeypatch.setenv("UWR_FETCH_CHAIN", "yuanbao,nosuchcap,ddgs")
    r = ProviderRouter()
    with caplog.at_level(logging.WARNING, logger="universal_web_retrieval.router"):
        assert r.resolve_names(Capability.FETCH) == ["ddgs"]
    text = " ".join(rec.getMessage() for rec in caplog.records)
    assert "yuanbao (no fetch capability)" in text


def test_disabled_providers_are_removed(monkeypatch, caplog):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "anysearch,tavily,ddgs")
    monkeypatch.setenv("UWR_DISABLED_PROVIDERS", "tavily")
    r = ProviderRouter()
    with caplog.at_level(logging.WARNING, logger="universal_web_retrieval.router"):
        assert r.resolve_names(Capability.SEARCH) == ["anysearch", "ddgs"]
        assert r.resolve_names(Capability.FETCH) == ["anysearch", "ddgs"]
    assert "disabled by UWR_DISABLED_PROVIDERS" in " ".join(
        rec.getMessage() for rec in caplog.records)


def test_disabled_applies_to_default_chain_too(monkeypatch):
    monkeypatch.setenv("UWR_DISABLED_PROVIDERS", "anysearch,ddgs")
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH) == ["tavily"]


def test_duplicate_names_collapse(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "anysearch,anysearch,ddgs")
    assert ProviderRouter().resolve_names(Capability.SEARCH) == ["anysearch", "ddgs"]


@pytest.mark.parametrize("value", ["", "   ", ",,,", " , "])
def test_empty_env_chain_falls_back_to_default(monkeypatch, value):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", value)
    assert ProviderRouter().resolve_names(Capability.SEARCH) == ["anysearch", "tavily", "ddgs"]


def test_fully_invalid_env_chain_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "nosuch2,nosuch3")
    assert ProviderRouter().resolve_names(Capability.SEARCH) == ["anysearch", "tavily", "ddgs"]


def test_everything_disabled_yields_empty_chain(monkeypatch):
    monkeypatch.setenv("UWR_DISABLED_PROVIDERS", "anysearch,tavily,ddgs")
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH) == []
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5)
    assert "all providers failed" in str(cm.value)


# ---------------------------------------------------------------- per-call override
def test_per_call_override_reorders(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "anysearch,tavily,ddgs")
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH, ["ddgs", "anysearch"]) == ["ddgs", "anysearch"]


def test_per_call_override_single_provider(monkeypatch):
    monkeypatch.setenv("BOCHA_API_KEY", "k")
    r = ProviderRouter()
    assert r.resolve_names(Capability.SEARCH, ["bocha"]) == ["bocha"]


def test_per_call_override_rejects_empty_list():
    with pytest.raises(InvalidInputError) as cm:
        ProviderRouter().chain(Capability.SEARCH, [])
    assert "none of the requested providers" in str(cm.value)


def test_per_call_override_rejects_unknown_names():
    with pytest.raises(InvalidInputError) as cm:
        ProviderRouter().chain(Capability.SEARCH, ["nope"])
    assert "none of the requested providers" in str(cm.value)


def test_per_call_override_rejects_wrong_capability():
    with pytest.raises(InvalidInputError) as cm:
        ProviderRouter().chain(Capability.FETCH, ["yuanbao"])
    assert "available:" in str(cm.value)


def test_per_call_override_still_respects_disabled(monkeypatch):
    monkeypatch.setenv("UWR_DISABLED_PROVIDERS", "bocha")
    with pytest.raises(InvalidInputError):
        ProviderRouter().chain(Capability.SEARCH, ["bocha"])


def test_web_search_tool_surfaces_invalid_override():
    out = json.loads(server.web_search_tool("q", 5, providers=["nope"]))
    assert out["success"] is False
    assert "invalid input" in out["error"]


# ---------------------------------------------------------------- attempts
def test_attempts_trail_on_success():
    bad = StubProvider("bad", keyed_only=True, key="x", error=ProviderError("HTTP 429 rate limited"))
    good = StubProvider("good")
    r = router_with(bad, good)
    result = r.search("q", 5, providers=["bad", "good"])
    assert result.provider == "good"
    assert result.attempts[0]["provider"] == "bad"
    assert result.attempts[0]["outcome"].startswith("error:")
    assert result.attempts[-1] == {"provider": "good", "outcome": "ok"}
    payload = result.to_dict()
    assert payload["provider"] == "good"
    assert payload["mode"] == "keyless"
    assert "latency_ms" in payload
    assert [a["outcome"] for a in payload["attempts"]][-1] == "ok"


def test_attempts_reported_when_chain_fails():
    a = StubProvider("a", error=ProviderError("boom"))
    b = StubProvider("b", error=ProviderError("bang"))
    r = router_with(a, b)
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5, providers=["a", "b"])
    attempts = cm.value.attempts
    assert [x["provider"] for x in attempts] == ["a", "b"]
    assert all(x["outcome"].startswith("error:") for x in attempts)


def test_not_configured_skip_is_silent_and_never_calls_provider():
    # KEYED_ONLY without a key must never reach the network.
    unkeyed = StubProvider("bocha", keyed_only=True, key="")
    ok = StubProvider("ok")
    r = router_with(unkeyed, ok)
    result = r.search("q", 5, providers=["bocha", "ok"])
    assert unkeyed.calls == 0
    assert result.attempts[0] == {"provider": "bocha", "outcome": "skipped:not_configured"}
    assert result.provider == "ok"


def test_keyed_only_with_key_is_attempted():
    keyed = StubProvider("bocha", keyed_only=True, key="secret")
    r = router_with(keyed)
    result = r.search("q", 5, providers=["bocha"])
    assert keyed.calls == 1
    assert result.mode == AuthMode.KEYED
    assert result.attempts == [{"provider": "bocha", "outcome": "ok"}]


def test_keyless_providers_always_attempted():
    anysearch = StubProvider("anysearch", keyed_only=False, key="")
    r = router_with(anysearch)
    assert isinstance(r.search("q", 5).provider, str)
    assert anysearch.calls == 1


def test_deadline_skip_is_recorded():
    p = StubProvider("slow")
    r = router_with(p)
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5, providers=["slow"], deadline=time.monotonic() - 1)
    assert cm.value.attempts == [{"provider": "slow", "outcome": "skipped:deadline"}]
    assert p.calls == 0


def test_auth_failure_is_still_loud_and_falls_back(caplog):
    bad = StubProvider("bad", error=ProviderError("HTTP 401: bad key", auth_failure=True))
    good = StubProvider("good")
    r = router_with(bad, good)
    with caplog.at_level(logging.ERROR, logger="universal_web_retrieval.router"):
        assert r.search("q", 5, providers=["bad", "good"]).provider == "good"
    assert any("AUTH FAILURE on bad" in rec.getMessage() for rec in caplog.records)


def test_non_eligible_error_aborts_chain():
    fatal = StubProvider("fatal", error=ProviderError("bad request", fallback_eligible=False))
    later = StubProvider("later")
    r = router_with(fatal, later)
    with pytest.raises(ProviderError):
        r.search("q", 5, providers=["fatal", "later"])
    assert later.calls == 0


def test_fetch_attempts_and_extra_metadata():
    p = StubProvider("solo", caps=(Capability.SEARCH, Capability.FETCH))
    r = router_with(p)
    out = r.fetch("https://example.com", providers=["solo"]).to_dict()
    assert out["provider"] == "solo"
    assert out["attempts"] == [{"provider": "solo", "outcome": "ok"}]


def test_search_result_extra_fields_pass_through():
    p = StubProvider("solo")
    p.search = lambda q, l, timeout_override=None: SearchResult(
        results=[{"title": "t", "url": "u", "snippet": "s", "published": "2026-01-01",
                  "site": "Example", "score": 0.8}],
        provider="solo", mode=AuthMode.KEYED)
    row = p.search("q", 1).to_dict()["results"][0]
    assert row["published"] == "2026-01-01"
    assert row["site"] == "Example"
    assert row["score"] == 0.8


# ---------------------------------------------------------------- list_providers
def test_list_providers_reports_positions_and_never_leaks_keys(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "anysearch,tavily,yuanbao,bocha,ddgs")
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as-super-secret-value")
    monkeypatch.setenv("BOCHA_API_KEY", "bocha-super-secret-value")
    raw = server.list_providers_tool()
    assert "as-super-secret-value" not in raw
    assert "bocha-super-secret-value" not in raw

    payload = json.loads(raw)
    assert payload["chains"]["search"] == ["anysearch", "tavily", "yuanbao", "bocha", "ddgs"]
    by_name = {row["name"]: row for row in payload["providers"]}

    assert by_name["bocha"]["position"] == {"search": 4}
    assert by_name["bocha"]["configured"] is True
    assert by_name["bocha"]["auth_mode"] == "keyed"
    assert by_name["yuanbao"]["position"] == {"search": 3}
    assert by_name["yuanbao"]["configured"] is False
    assert by_name["yuanbao"]["auth_mode"] is None
    assert by_name["yuanbao"]["keyed_only"] is True
    assert by_name["anysearch"]["position"] == {"search": 1, "fetch": 1}
    # DeepSeek is not in the default chain: it advertises the capability but has
    # no position, which is reported as an explicit null (not an omitted key).
    assert by_name["deepseek"]["position"] == {"search": None, "fetch": None}
    assert by_name["deepseek"]["capabilities"] == ["fetch", "search"]


def test_list_providers_reports_disabled(monkeypatch):
    monkeypatch.setenv("UWR_DISABLED_PROVIDERS", "tavily,bocha")
    payload = json.loads(server.list_providers_tool())
    by_name = {row["name"]: row for row in payload["providers"]}
    assert by_name["tavily"]["enabled"] is False
    assert by_name["bocha"]["enabled"] is False
    assert by_name["anysearch"]["enabled"] is True
    assert payload["disabled"] == ["bocha", "tavily"]
    assert "tavily" not in payload["chains"]["search"]


def test_list_providers_lists_every_known_provider():
    payload = json.loads(server.list_providers_tool())
    names = [row["name"] for row in payload["providers"]]
    assert names == ["anysearch", "tavily", "ddgs", "yuanbao", "bocha", "deepseek",
                     "kimi", "zhipu", "brave", "jina"]
    for row in payload["providers"]:
        assert set(row["capabilities"]) <= {"search", "fetch"}
        assert row["kind"] in {"search", "fetch", "answer"}


def test_server_info_version_matches_package_version():
    from universal_web_retrieval.server import app
    assert app.version == "0.2.0"


def test_empty_provider_list_is_invalid_on_search_and_fetch_tools(monkeypatch):
    monkeypatch.setenv("ANYSEARCH_API_KEY", "must-not-be-used")
    monkeypatch.setattr(server, "router", ProviderRouter())
    search = json.loads(server.web_search_tool("q", providers=[]))
    fetch = json.loads(server.web_fetch_tool(url="https://example.com", providers=[]))
    assert search["success"] is False
    assert search["error"].startswith("invalid input:")
    assert fetch["error"].startswith("invalid input:")


def test_web_search_tool_response_shape(monkeypatch):
    """web_search keeps provider/mode/latency_ms and adds attempts."""
    monkeypatch.setenv("BOCHA_API_KEY", "k")
    r = router_with(StubProvider("solo", keyed_only=True, key="k"))
    monkeypatch.setattr(server, "router", r)
    out = json.loads(server.web_search_tool("q", 3, providers=["solo"]))
    assert out["provider"] == "solo"
    assert out["mode"] == "keyed"
    assert isinstance(out["latency_ms"], int)
    assert out["attempts"] == [{"provider": "solo", "outcome": "ok"}]


def test_web_fetch_tool_reports_attempts(monkeypatch):
    r = router_with(StubProvider("solo", caps=(Capability.SEARCH, Capability.FETCH)))
    monkeypatch.setattr(server, "router", r)
    out = json.loads(server.web_fetch_tool(url="https://example.com", providers=["solo"]))
    assert out[0]["provider"] == "solo"
    assert out[0]["attempts"] == [{"provider": "solo", "outcome": "ok"}]
