"""0.2.0 provider additions: DeepSeek Fetch, Kimi, Zhipu, Brave, Jina Reader.

Every request and response here is mocked — no live calls, no keys required.
Contracts under test come from the official docs quoted in each provider module:

  DeepSeek fetch : https://api-docs.deepseek.com/guides/search
  Kimi           : https://platform.kimi.ai/docs/api/tools-search.md
                   https://platform.kimi.ai/docs/api/tools-search-pro.md
                   https://platform.kimi.ai/docs/api/tools-fetch.md
  Zhipu          : https://docs.bigmodel.cn/api-reference/工具-api/网络搜索
  Brave          : https://api-dashboard.search.brave.com/api-reference/web/search/get
  Jina Reader    : https://jina.ai/reader + https://docs.jina.ai/openapi.json
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from universal_web_retrieval import config, server
from universal_web_retrieval.errors import Capability, ProviderError
from universal_web_retrieval.providers.brave import BraveProvider
from universal_web_retrieval.providers.deepseek import DeepSeekProvider
from universal_web_retrieval.providers.jina import JinaProvider
from universal_web_retrieval.providers.kimi import KimiProvider
from universal_web_retrieval.providers.router import ProviderRouter
from universal_web_retrieval.providers.zhipu import ZhipuProvider


def _resp(payload=None, status=200, headers=None, text=None, json_raises=False):
    m = MagicMock()
    m.status_code = status
    m.headers = headers if headers is not None else {"content-type": "application/json"}
    m.text = text if text is not None else json.dumps(payload if payload is not None else {})
    if json_raises:
        m.json.side_effect = ValueError("not json")
    else:
        m.json.return_value = payload if payload is not None else {}
    return m


# ===================================================================== DeepSeek fetch
DS_FETCH_OK = {"title": "Example article",
               "content": "# Example article\n\nArticle content."}


class TestDeepSeekFetch:
    def setup_method(self):
        self.p = DeepSeekProvider()

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_FETCH_OK)) as post:
            result = self.p.fetch("https://example.com/article")
        args, kwargs = post.call_args
        assert args[0] == "https://api.deepseek.com/v1/tools/fetch"
        assert kwargs["json"] == {"url": "https://example.com/article"}
        assert kwargs["headers"]["Authorization"] == "Bearer ds-key"
        assert result.provider == "deepseek"
        assert result.mode.value == "keyed"
        assert result.content_type == "text_markdown"

    def test_response_mapping(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_FETCH_OK)):
            result = self.p.fetch("https://example.com/article")
        assert result.title == "Example article"
        assert result.content.startswith("# Example article")
        assert result.url == "https://example.com/article"

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setenv("UWR_DEEPSEEK_BASE_URL", "https://api.deepseek.test/")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp(DS_FETCH_OK)) as post:
            self.p.fetch("https://example.com")
        assert post.call_args.args[0] == "https://api.deepseek.test/v1/tools/fetch"

    def test_403_is_security_policy_not_auth(self, monkeypatch):
        """DeepSeek documents 403 as 'Fetch rejected by the security policy'."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "blocked"}}, status=403)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://blocked.example")
        assert "security policy" in str(cm.value)
        assert cm.value.auth_failure is False
        assert cm.value.fallback_eligible is True

    def test_404_is_no_content_not_auth(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "no markdown"}}, status=404)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://empty.example")
        assert "no usable page content" in str(cm.value)
        assert cm.value.auth_failure is False
        assert cm.value.fallback_eligible is True

    def test_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "bad key"}}, status=401)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert cm.value.auth_failure is True

    def test_429_is_rate_limited_and_not_auth(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "qps"}}, status=429)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert "429" in str(cm.value)
        assert cm.value.auth_failure is False

    def test_402_is_balance_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "Insufficient Balance"}}, status=402)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert "insufficient balance" in str(cm.value).lower()
        assert cm.value.auth_failure is False

    def test_empty_content_raises(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"title": "t", "content": "   "})):
            with pytest.raises(ProviderError):
                self.p.fetch("https://example.com")

    def test_content_is_truncated_to_extract_limit(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
        monkeypatch.setenv("WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT", "10")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"title": "t", "content": "x" * 500})):
            assert len(self.p.fetch("https://example.com").content) == 10

    def test_overlong_url_never_hits_the_network(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

        def _boom(*a, **k):
            raise AssertionError("no network call expected for a >8192-char url")

        with patch('universal_web_retrieval.providers.deepseek.httpx.post', side_effect=_boom):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com/" + "x" * 8200)
        assert "url too long" in str(cm.value)
        assert cm.value.fallback_eligible is True

    def test_missing_key_raises_before_any_call(self, monkeypatch):
        with patch('universal_web_retrieval.providers.deepseek.httpx.post') as post:
            with pytest.raises(ProviderError):
                self.p.fetch("https://example.com")
        assert post.called is False


# ===================================================================== Kimi
KIMI_BASIC_OK = {"search_results": [
    {"authority": "S", "date": "2026-06-01", "icon": "https://x/favicon.ico",
     "mime": "text/html", "site_name": "Kimi API Open Platform",
     "snippet": "Documentation entry of the Kimi API open platform.",
     "text": "", "title": "API Overview", "url": "https://platform.kimi.ai/docs/api/overview"},
    {"authority": "A", "date": "", "icon": "", "mime": "text/html",
     "site_name": "", "snippet": "", "text": "", "title": "No snippet",
     "url": "https://example.com/no-snippet"},
]}

KIMI_PRO_OK = {"search_results": [
    {"authority": "S", "date": "2026-06-01", "icon": "", "mime": "text/html",
     "site_name": "Kimi API Open Platform", "snippet": "Short snippet.",
     "title": "API Overview", "url": "https://platform.kimi.ai/docs/api/overview",
     "chunks": [{"text": "The Kimi API provides chat completions.", "score": 1.23},
                {"text": "Second chunk", "score": 0.4}]},
]}


class TestKimiSearch:
    def setup_method(self):
        self.p = KimiProvider()

    def test_basic_request_shape(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "ms-key")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_BASIC_OK)) as post:
            self.p.search("Kimi K2 release", 5)
        args, kwargs = post.call_args
        assert args[0] == "https://api.moonshot.cn/v1/tools/search"
        assert kwargs["headers"]["Authorization"] == "Bearer ms-key"
        # Official field name is text_query — not "query".
        assert kwargs["json"] == {"text_query": "Kimi K2 release", "limit": 5,
                                  "include_content": False}
        assert "query" not in kwargs["json"]

    def test_kimi_api_key_alias(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "kimi-alias")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_BASIC_OK)) as post:
            self.p.search("q", 3)
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer kimi-alias"

    def test_moonshot_key_wins_over_alias(self, monkeypatch):
        monkeypatch.setenv("KIMI_API_KEY", "alias")
        monkeypatch.setenv("MOONSHOT_API_KEY", "primary")
        assert config.kimi_api_key() == "primary"

    def test_pro_tier_uses_search_pro_endpoint(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        monkeypatch.setenv("UWR_KIMI_SEARCH_TIER", "pro")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_PRO_OK)) as post:
            self.p.search("q", 4)
        assert post.call_args.args[0] == "https://api.moonshot.cn/v1/tools/search_pro"
        # /search_pro documents no include_content field.
        assert post.call_args.kwargs["json"] == {"text_query": "q", "limit": 4}

    @pytest.mark.parametrize("value,expected", [
        ("", "basic"), ("BASIC", "basic"), ("pro", "pro"), (" PRO ", "pro"),
        ("premium", "basic"), ("pro_max", "basic"),
    ])
    def test_tier_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("UWR_KIMI_SEARCH_TIER", value)
        assert config.kimi_search_tier() == expected

    def test_intl_base_url_override(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        monkeypatch.setenv("UWR_KIMI_BASE_URL", "https://api.moonshot.ai/")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   side_effect=[_resp(KIMI_BASIC_OK),
                                _resp({"url": "https://e/1", "markdown": "# ok",
                                       "title": "ok"})]) as post:
            self.p.search("q", 5)
            self.p.fetch("https://e/1")
        assert post.call_args_list[0].args[0] == "https://api.moonshot.ai/v1/tools/search"
        assert post.call_args_list[1].args[0] == "https://api.moonshot.ai/v1/tools/fetch"

    def test_default_base_url_is_cn(self):
        assert config.kimi_base_url() == "https://api.moonshot.cn"

    def test_response_mapping_basic(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_BASIC_OK)):
            result = self.p.search("q", 5)
        assert len(result.results) == 2
        first = result.results[0]
        assert first["title"] == "API Overview"
        assert first["url"] == "https://platform.kimi.ai/docs/api/overview"
        assert first["snippet"].startswith("Documentation entry")
        assert first["published"] == "2026-06-01"
        assert first["site"] == "Kimi API Open Platform"
        assert result.provider == "kimi"
        assert result.mode.value == "keyed"

    def test_pro_chunks_fill_snippet_and_score(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        monkeypatch.setenv("UWR_KIMI_SEARCH_TIER", "pro")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_PRO_OK)):
            row = self.p.search("q", 5).results[0]
        # The documented snippet wins when present; score comes from chunk 1.
        assert row["snippet"] == "Short snippet."
        assert row["score"] == 1.23

    def test_pro_chunks_used_when_snippet_empty(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        monkeypatch.setenv("UWR_KIMI_SEARCH_TIER", "pro")
        payload = {"search_results": [
            {"title": "T", "url": "https://e/1", "snippet": "", "site_name": "",
             "date": "", "chunks": [{"text": "chunk body", "score": 0.9}]}]}
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(payload)):
            row = self.p.search("q", 5).results[0]
        assert row["snippet"] == "chunk body"
        assert row["score"] == 0.9

    def test_limit_is_capped_at_20(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_BASIC_OK)) as post:
            self.p.search("q", 500)
        assert post.call_args.kwargs["json"]["limit"] == 20

    def test_rows_truncated_to_limit(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(KIMI_BASIC_OK)):
            assert len(self.p.search("q", 1).results) == 1

    def test_401_without_body_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(None, status=401, text="", json_raises=True)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True

    def test_403_without_body_is_account_problem_not_key(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp(None, status=403, text="", json_raises=True)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "account inactive or suspended" in str(cm.value)
        assert cm.value.auth_failure is False
        assert cm.value.fallback_eligible is True

    def test_429_is_rate_limited(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"error": {"type": "rate_limited",
                                                 "message": "project qps limit exceeded"}},
                                      status=429)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "rate limited" in str(cm.value)
        assert "project qps limit exceeded" in str(cm.value)
        assert cm.value.auth_failure is False

    def test_400_invalid_request_is_fallback_eligible(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"error": {"type": "invalid_request",
                                                 "message": "limit must be between 1 and 20"}},
                                      status=400)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "HTTP 400" in str(cm.value)
        assert cm.value.fallback_eligible is True

    def test_empty_results_raises(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"search_results": []})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)


