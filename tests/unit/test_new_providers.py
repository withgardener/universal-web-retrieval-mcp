"""New providers: Tencent Yuanbao Search (MCP), Bocha, DeepSeek Search API.

Every request/response here is mocked — no live calls, no keys required.
"""
import json
import json as json_module
from unittest.mock import MagicMock, patch

import pytest

from universal_web_retrieval import config
from universal_web_retrieval.errors import Capability, ProviderError
from universal_web_retrieval.providers import registry as provider_registry
from universal_web_retrieval.providers.bocha import BochaProvider
from universal_web_retrieval.providers.deepseek import DeepSeekProvider
from universal_web_retrieval.providers.yuanbao import (YuanbaoProvider, clamp_query,
                                                       cnt_for_limit)


def _resp(payload=None, status=200, headers=None, text=None):
    m = MagicMock()
    m.status_code = status
    m.headers = headers if headers is not None else {"content-type": "application/json"}
    m.text = text if text is not None else json.dumps(payload if payload is not None else {})
    m.json.return_value = payload if payload is not None else {}
    return m


# ===================================================================== Yuanbao
PAGES = [
    json.dumps({"title": "T1", "url": "https://a.example/1", "date": "2026-01-01",
                "passage": "P1", "site": "Site1", "score": 0.91}),
    json.dumps({"title": "T2", "url": "https://b.example/2", "date": "2026-01-02",
                "content": "C2", "site": "Site2", "score": 0.5}),
]
SEARCH_PAYLOAD = {"Response": {"Pages": PAGES, "Query": "q", "Version": "standard",
                               "RequestId": "req-1"}}


class FakeMCP:
    """Minimal Streamable-HTTP MCP server stub (initialize / initialized / tools-call)."""

    def __init__(self, payload=None, *, session="sess-1", error=None, status=200,
                 sse=False, expire_session_once=False):
        self.payload = payload if payload is not None else SEARCH_PAYLOAD
        self.session = session
        self.error = error
        self.status = status
        self.sse = sse
        self.expire_session_once = expire_session_once
        self.calls = []

    def _tool_result(self):
        if self.error is not None:
            return {"jsonrpc": "2.0", "id": 3, "error": self.error}
        body = {"jsonrpc": "2.0", "id": 3,
                "result": {"content": [{"type": "text", "text": json.dumps(self.payload)}],
                           "isError": False}}
        return body

    def __call__(self, url, json=None, timeout=None, headers=None):  # noqa: A002
        body = json or {}   # provider passes the JSON-RPC envelope as `json=`
        method = body.get("method")
        self.calls.append({"url": url, "method": method, "body": body, "headers": headers})
        if method == "initialize":
            self.session = "sess-1"
            return _resp({"jsonrpc": "2.0", "id": 1,
                          "result": {"protocolVersion": "2025-06-18", "capabilities": {},
                                     "serverInfo": {"name": "yuanbao"}}},
                         headers={"content-type": "application/json",
                                  "mcp-session-id": "sess-1"})
        if method == "notifications/initialized":
            return _resp(None, status=202, text="")
        if method == "tools/call":
            if self.expire_session_once and headers and headers.get("Mcp-Session-Id") == "sess-1" \
                    and sum(1 for c in self.calls if c["method"] == "initialize") == 1:
                return _resp(None, status=404, text="session not found")
            if self.sse:
                text = "event: message\ndata: " + json_module.dumps(self._tool_result()) + "\n\n"
                return _resp(None, text=text,
                             headers={"content-type": "text/event-stream"})
            return _resp(self._tool_result(), status=self.status)
        raise AssertionError(f"unexpected method {method}")


