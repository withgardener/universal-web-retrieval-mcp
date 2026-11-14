"""0.3.1 — fetch results say when they were truncated, and name block pages.

Two separate promises, deliberately kept apart:

* **Truncation is explicit.** Every provider used to slice content to
  ``WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT`` with nothing in the payload saying so.
  ``FetchResult`` now carries ``truncated`` and ``original_length`` — additive
  fields, so existing consumers see the same ``content``/``title``/... keys.
* **Blocked pages are detected by evidence, not by length.** A provider that
  gets a Cloudflare/Akamai/captcha interstitial back would hand the caller
  "Just a moment..." as if it were the article. Such a page raises a
  fallback-eligible error so the chain tries the next provider. Detection needs
  a marker *combination* (title marker + body marker, or several body markers)
  inside a short page — a long article that merely mentions "captcha", or a
  short page saying "not found", is never treated as a block.

Everything here is offline: provider transports are stubbed.
"""
from __future__ import annotations

import json
import unittest.mock as mock

import pytest

from universal_web_retrieval import config
from universal_web_retrieval.content import challenge_reason, truncate
from universal_web_retrieval.errors import (AuthMode, Capability, FetchResult,
                                            Provider, ProviderError)
from universal_web_retrieval.providers.router import ProviderRouter


CF_PAGE = {
    "title": "Just a moment...",
    "body": ("Checking your browser before accessing example.com.\n\n"
             "Enable JavaScript and cookies to continue\n\n"
             "Ray ID: 8f2c1d9a\ncf_chl_opt = {}"),
}

AKAMAI_PAGE = {
    "title": "Access Denied",
    "body": ("You don't have permission to access \"http://example.com/\" on "
             "this server.\n\nReference #18.2f3d4c17.1790000000.1a2b3c4"),
}

BENIGN_SHORT = {
    "title": "Login help",
    "body": "We use a captcha on the login page to stop automated sign-ups.",
}

LONG_ARTICLE = {
    "title": "Why captchas annoy everyone",
    "body": ("Cloudflare and other CDNs serve captcha challenges. " * 120),
}


# ---------------------------------------------------------------- truncation
def test_truncate_reports_original_length_and_flag():
    text = "x" * 100
    kept, truncated, original = truncate(text, 20)
    assert kept == "x" * 20
    assert truncated is True
    assert original == 100


def test_truncate_leaves_short_text_untouched():
    kept, truncated, original = truncate("short", 20)
    assert kept == "short"
    assert truncated is False
    assert original == 5


def test_truncate_counts_characters_not_bytes():
    text = "字" * 10          # 30 UTF-8 bytes, 10 characters
    kept, truncated, original = truncate(text, 4)
    assert kept == "字" * 4
    assert truncated is True
    assert original == 10


def test_truncate_zero_limit_is_a_no_op():
    kept, truncated, original = truncate("abc", 0)
    assert (kept, truncated, original) == ("abc", False, 3)


# ---------------------------------------------------------------- block pages
def test_cloudflare_interstitial_is_detected():
    assert challenge_reason(CF_PAGE["title"], CF_PAGE["body"])


def test_akamai_denial_is_detected():
    assert challenge_reason(AKAMAI_PAGE["title"], AKAMAI_PAGE["body"])


def test_short_page_mentioning_captcha_once_is_not_a_block():
    assert challenge_reason(BENIGN_SHORT["title"], BENIGN_SHORT["body"]) == ""


def test_long_article_mentioning_block_hardware_is_not_a_block():
    assert challenge_reason(LONG_ARTICLE["title"], LONG_ARTICLE["body"]) == ""


def test_short_body_without_markers_is_not_a_block():
    assert challenge_reason("", "Not found") == ""
    assert challenge_reason("", "") == ""


def test_detection_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("UWR_BLOCK_DETECTION", "off")
    assert config.block_detection_enabled() is False


# ---------------------------------------------------------------- provider wiring
class _Resp:
    def __init__(self, payload, status=200):
        self.status_code = status
        self.text = json.dumps(payload)
        self.headers = {"content-type": "application/json"}
        self._payload = payload

    def json(self):
        return self._payload