class TestKimiFetch:
    def setup_method(self):
        self.p = KimiProvider()

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "ms-key")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"url": "https://e/1", "markdown": "# Hi",
                                       "title": "Hi"})) as post:
            result = self.p.fetch("https://e/1")
        args, kwargs = post.call_args
        assert args[0] == "https://api.moonshot.cn/v1/tools/fetch"
        assert kwargs["json"] == {"url": "https://e/1"}
        assert result.content == "# Hi"
        assert result.title == "Hi"
        assert result.content_type == "text_markdown"

    def test_404_markdown_not_found_is_not_auth(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"error": {"type": "markdown_not_found",
                                                 "message": "No text/markdown content found."}},
                                      status=404)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://e/1")
        assert "no extractable content" in str(cm.value)
        assert cm.value.auth_failure is False
        assert cm.value.fallback_eligible is True

    def test_403_security_risk_is_not_auth(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"error": {"type": "security_risk",
                                                 "message": "We consider the current URL poses a security risk"}},
                                      status=403)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://blocked.example")
        assert "security risk" in str(cm.value)
        assert cm.value.auth_failure is False

    def test_invalid_scheme_never_hits_network(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post') as post:
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("ftp://example.com/file")
        assert "http/https" in str(cm.value)
        assert post.called is False

    def test_empty_markdown_raises(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "k")
        with patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"url": "https://e/1", "markdown": "  ",
                                       "title": ""})):
            with pytest.raises(ProviderError):
                self.p.fetch("https://e/1")

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.fetch("https://e/1")


