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
from universal_web_retrieval.providers.ddgs import DDGSProvider
from universal_web_retrieval.providers.tavily import TavilyProvider

ANYSEARCH_KEY = os.environ.get("ANYSEARCH_API_KEY", "").strip()
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()


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