class TestYuanbao:
    def setup_method(self):
        self.p = YuanbaoProvider()

    def test_cnt_rounds_up_to_allowed_buckets(self):
        assert cnt_for_limit(1) == "10"
        assert cnt_for_limit(5) == "10"
        assert cnt_for_limit(10) == "10"
        assert cnt_for_limit(11) == "20"
        assert cnt_for_limit(20) == "20"
        assert cnt_for_limit(21) == "30"
        assert cnt_for_limit(41) == "50"
        assert cnt_for_limit(99) == "50"
        assert cnt_for_limit(0) == "10"

    def test_clamp_query_to_1500_utf8_bytes(self):
        long = "客" * 600          # 1800 bytes
        clamped = clamp_query(long)
        assert len(clamped.encode("utf-8")) <= 1500
        assert len(clamped) == 500  # 1500 / 3, no split code point
        assert clamp_query("短查询") == "短查询"

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "wsa-test-key")
        fake = FakeMCP()
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake):
            self.p.search("人工智能", 5)

        init, notified, call = fake.calls[0], fake.calls[1], fake.calls[2]
        assert init["method"] == "initialize"
        assert init["body"]["params"]["protocolVersion"] == "2025-06-18"
        assert init["headers"]["Authorization"] == "Bearer wsa-test-key"
        assert "text/event-stream" in init["headers"]["Accept"]
        assert notified["method"] == "notifications/initialized"
        assert "id" not in notified["body"]

        assert call["method"] == "tools/call"
        assert call["url"] == "https://api.wsa.cloud.tencent.com/Mcp"
        assert call["body"]["params"]["name"] == "SearchPro"
        assert call["body"]["params"]["arguments"] == {"Query": "人工智能", "Cnt": "10"}
        assert call["headers"]["Mcp-Session-Id"] == "sess-1"

    def test_search_requests_do_not_hold_session_lock_during_http(self, monkeypatch):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        monkeypatch.setenv("YUANBAO_API_KEY", "concurrent-test-key")
        provider = YuanbaoProvider()
        provider._session_id = "session"
        provider._session_for = (config.yuanbao_mcp_url(), "concurrent-test-key")
        barrier = threading.Barrier(2)

        def post(url, json=None, timeout=None, headers=None):
            barrier.wait(timeout=0.4)
            payload = {"jsonrpc": "2.0", "id": json["id"],
                       "result": {"content": [{"type": "text",
                                                "text": json_module.dumps(SEARCH_PAYLOAD)}],
                                  "isError": False}}
            return _resp(payload)

        with patch("universal_web_retrieval.providers.yuanbao.httpx.post", side_effect=post):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda q: provider.search(q, 5), ("one", "two")))
        assert [result.provider for result in results] == ["yuanbao", "yuanbao"]

    def test_wsa_api_key_alias(self, monkeypatch):
        monkeypatch.setenv("WSA_API_KEY", "wsa-alias")
        fake = FakeMCP()
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake):
            self.p.search("q", 3)
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer wsa-alias"

    def test_response_mapping_and_truncation(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        fake = FakeMCP()
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake):
            result = self.p.search("q", 5)
        assert len(result.results) == 2
        first = result.results[0]
        assert first["title"] == "T1"
        assert first["url"] == "https://a.example/1"
        assert first["snippet"] == "P1"           # passage preferred
        assert first["published"] == "2026-01-01"
        assert first["site"] == "Site1"
        assert result.results[1]["snippet"] == "C2"  # content fallback
        assert result.provider == "yuanbao"
        assert result.mode.value == "keyed"

        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=FakeMCP()):
            assert len(self.p.search("q", 1).results) == 1

    def test_pages_as_dicts_are_tolerated(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        payload = {"Pages": [{"title": "T", "url": "https://x/1", "passage": "p"}]}
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post',
                   side_effect=FakeMCP(payload)):
            rows = self.p.search("q", 5).results
        assert rows[0]["url"] == "https://x/1"

    def test_sse_response_is_decoded(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post',
                   side_effect=FakeMCP(sse=True)):
            assert len(self.p.search("q", 5).results) == 2

    def test_session_is_reused_between_calls(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        fake = FakeMCP()
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake):
            self.p.search("q", 5)
            self.p.search("q2", 5)
        inits = [c for c in fake.calls if c["method"] == "initialize"]
        assert len(inits) == 1
        assert len([c for c in fake.calls if c["method"] == "tools/call"]) == 2

    def test_expired_session_triggers_one_reinitialize(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        fake = FakeMCP(expire_session_once=True)
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake):
            assert len(self.p.search("q", 5).results) == 2
        assert len([c for c in fake.calls if c["method"] == "initialize"]) == 2

    @pytest.mark.parametrize("code,needle", [
        ("UnauthorizedOperation", "unauthorized"),
        ("ResourceNotFound", "not activated"),
        ("ResourceUnavailable", "arrears"),
        ("RequestLimitExceeded", "rate limited"),
    ])
    def test_error_classification(self, monkeypatch, code, needle):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        fake = FakeMCP(error={"code": -32000, "message": f"{code}: failed"})
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        err = cm.value
        assert needle in str(err)
        if code == "UnauthorizedOperation":
            assert err.auth_failure is True
        else:
            assert err.auth_failure is False
        assert err.fallback_eligible is True   # every case still falls back

    def test_http_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")

        def fake_post(url, json=None, timeout=None, headers=None):
            if (json or {}).get("method") == "initialize":
                return _resp({"error": "nope"}, status=401)
            raise AssertionError("must not continue after a 401 handshake")

        with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=fake_post):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True

    def test_empty_pages_raises(self, monkeypatch):
        monkeypatch.setenv("YUANBAO_API_KEY", "k")
        with patch('universal_web_retrieval.providers.yuanbao.httpx.post',
                   side_effect=FakeMCP({"Response": {"Pages": []}})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)

    def test_fetch_not_supported(self):
        with pytest.raises(ProviderError):
            self.p.fetch("https://x")