# ===================================================================== Zhipu
ZHIPU_OK = {"id": "task-1", "created": 1790000000, "request_id": "req-1",
            "search_intent": [{"query": "q", "intent": "SEARCH_ALWAYS",
                               "keywords": "q"}],
            "search_result": [
                {"title": "Z1", "content": "Zhipu summary one",
                 "link": "https://z.example/1", "media": "Example Site",
                 "icon": "https://z.example/favicon.ico", "refer": "1",
                 "publish_date": "2026-05-01"},
                {"title": "Z2", "content": "Zhipu summary two",
                 "link": "https://z.example/2", "media": "", "icon": "",
                 "refer": "2", "publish_date": ""},
            ]}


class TestZhipu:
    def setup_method(self):
        self.p = ZhipuProvider()

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "zp-key")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)) as post:
            self.p.search("智谱 网络搜索", 5)
        args, kwargs = post.call_args
        assert args[0] == "https://open.bigmodel.cn/api/paas/v4/web_search"
        assert kwargs["headers"]["Authorization"] == "Bearer zp-key"
        assert kwargs["json"] == {"search_query": "智谱 网络搜索",
                                  "search_engine": "search_std",
                                  "search_intent": False, "count": 5}

    def test_bigmodel_api_key_alias(self, monkeypatch):
        monkeypatch.setenv("BIGMODEL_API_KEY", "bm-alias")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)) as post:
            self.p.search("q", 5)
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer bm-alias"
        assert post.call_args.kwargs["json"]["search_engine"] == "search_std"

    @pytest.mark.parametrize("engine", ["search_std", "search_pro",
                                        "search_pro_sogou", "search_pro_quark"])
    def test_engine_env_is_forwarded(self, monkeypatch, engine):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        monkeypatch.setenv("UWR_ZHIPU_ENGINE", engine.upper())
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)) as post:
            self.p.search("q", 5)
        assert post.call_args.kwargs["json"]["search_engine"] == engine

    def test_unknown_engine_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("UWR_ZHIPU_ENGINE", "search_ultra")
        assert config.zhipu_engine() == "search_std"

    def test_query_truncated_to_70_chars(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)) as post:
            self.p.search("客" * 200, 5)
        assert len(post.call_args.kwargs["json"]["search_query"]) == 70

    def test_count_capped_at_50(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)) as post:
            self.p.search("q", 500)
        assert post.call_args.kwargs["json"]["count"] == 50

    def test_response_mapping(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)):
            result = self.p.search("q", 5)
        assert [r["title"] for r in result.results] == ["Z1", "Z2"]
        assert result.results[0]["url"] == "https://z.example/1"
        assert result.results[0]["snippet"] == "Zhipu summary one"
        assert result.results[0]["site"] == "Example Site"
        assert result.results[0]["published"] == "2026-05-01"
        assert result.provider == "zhipu"

    def test_1701_body_code_is_concurrency_limit(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp({"error": {"code": "1701",
                                                 "message": "网络搜索并发已达上限"}},
                                      status=429)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "concurrency" in str(cm.value)
        assert cm.value.auth_failure is False

    def test_1703_body_code_is_no_results(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp({"error": {"code": "1703",
                                                 "message": "搜索引擎未返回有效数据"}})):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "no results" in str(cm.value)
        assert cm.value.fallback_eligible is True

    def test_unknown_body_code_is_reported(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp({"error": {"code": "1702",
                                                 "message": "no engine"}})):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "1702" in str(cm.value)

    def test_http_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp({"error": {"code": "1000", "message": "身份验证失败"}},
                                      status=401)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True

    def test_empty_result_raises(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp({"search_result": []})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "k")
        monkeypatch.setenv("UWR_ZHIPU_BASE_URL", "https://open.bigmodel.test/")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp(ZHIPU_OK)) as post:
            self.p.search("q", 5)
        assert post.call_args.args[0] == "https://open.bigmodel.test/api/paas/v4/web_search"

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)

    def test_fetch_not_supported(self):
        with pytest.raises(ProviderError):
            self.p.fetch("https://x")


