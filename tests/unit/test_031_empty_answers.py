"""0.3.1 — an OK provider answer with zero hits is an empty result, not an error.

Before 0.3.1 every provider raised ``ProviderError("no results")`` when a
well-formed reply contained no hits. That turned "the web has nothing for this
query" into ``all providers failed: ...`` and, in fan-out mode, into
``error:no results`` attempt entries — even though the merge layer, the tools'
docs and CHANGELOG all promised that an all-empty answer set is a successful
empty result.

Contract pinned here:

* A provider that receives a **well-formed success reply** with no rows returns
  ``SearchResult(results=[])``. An API-level error envelope or a malformed body
  is still a ``ProviderError`` (zhipu 1703 and friends stay errors: the provider
  told us it failed, we did not merely get nothing).
* Failover keeps walking after an empty answer (quality: a later provider may
  have hits), and returns the first empty answer as a SUCCESS when no provider
  produced hits.
* Fan-out counts an empty answer as OK, and it starts the grace clock.

No network: every provider here is patched at its ``httpx.post`` boundary or is a
pure stub.
"""
from __future__ import annotations

import json

import pytest

from universal_web_retrieval import config
from universal_web_retrieval.errors import (AuthMode, Capability, Provider,
                                            ProviderError, SearchResult)
from universal_web_retrieval.providers.router import ProviderRouter


# ---------------------------------------------------------------- stubs
class StubProvider(Provider):
    NAME = "stub"
    CAPABILITIES = frozenset({Capability.SEARCH, Capability.FETCH})
    TIMEOUT_CONFIG = staticmethod(lambda: 5.0)

    def __init__(self, name, *, rows=None, error=None, delay=0.0):
        self.NAME = name
        self._rows = [] if rows is None else rows
        self._error = error
        self._delay = delay
        self.calls = 0

    def api_key(self):
        return ""

    def search(self, query, limit, timeout_override=None):
        import time
        self.calls += 1
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return SearchResult(results=[dict(r) for r in self._rows], provider=self.NAME,
                            mode=self.mode())

    def fetch(self, url, timeout_override=None):
        raise AssertionError("not used")


def router_with(*providers):
    r = ProviderRouter(providers={p.NAME: p for p in providers})
    r._providers = {Capability.SEARCH: list(providers)}
    return r


def _row(name):
    return {"title": f"{name} hit", "url": f"https://{name}.example/x", "snippet": "s"}


# ---------------------------------------------------------------- providers
class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload)
        self.headers = {"content-type": "application/json"}
        self._payload = payload

    def json(self):
        return self._payload


def _anysearch(monkeypatch):
    from universal_web_retrieval.providers.anysearch import AnySearchProvider
    return AnySearchProvider(), "universal_web_retrieval.providers.anysearch.httpx.post", \
        {"code": 0, "data": {"results": []}}


def _tavily(monkeypatch):
    from universal_web_retrieval.providers.tavily import TavilyProvider
    return TavilyProvider(), "universal_web_retrieval.providers.tavily.httpx.post", \
        {"results": []}


def _bocha(monkeypatch):
    from universal_web_retrieval.providers.bocha import BochaProvider
    monkeypatch.setenv("BOCHA_API_KEY", "k")
    return BochaProvider(), "universal_web_retrieval.providers.bocha.httpx.post", \
        {"code": 200, "data": {"webPages": {"value": []}}}


def _deepseek(monkeypatch):
    from universal_web_retrieval.providers.deepseek import DeepSeekProvider
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    return DeepSeekProvider(), "universal_web_retrieval.providers.deepseek.httpx.post", \
        {"results": []}


def _kimi(monkeypatch):
    from universal_web_retrieval.providers.kimi import KimiProvider
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    return KimiProvider(), "universal_web_retrieval.providers.kimi.httpx.post", \
        {"search_results": []}


def _zhipu(monkeypatch):
    from universal_web_retrieval.providers.zhipu import ZhipuProvider
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    return ZhipuProvider(), "universal_web_retrieval.providers.zhipu.httpx.post", \
        {"search_result": []}


def _brave(monkeypatch):
    from universal_web_retrieval.providers.brave import BraveProvider
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    return BraveProvider(), "universal_web_retrieval.providers.brave.httpx.get", \
        {"web": {"results": []}}


@pytest.mark.parametrize("factory,target", [
    (_anysearch, "anysearch"),
    (_tavily, "tavily"),
    (_bocha, "bocha"),
    (_deepseek, "deepseek"),
    (_kimi, "kimi"),
    (_zhipu, "zhipu"),
    (_brave, "brave"),
])
def test_provider_with_no_rows_returns_empty_answer(factory, target, monkeypatch):
    provider, patch_target, payload = factory(monkeypatch)
    import unittest.mock as mock
    with mock.patch(patch_target, return_value=_Resp(payload)):
        result = provider.search("nothing matches this", 5)
    assert isinstance(result, SearchResult)
    assert result.results == []
    assert result.provider == provider.NAME