def _anysearch_fetch(payload, limit=None):
    from universal_web_retrieval.providers.anysearch import AnySearchProvider
    with mock.patch("universal_web_retrieval.providers.anysearch.httpx.post",
                    return_value=_Resp(payload)):
        return AnySearchProvider().fetch("https://example.com")


def test_truncation_is_reported_in_the_result(monkeypatch):
    monkeypatch.setenv("WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT", "25")
    result = _anysearch_fetch({"code": 0, "data": {"title": "T",
                                                   "content": "y" * 200}})
    assert len(result.content) == 25
    assert result.truncated is True
    assert result.original_length == 200


def test_untruncated_content_says_so(monkeypatch):
    result = _anysearch_fetch({"code": 0, "data": {"title": "T", "content": "hello"}})
    assert result.truncated is False
    assert result.original_length == 5


def test_to_dict_carries_truncation_fields():
    payload = FetchResult(url="https://x", content="abc", provider="p",
                          mode=AuthMode.KEYLESS, truncated=True,
                          original_length=99).to_dict()
    assert payload["truncated"] is True
    assert payload["original_length"] == 99
    # Existing keys keep their meaning; this is an additive change.
    for key in ("url", "content", "title", "content_type", "provider", "mode",
                "latency_ms", "attempts"):
        assert key in payload


def test_challenge_page_from_a_provider_raises_fallback_eligible(monkeypatch):
    with pytest.raises(ProviderError) as exc:
        _anysearch_fetch({"code": 0, "data": {"title": CF_PAGE["title"],
                                              "content": CF_PAGE["body"]}})
    assert "blocked" in str(exc.value).lower()
    assert exc.value.fallback_eligible is True


def test_challenge_page_is_skipped_by_the_chain(monkeypatch):
    """The walk moves on to the next provider instead of returning the wall."""

    class Challenge:
        NAME = "blocked"
        CAPABILITIES = frozenset({Capability.FETCH})
        KIND = "fetch"
        TIMEOUT_CONFIG = staticmethod(lambda: 5.0)
        calls = 0

        def api_key(self):
            return ""

        def supports(self, capability):
            return capability in self.CAPABILITIES

        def mode(self):
            return AuthMode.KEYLESS

        def search(self, query, limit, timeout_override=None):
            raise AssertionError("not used")

        def fetch(self, url, timeout_override=None):
            Challenge.calls += 1
            from universal_web_retrieval.content import enforce_not_blocked
            enforce_not_blocked(CF_PAGE["title"], CF_PAGE["body"], provider=self.NAME)
            return FetchResult(url=url, content="should not happen",
                               provider=self.NAME, mode=AuthMode.KEYLESS)

    class Good(Provider):
        NAME = "good"
        CAPABILITIES = frozenset({Capability.FETCH})
        TIMEOUT_CONFIG = staticmethod(lambda: 5.0)

        def api_key(self):
            return ""

        def search(self, query, limit, timeout_override=None):
            raise AssertionError("not used")

        def fetch(self, url, timeout_override=None):
            return FetchResult(url=url, content="real article", provider=self.NAME,
                               mode=AuthMode.KEYLESS)

    r = ProviderRouter(providers={"blocked": Challenge(), "good": Good()})
    r._providers = {Capability.FETCH: [Challenge(), Good()]}
    result = r.fetch("https://example.com")
    assert result.provider == "good"
    assert result.content == "real article"
    assert result.attempts[0]["outcome"].startswith("error:")
    assert "blocked" in result.attempts[0]["outcome"].lower()


def test_detection_off_returns_the_page_as_is(monkeypatch):
    monkeypatch.setenv("UWR_BLOCK_DETECTION", "off")
    result = _anysearch_fetch({"code": 0, "data": {"title": CF_PAGE["title"],
                                                   "content": CF_PAGE["body"]}})
    assert "Checking your browser" in result.content