# ===================================================================== Brave
BRAVE_OK = {"type": "search",
            "query": {"original": "python frameworks", "altered": "python web frameworks",
                      "more_results_available": True},
            "web": {"type": "search", "results": [
                {"title": "Top Python Web Frameworks", "url": "https://example.com/pf",
                 "description": "A comprehensive guide to Python web frameworks.",
                 "page_age": "2026-04-12T14:22:41",
                 "profile": {"name": "Example", "url": "https://example.com"},
                 "language": "en"},
                {"title": "Second", "url": "https://example.com/2",
                 "description": "No profile here",
                 "meta_url": {"hostname": "example.com"}},
            ]}}


class TestBrave:
    def setup_method(self):
        self.p = BraveProvider()

    def test_request_shape(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)) as get:
            self.p.search("python frameworks", 5)
        args, kwargs = get.call_args
        assert args[0] == "https://api.search.brave.com/res/v1/web/search"
        assert kwargs["params"] == {"q": "python frameworks", "count": 5}
        # Brave authenticates with X-Subscription-Token, never Authorization.
        assert kwargs["headers"]["X-Subscription-Token"] == "brave-key"
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["headers"]["Accept"] == "application/json"

    def test_count_capped_at_20(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)) as get:
            self.p.search("q", 500)
        assert get.call_args.kwargs["params"]["count"] == 20

    def test_query_truncated_to_600_chars(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)) as get:
            self.p.search("x" * 900, 3)
        assert len(get.call_args.kwargs["params"]["q"]) == 600

    def test_response_mapping(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)):
            result = self.p.search("q", 5)
        assert len(result.results) == 2
        first = result.results[0]
        assert first["title"] == "Top Python Web Frameworks"
        assert first["url"] == "https://example.com/pf"
        assert first["snippet"].startswith("A comprehensive guide")
        assert first["published"] == "2026-04-12T14:22:41"   # page_age preferred
        assert first["site"] == "Example"
        # No profile -> fall back to meta_url.hostname rather than an empty site.
        assert result.results[1]["site"] == "example.com"
        assert result.results[1]["published"] == ""

    def test_rows_truncated_to_limit(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)):
            assert len(self.p.search("q", 1).results) == 1

    def test_base_url_override(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        monkeypatch.setenv("UWR_BRAVE_BASE_URL", "https://brave.test/")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)) as get:
            self.p.search("q", 5)
        assert get.call_args.args[0] == "https://brave.test/res/v1/web/search"

    def test_401_is_auth_failure(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp({"error": {"code": "SUBSCRIPTION_TOKEN_INVALID",
                                                 "detail": "The provided subscription token is invalid."}},
                                      status=401)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert cm.value.auth_failure is True

    def test_422_is_unprocessable(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp({"error": {"code": "VALIDATION",
                                                 "detail": "count must be <= 20"}},
                                      status=422)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "unprocessable" in str(cm.value)
        assert cm.value.fallback_eligible is True

    def test_429_is_rate_limited(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp({"error": {"code": "RATE_LIMITED",
                                                 "detail": "Too many requests"}},
                                      status=429)):
            with pytest.raises(ProviderError) as cm:
                self.p.search("q", 5)
        assert "429" in str(cm.value)

    def test_empty_results_raises(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp({"type": "search", "web": {"results": []}})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_missing_web_key_raises(self, monkeypatch):
        monkeypatch.setenv("BRAVE_API_KEY", "k")
        with patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp({"type": "search"})):
            with pytest.raises(ProviderError):
                self.p.search("q", 5)

    def test_missing_key_raises(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)

    def test_fetch_not_supported(self):
        with pytest.raises(ProviderError):
            self.p.fetch("https://x")


# ===================================================================== Jina Reader
JINA_OK = {"code": 200, "status": 20000,
           "data": {"title": "Example Domain",
                    "url": "https://example.com",
                    "content": "# Example Domain\n\nThis domain is for use in examples.",
                    "description": "Example page",
                    "publishedTime": "2026-01-01"},
           "meta": {"usage": {"tokens": 42}}}


class TestJina:
    def setup_method(self):
        self.p = JinaProvider()

    def test_keyless_request_shape(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)) as get:
            result = self.p.fetch("https://example.com")
        args, kwargs = get.call_args
        # ?url= form keeps a target query string from colliding with Reader params.
        assert args[0] == "https://r.jina.ai/"
        assert kwargs["params"] == {"url": "https://example.com"}
        assert kwargs["headers"]["Accept"] == "application/json"
        assert "Authorization" not in kwargs["headers"]
        assert kwargs["headers"]["X-Timeout"].isdigit()
        assert result.mode.value == "keyless"
        assert result.title == "Example Domain"
        assert result.content.startswith("# Example Domain")
        assert result.provider == "jina"

    def test_key_adds_bearer_and_flips_mode(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "jina-key")
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)) as get:
            result = self.p.fetch("https://example.com")
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer jina-key"
        assert result.mode.value == "keyed"

    def test_x_timeout_follows_the_chain_budget_and_is_clamped(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)) as get:
            self.p.fetch("https://example.com", timeout_override=7)
        assert get.call_args.kwargs["headers"]["X-Timeout"] == "7"
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)) as get:
            self.p.fetch("https://example.com", timeout_override=999)
        assert get.call_args.kwargs["headers"]["X-Timeout"] == "180"

    def test_target_query_string_survives(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)) as get:
            self.p.fetch("https://example.com/search?q=a&b=2")
        assert get.call_args.kwargs["params"] == {"url": "https://example.com/search?q=a&b=2"}

    def test_response_truncated_to_extract_limit(self, monkeypatch):
        monkeypatch.setenv("WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT", "5")
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)):
            assert len(self.p.fetch("https://example.com").content) == 5

    def test_keyless_401_is_not_reported_as_a_key_problem(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp({"readableMessage": "Authentication required"},
                                      status=401, text="Authentication required")):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert cm.value.auth_failure is False        # keyless: cannot be a key problem
        assert cm.value.fallback_eligible is True

    def test_keyed_401_is_an_auth_failure(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "bad")
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp({"readableMessage": "Invalid API key"},
                                      status=401, text="Invalid API key")):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert cm.value.auth_failure is True

    def test_429_is_rate_limited(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp({"readableMessage": "Rate limit"},
                                      status=429)):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert "429" in str(cm.value)

    def test_451_is_explicit(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp({}, status=451, text="unavailable")):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert "451" in str(cm.value)

    def test_non_200_envelope_raises(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp({"code": 403, "status": 40301,
                                       "readableMessage": "blocked"})):
            with pytest.raises(ProviderError) as cm:
                self.p.fetch("https://example.com")
        assert "envelope code 403" in str(cm.value)

    def test_empty_content_raises(self, monkeypatch):
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp({"code": 200, "status": 20000,
                                       "data": {"url": "https://example.com",
                                                "content": ""}})):
            with pytest.raises(ProviderError):
                self.p.fetch("https://example.com")

    def test_bad_scheme_never_hits_network(self):
        with patch('universal_web_retrieval.providers.jina.httpx.get') as get:
            with pytest.raises(ProviderError):
                self.p.fetch("file:///etc/passwd")
        assert get.called is False

    def test_search_not_supported(self):
        with pytest.raises(ProviderError):
            self.p.search("q", 5)