def test_ddgs_with_no_rows_returns_empty_answer():
    import unittest.mock as mock
    from universal_web_retrieval.providers.ddgs import DDGSProvider

    class FakeDDGS:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results=5):
            return []

    with mock.patch("ddgs.DDGS", FakeDDGS):
        result = DDGSProvider().search("q", 5)
    assert result.results == []


def test_yuanbao_with_no_pages_returns_empty_answer(monkeypatch):
    from universal_web_retrieval.providers.yuanbao import YuanbaoProvider
    monkeypatch.setenv("WSA_API_KEY", "k")
    provider = YuanbaoProvider()
    provider._call_tool = lambda arguments, timeout, _p=provider: {
        "content": [{"type": "text",
                     "text": json.dumps({"Response": {"Pages": []}})}],
        "isError": False,
    }
    result = provider.search("q", 5)
    assert result.results == []


def test_provider_error_envelope_is_still_an_error(monkeypatch):
    """An explicit API failure is not an empty answer."""
    from universal_web_retrieval.providers.zhipu import ZhipuProvider
    monkeypatch.setenv("ZHIPU_API_KEY", "k")
    import unittest.mock as mock
    with mock.patch("universal_web_retrieval.providers.zhipu.httpx.post",
                    return_value=_Resp({"error": {"code": "1703",
                                                  "message": "搜索引擎未返回有效数据"}})):
        with pytest.raises(ProviderError):
            ZhipuProvider().search("q", 5)


# ---------------------------------------------------------------- failover
def test_empty_answer_does_not_stop_the_failover_walk():
    empty = StubProvider("empty")
    good = StubProvider("good", rows=[_row("good")])
    result = router_with(empty, good).search("q", 5)
    assert result.provider == "good"
    assert empty.calls == 1


def test_all_empty_answers_is_a_successful_empty_result():
    a = StubProvider("a")
    b = StubProvider("b")
    result = router_with(a, b).search("q", 5)
    assert result.results == []
    assert result.provider == "a"          # first provider that answered OK
    assert b.calls == 1


def test_empty_answer_is_preferred_over_a_hard_chain_failure():
    a = StubProvider("a")
    b = StubProvider("b", error=ProviderError("HTTP 500 upstream error"))
    result = router_with(a, b).search("q", 5)
    assert result.results == []
    assert result.provider == "a"


def test_attempts_trail_names_empty_answers():
    a = StubProvider("a")
    b = StubProvider("b", rows=[_row("b")])
    payload = router_with(a, b).search("q", 5).to_dict()
    assert payload["attempts"] == [{"provider": "a", "outcome": "empty"},
                                   {"provider": "b", "outcome": "ok"}]


def test_empty_answer_does_not_hide_a_later_failure():
    """Every link failing is still a failure: empty answers aside, nothing answered."""
    a = StubProvider("a", error=ProviderError("HTTP 429 rate limited"))
    b = StubProvider("b", error=ProviderError("HTTP 500 upstream error"))
    with pytest.raises(ProviderError) as exc:
        router_with(a, b).search("q", 5)
    assert "all providers failed" in str(exc.value)


def test_not_configured_only_chain_still_fails():
    a = StubProvider("a")
    a.KEYED_ONLY = True
    a.api_key = lambda: ""
    with pytest.raises(ProviderError):
        router_with(a).search("q", 5)


# ---------------------------------------------------------------- fan-out
def test_fanout_empty_answer_is_ok_and_starts_the_grace_clock(monkeypatch):
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0.3")
    fast = StubProvider("fast")
    slow = StubProvider("slow", rows=[_row("slow")], delay=1.0)
    result = router_with(fast, slow).search("q", 5, mode="fanout")
    assert result.results == []
    outcomes = {a["provider"]: a["outcome"] for a in result.attempts}
    assert outcomes["fast"] == "ok"
    assert outcomes["slow"].startswith("skipped:")


def test_fanout_counts_empty_and_hits_together(monkeypatch):
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0")
    a = StubProvider("a")
    b = StubProvider("b", rows=[_row("b")])
    result = router_with(a, b).search("q", 5, mode="fanout")
    assert [r["url"] for r in result.results] == ["https://b.example/x"]
    outcomes = {x["provider"]: x["outcome"] for x in result.attempts}
    assert outcomes == {"a": "ok", "b": "ok"}


def test_fanout_records_the_auth_mode_of_each_ok_answer(monkeypatch):
    """Metadata: an ok attempt entry says which auth mode answered it."""
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0")
    a = StubProvider("a")
    result = router_with(a).search("q", 5, mode="fanout")
    assert result.attempts == [{"provider": "a", "outcome": "ok", "results": 0,
                                "latency_ms": result.attempts[0]["latency_ms"],
                                "mode": AuthMode.KEYLESS.value}]
