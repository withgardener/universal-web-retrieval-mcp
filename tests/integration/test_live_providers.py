"""Live provider smoke tests — gated behind RUN_LIVE_TESTS=1.

These hit real provider APIs. Skipped entirely unless RUN_LIVE_TESTS=1.
Keyed tests SKIP (not fail) when the provider's key is not configured.
"""
import os
import unittest

import pytest

if os.environ.get("RUN_LIVE_TESTS") != "1":
    pytest.skip("live tests disabled (set RUN_LIVE_TESTS=1)", allow_module_level=True)

sys_path = os.path.join(os.path.dirname(__file__), '..', '..', 'src')
import sys
sys.path.insert(0, sys_path)

from universal_web_retrieval.providers.anysearch import AnySearchProvider
from universal_web_retrieval.providers.bocha import BochaProvider
from universal_web_retrieval.providers.brave import BraveProvider
from universal_web_retrieval.providers.ddgs import DDGSProvider
from universal_web_retrieval.providers.deepseek import DeepSeekProvider
from universal_web_retrieval.providers.jina import JinaProvider
from universal_web_retrieval.providers.kimi import KimiProvider
from universal_web_retrieval.providers.tavily import TavilyProvider
from universal_web_retrieval.providers.yuanbao import YuanbaoProvider
from universal_web_retrieval.providers.zhipu import ZhipuProvider

ANYSEARCH_KEY = os.environ.get("ANYSEARCH_API_KEY", "").strip()
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
YUANBAO_KEY = (os.environ.get("YUANBAO_API_KEY") or os.environ.get("WSA_API_KEY") or "").strip()
BOCHA_KEY = os.environ.get("BOCHA_API_KEY", "").strip()
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
KIMI_KEY = (os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY") or "").strip()
ZHIPU_KEY = (os.environ.get("ZHIPU_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or "").strip()
BRAVE_KEY = os.environ.get("BRAVE_API_KEY", "").strip()


def _assert_search_ok(res, provider_name):
    assert res.provider == provider_name
    assert res.results, f"{provider_name} returned no results"
    assert all(r.get("url") for r in res.results)


def _assert_fetch_ok(res, provider_name):
    assert res.provider == provider_name
    assert res.content, f"{provider_name} returned empty content"


class TestDDGSLive(unittest.TestCase):
    """No key needed — always runnable."""

    def test_search_live(self):
        _assert_search_ok(DDGSProvider().search("python asyncio", 3), "ddgs")

    def test_extract_live(self):
        _assert_fetch_ok(DDGSProvider().fetch("https://example.com"), "ddgs")


class TestAnySearchLive(unittest.TestCase):
    def test_anonymous_search(self):
        p = AnySearchProvider()
        if p.api_key():
            self.skipTest("ANYSEARCH_API_KEY set — anonymous test not applicable")
        _assert_search_ok(p.search("python asyncio", 3), "anysearch")

    def test_anonymous_extract(self):
        p = AnySearchProvider()
        if p.api_key():
            self.skipTest("ANYSEARCH_API_KEY set — anonymous test not applicable")
        _assert_fetch_ok(p.fetch("https://example.com"), "anysearch")

    def test_keyed_search(self):
        if not ANYSEARCH_KEY:
            self.skipTest("ANYSEARCH_API_KEY not configured")
        p = AnySearchProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("python asyncio", 3), "anysearch")

    def test_keyed_extract(self):
        if not ANYSEARCH_KEY:
            self.skipTest("ANYSEARCH_API_KEY not configured")
        _assert_fetch_ok(AnySearchProvider().fetch("https://example.com"), "anysearch")


class TestTavilyLive(unittest.TestCase):
    def test_keyless_search(self):
        p = TavilyProvider()
        if p.api_key():
            self.skipTest("TAVILY_API_KEY set — keyless test not applicable")
        _assert_search_ok(p.search("python asyncio", 3), "tavily")

    def test_keyless_extract(self):
        p = TavilyProvider()
        if p.api_key():
            self.skipTest("TAVILY_API_KEY set — keyless test not applicable")
        _assert_fetch_ok(p.fetch("https://example.com"), "tavily")

    def test_keyed_search(self):
        if not TAVILY_KEY:
            self.skipTest("TAVILY_API_KEY not configured")
        p = TavilyProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("python asyncio", 3), "tavily")

    def test_keyed_extract(self):
        if not TAVILY_KEY:
            self.skipTest("TAVILY_API_KEY not configured")
        _assert_fetch_ok(TavilyProvider().fetch("https://example.com"), "tavily")


class TestYuanbaoLive(unittest.TestCase):
    def test_keyed_search(self):
        if not YUANBAO_KEY:
            self.skipTest("YUANBAO_API_KEY / WSA_API_KEY not configured")
        p = YuanbaoProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("人工智能发展现状", 5), "yuanbao")

    def test_session_reuse_second_search(self):
        if not YUANBAO_KEY:
            self.skipTest("YUANBAO_API_KEY / WSA_API_KEY not configured")
        p = YuanbaoProvider()
        _assert_search_ok(p.search("腾讯云", 5), "yuanbao")
        # second call must reuse the MCP session (no second initialize)
        _assert_search_ok(p.search("腾讯云 元宝搜索", 5), "yuanbao")


class TestBochaLive(unittest.TestCase):
    def test_keyed_search(self):
        if not BOCHA_KEY:
            self.skipTest("BOCHA_API_KEY not configured")
        p = BochaProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("阿里巴巴 2024 ESG 报告", 5), "bocha")


class TestDeepSeekLive(unittest.TestCase):
    def test_keyed_search(self):
        if not DEEPSEEK_KEY:
            self.skipTest("DEEPSEEK_API_KEY not configured")
        p = DeepSeekProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("DeepSeek V4", 5), "deepseek")

    def test_keyed_fetch(self):
        if not DEEPSEEK_KEY:
            self.skipTest("DEEPSEEK_API_KEY not configured")
        _assert_fetch_ok(DeepSeekProvider().fetch("https://example.com"), "deepseek")


class TestKimiLive(unittest.TestCase):
    def test_keyed_search(self):
        if not KIMI_KEY:
            self.skipTest("MOONSHOT_API_KEY / KIMI_API_KEY not configured")
        p = KimiProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("Kimi K2 release", 5), "kimi")

    def test_keyed_fetch(self):
        if not KIMI_KEY:
            self.skipTest("MOONSHOT_API_KEY / KIMI_API_KEY not configured")
        _assert_fetch_ok(KimiProvider().fetch("https://example.com"), "kimi")


class TestZhipuLive(unittest.TestCase):
    def test_keyed_search(self):
        if not ZHIPU_KEY:
            self.skipTest("ZHIPU_API_KEY / BIGMODEL_API_KEY not configured")
        p = ZhipuProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("智谱 网络搜索", 5), "zhipu")


class TestBraveLive(unittest.TestCase):
    def test_keyed_search(self):
        if not BRAVE_KEY:
            self.skipTest("BRAVE_API_KEY not configured")
        p = BraveProvider()
        assert p.mode().value == "keyed"
        _assert_search_ok(p.search("python asyncio", 5), "brave")


class TestJinaLive(unittest.TestCase):
    def test_keyless_fetch(self):
        # Jina Reader documents keyless use (20 RPM); no key required.
        _assert_fetch_ok(JinaProvider().fetch("https://example.com"), "jina")