# ===================================================================== chain wiring
class TestChainWiring:
    def test_unconfigured_new_providers_are_skipped_without_network(self, monkeypatch):
        """A chain of keyed-only new providers with no keys makes zero HTTP calls."""
        monkeypatch.setenv("UWR_SEARCH_CHAIN", "kimi,zhipu,brave")

        def _boom(*a, **k):
            raise AssertionError("no network call expected for unconfigured providers")

        with patch('universal_web_retrieval.providers.kimi.httpx.post', side_effect=_boom), \
             patch('universal_web_retrieval.providers.zhipu.httpx.post', side_effect=_boom), \
             patch('universal_web_retrieval.providers.brave.httpx.get', side_effect=_boom):
            with pytest.raises(ProviderError) as cm:
                ProviderRouter().search("q", 5)
        assert [a["outcome"] for a in cm.value.attempts] == ["skipped:not_configured"] * 3

    def test_unconfigured_fetch_chain_is_skipped_without_network(self, monkeypatch):
        monkeypatch.setenv("UWR_FETCH_CHAIN", "deepseek,kimi")

        def _boom(*a, **k):
            raise AssertionError("no network call expected for unconfigured providers")

        with patch('universal_web_retrieval.providers.deepseek.httpx.post', side_effect=_boom), \
             patch('universal_web_retrieval.providers.kimi.httpx.post', side_effect=_boom):
            with pytest.raises(ProviderError) as cm:
                ProviderRouter().fetch("https://example.com")
        assert [a["outcome"] for a in cm.value.attempts] == ["skipped:not_configured"] * 2

    def test_per_call_override_kimi_reports_not_configured(self):
        out = json.loads(server.web_search_tool("q", 5, providers=["kimi"]))
        assert out["success"] is False
        assert out["attempts"] == [{"provider": "kimi", "outcome": "skipped:not_configured"}]

    def test_example_chain_resolves_in_order(self, monkeypatch):
        monkeypatch.setenv("UWR_SEARCH_CHAIN",
                           "anysearch,tavily,deepseek,kimi,yuanbao,ddgs")
        monkeypatch.setenv("UWR_FETCH_CHAIN", "anysearch,tavily,deepseek,kimi,ddgs")
        r = ProviderRouter()
        assert r.resolve_names(Capability.SEARCH) == [
            "anysearch", "tavily", "deepseek", "kimi", "yuanbao", "ddgs"]
        assert r.resolve_names(Capability.FETCH) == [
            "anysearch", "tavily", "deepseek", "kimi", "ddgs"]

    def test_search_only_providers_cannot_join_a_fetch_chain(self, monkeypatch):
        monkeypatch.setenv("UWR_FETCH_CHAIN", "zhipu,brave,jina")
        r = ProviderRouter()
        with patch('universal_web_retrieval.providers.jina.httpx.get',
                   return_value=_resp(JINA_OK)):
            assert r.resolve_names(Capability.FETCH) == ["jina"]
            assert r.fetch("https://example.com").provider == "jina"

    def test_search_chain_fails_over_from_zhipu_to_brave(self, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "zp")
        monkeypatch.setenv("BRAVE_API_KEY", "br")
        with patch('universal_web_retrieval.providers.zhipu.httpx.post',
                   return_value=_resp({"error": {"code": "1701", "message": "并发上限"}},
                                      status=429)), \
             patch('universal_web_retrieval.providers.brave.httpx.get',
                   return_value=_resp(BRAVE_OK)):
            result = ProviderRouter().search("q", 5, providers=["zhipu", "brave"])
        assert result.provider == "brave"
        assert [a["outcome"] for a in result.attempts][0].startswith("error:")
        assert result.attempts[-1] == {"provider": "brave", "outcome": "ok"}

    def test_fetch_fails_over_from_deepseek_403_to_kimi(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds")
        monkeypatch.setenv("MOONSHOT_API_KEY", "ms")
        with patch('universal_web_retrieval.providers.deepseek.httpx.post',
                   return_value=_resp({"error": {"message": "policy"}}, status=403)), \
             patch('universal_web_retrieval.providers.kimi.httpx.post',
                   return_value=_resp({"url": "https://e/1", "markdown": "# ok",
                                       "title": "ok"})):
            result = ProviderRouter().fetch("https://e/1", providers=["deepseek", "kimi"])
        assert result.provider == "kimi"
        assert result.content == "# ok"
        assert [a["provider"] for a in result.attempts] == ["deepseek", "kimi"]

    def test_list_providers_shows_new_providers_and_positions(self, monkeypatch):
        monkeypatch.setenv("UWR_SEARCH_CHAIN", "anysearch,tavily,deepseek,kimi,yuanbao,ddgs")
        monkeypatch.setenv("UWR_FETCH_CHAIN", "anysearch,tavily,deepseek,kimi,ddgs")
        monkeypatch.setenv("MOONSHOT_API_KEY", "moonshot-super-secret")
        monkeypatch.setenv("ZHIPU_API_KEY", "zhipu-super-secret")
        monkeypatch.setenv("BRAVE_API_KEY", "brave-super-secret")
        raw = server.list_providers_tool()
        for secret in ("moonshot-super-secret", "zhipu-super-secret", "brave-super-secret"):
            assert secret not in raw
        payload = json.loads(raw)
        by_name = {row["name"]: row for row in payload["providers"]}

        assert by_name["kimi"]["position"] == {"search": 4, "fetch": 4}
        assert by_name["kimi"]["capabilities"] == ["fetch", "search"]
        assert by_name["kimi"]["configured"] is True
        assert by_name["deepseek"]["position"] == {"search": 3, "fetch": 3}
        assert by_name["zhipu"]["position"] == {"search": None}
        assert by_name["zhipu"]["configured"] is True
        assert by_name["brave"]["capabilities"] == ["search"]
        assert by_name["jina"]["position"] == {"fetch": None}
        # Jina is keyless, so it is "configured" without any key at all.
        assert by_name["jina"]["configured"] is True
        assert by_name["jina"]["auth_mode"] == "keyless"