# ===================================================================== Bocha
BOCHA_OK = {"code": 200, "log_id": "log-1", "msg": None,
            "data": {"_type": "SearchResponse",
                     "webPages": {"webSearchUrl": "https://bochaai.com/search?q=x",
                                  "totalEstimatedMatches": 2,
                                  "value": [
                                      {"name": "B1", "url": "https://b.example/1",
                                       "snippet": "short", "summary": "long summary",
                                       "siteName": "Site1",
                                       "datePublished": "2026-03-01T00:00:00+08:00"},
                                      {"name": "B2", "url": "https://b.example/2",
                                       "snippet": "only snippet", "summary": "",
                                       "siteName": "Site2", "datePublished": ""}]}}}


class TestBocha:
    def setup_method(self):
        self.p = BochaProvider()

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "bocha-key")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp(BOCHA_OK)) as post:
            self.p.search("博查测试", 5)
        args, kwargs = post.call_args
        assert args[0] == "https://api.bochaai.com/v1/web-search"
        assert kwargs["headers"]["Authorization"] == "Bearer bocha-key"
        assert kwargs["json"] == {"query": "博查测试", "summary": True,
                                  "freshness": "noLimit", "count": 5}

    def test_count_is_capped_at_50(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp(BOCHA_OK)) as post:
            self.p.search("q", 500)
        assert post.call_args.kwargs["json"]["count"] == 50

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        monkeypatch.setenv("UWR_BOCHA_BASE_URL", "https://api.bocha.cn/")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp(BOCHA_OK)) as post:
            self.p.search("q", 5)
        assert post.call_args.args[0] == "https://api.bocha.cn/v1/web-search"

    def test_response_mapping_prefers_summary(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp(BOCHA_OK)):
            result = self.p.search("q", 5)
        assert len(result.results) == 2
        assert result.results[0]["title"] == "B1"
        assert result.results[0]["snippet"] == "long summary"
        assert result.results[0]["site"] == "Site1"
        assert result.results[0]["published"].startswith("2026-03-01")
        assert result.results[1]["snippet"] == "only snippet"   # falls back

    def test_truncates_to_limit(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp(BOCHA_OK)):
            assert len(self.p.search("q", 1).results) == 1

    def test_http_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp({"msg": "bad key"}, status=401)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True

    def test_body_code_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp({"code": 401, "msg": "invalid api key"})):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True
        assert "invalid api key" in str(cm.value)

    def test_body_code_429_is_rate_limit(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp({"code": 429, "msg": "too many"})):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "rate limited" in str(cm.value)
        assert cm.value.auth_failure is False

    def test_empty_payload_raises(self, monkeypatch):
        monkeypatch.setenv("BOCHA_API_KEY", "k")
        with patch('universal_web_retrieval.providers.bocha.httpx.post',
                   return_value=_resp({"code": 200, "data": {"webPages": {"value": []}}})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)

    def test_fetch_not_supported(self):
        with pytest.raises(ProviderError):
            self.p.fetch("https://x")


# ===================================================================== DeepSeek
DS_OK = {"results": [
    {"rerank_score": 2.4, "title": "D1", "url": "https://d.example/1",
     "snippet": "short snip", "summary": "long summary", "content": "",
     "site_name": "Example News", "date": "2026-06-01T00:00:00+00:00", "authority": 3},
    {"rerank_score": 1.1, "title": "D2", "url": "https://d.example/2",
     "snippet": "", "summary": "only summary", "site_name": "Other", "date": None},
]}


class TestDeepSeek:
    def setup_method(self):
        self.p = DeepSeekProvider()

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)) as post:
            self.p.search("DeepSeek V4", 5)
        args, kwargs = post.call_args
        assert args[0] == "https://api.deepseek.com/v1/tools/search"
        assert kwargs["headers"]["Authorization"] == "Bearer ds-key"
        assert kwargs["json"] == {"query": "DeepSeek V4", "count": 5,
                                  "need_content": False,
                                  "ori_question": "DeepSeek V4"}
        assert "model" not in kwargs["json"]   # Search API takes no model

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setenv("UWR_DEEPSEEK_BASE_URL", "https://api.deepseek.test/")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)) as post:
            self.p.search("q", 2)
        assert post.call_args.args[0] == "https://api.deepseek.test/v1/tools/search"

    def test_response_mapping(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)):
            result = self.p.search("q", 5)
        assert [r["title"] for r in result.results] == ["D1", "D2"]
        assert result.results[0]["snippet"] == "short snip"
        assert result.results[0]["site"] == "Example News"
        assert result.results[0]["published"].startswith("2026-06-01")
        assert result.results[0]["score"] == 2.4
        assert result.results[1]["snippet"] == "only summary"
        assert result.results[1]["published"] == ""

    def test_count_capped_and_truncated(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)) as post:
            assert len(self.p.search("q", 500).results) == 2
        assert post.call_args.kwargs["json"]["count"] == 50

    def test_query_truncated_to_128_chars(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)) as post:
            self.p.search("x" * 300, 5)
        assert len(post.call_args.kwargs["json"]["query"]) == 128

    def test_search_uses_the_original_question_when_available(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        original_question = "Why did the database migration fail after the release?"
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)) as post:
            self.p.search("database migration failure", 5, original_question=original_question)
        assert post.call_args.kwargs["json"]["query"] == "database migration failure"
        assert post.call_args.kwargs["json"]["ori_question"] == original_question

    def test_ori_question_is_truncated_to_128_unicode_characters(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
        query = "🧪" * 140
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_OK)) as post:
            self.p.search(query, 5)
        body = post.call_args.kwargs["json"]
        assert body["query"] == query[:128]
        assert body["ori_question"] == query[:128]

    def test_402_is_balance_error_not_auth(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "Insufficient Balance"}}, status=402)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "insufficient balance" in str(cm.value).lower()
        assert cm.value.auth_failure is False
        assert cm.value.fallback_eligible is True

    def test_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "bad key"}}, status=401)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True

    def test_429_is_rate_limit(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "qps"}}, status=429)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "429" in str(cm.value)
        assert cm.value.auth_failure is False

    def test_empty_results_raises(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"results": []})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)

    # fetch() became a real capability in 0.2.0; the full request/response/error
    # matrix lives in tests/unit/test_more_providers.py.


# ===================================================================== registry
def test_new_providers_are_search_only_and_keyed():
    reg = provider_registry.registry()
    for name in ("yuanbao", "bocha", "zhipu", "brave"):
        provider = reg[name]
        assert provider.CAPABILITIES == frozenset({Capability.SEARCH}), name
        assert provider.KEYED_ONLY is True, name
        assert provider.KIND == "search", name
        assert provider.not_configured() is True, name
    # DeepSeek and Kimi ship both capabilities; both are keyed-only.
    for name in ("deepseek", "kimi"):
        assert reg[name].CAPABILITIES == frozenset({Capability.SEARCH, Capability.FETCH}), name
        assert reg[name].KEYED_ONLY is True, name
    # The keyless trio keeps its official behaviour and serves both chains.
    for name in ("anysearch", "tavily", "ddgs"):
        assert reg[name].CAPABILITIES == frozenset({Capability.SEARCH, Capability.FETCH})
        assert reg[name].KEYED_ONLY is False
        assert reg[name].is_configured() is True


def test_jina_is_keyless_fetch_only_but_not_in_any_default_chain():
    reg = provider_registry.registry()
    jina = reg["jina"]
    assert jina.CAPABILITIES == frozenset({Capability.FETCH})
    assert jina.KEYED_ONLY is False
    assert jina.KIND == "fetch"
    # Keyless providers are always "configured", which is exactly why a keyless
    # fetch link must never be in the defaults.
    assert jina.is_configured() is True
    assert "jina" not in config.DEFAULT_SEARCH_CHAIN
    assert "jina" not in config.DEFAULT_FETCH_CHAIN


def test_search_only_providers_are_not_in_the_fetch_chain():
    assert provider_registry.providers_for(Capability.FETCH) == [
        "anysearch", "tavily", "ddgs", "deepseek", "kimi", "jina"]


def test_new_providers_are_not_in_default_search_chain(monkeypatch):
    from universal_web_retrieval.providers.router import ProviderRouter
    names = ProviderRouter().resolve_names(Capability.SEARCH)
    assert names == ["anysearch", "tavily", "ddgs"]
    for name in ("yuanbao", "bocha", "deepseek", "kimi", "zhipu", "brave", "jina"):
        assert name not in names


def test_keyed_only_providers_never_hit_network_without_keys(monkeypatch):
    """A chain of keyed-only providers with no keys must not make any HTTP call."""
    from universal_web_retrieval.providers.router import ProviderRouter
    monkeypatch.setenv("UWR_SEARCH_CHAIN", "yuanbao,bocha,deepseek")

    def _boom(*args, **kwargs):
        raise AssertionError("no network call expected for unconfigured providers")

    with patch('universal_web_retrieval.providers.yuanbao.httpx.post', side_effect=_boom), \
         patch('universal_web_retrieval.providers.bocha.httpx.post', side_effect=_boom), \
         patch('universal_web_retrieval.providers.deepseek.httpx.post', side_effect=_boom):
        with pytest.raises(ProviderError) as cm:
            ProviderRouter().search("q", 5)
    outcomes = [a["outcome"] for a in cm.value.attempts]
    assert outcomes == ["skipped:not_configured"] * 3
