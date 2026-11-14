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
  a *combination*: two distinct technical fingerprints, or a challenge title
  plus one marker, inside a short page. A single word is never enough, so a long
  article that merely mentions "captcha", a short help page titled "CAPTCHA
  help", a vendor glossary or an FAQ quoting "Just a moment..." stays content.

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

# A help page whose TITLE carries the word — the shape that used to be killed:
# "captcha" was a title marker, so any short help page about captchas matched
# title marker + body marker and was reported as a block wall.
CAPTCHA_HELP = {
    "title": "CAPTCHA help",
    "body": ("A CAPTCHA is a test that tells humans and bots apart. If the "
             "challenge does not load, reload the page or use another browser."),
}

# A reference page that names several vendors. Three distinct markers used to be
# enough on its own, so a short glossary was indistinguishable from a wall.
CAPTCHA_DOCS = {
    "title": "CAPTCHA types explained",
    "body": ("Common challenge providers: reCAPTCHA, hCaptcha and Cloudflare "
             "Turnstile. All three ask the visitor for a captcha answer."),
}

# A help page that quotes the interstitial text it explains.
FAQ_QUOTING_WALL = {
    "title": "What does “Just a moment…” mean?",
    "body": ("Sometimes a site answers with “Just a moment...” and a browser "
             "check before showing the article. It usually clears in a few "
             "seconds; if it does not, allow JavaScript and cookies."),
}

# A help page whose title opens with a denial phrase but whose suffix is a
# subtitle, not the site's own label. The separator rule used to accept any
# suffix, so this page matched title + the word "captcha" and was thrown away.
ACCESS_DENIED_GUIDE = {
    "title": "Access Denied: a practical guide to 403 responses",
    "body": ("A 403 response means the server understood the request and refused "
             "it. Some sites put a captcha in front of a page; others simply do "
             "not let the file be read. Check the URL and your credentials."),
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


def test_short_help_page_titled_with_captcha_is_not_a_block():
    """The false positive the review found: the title word alone used to count.

    ``CAPTCHA help`` does not name a challenge page, and the body merely
    explains the term, so the page must be returned as content.
    """
    assert challenge_reason(CAPTCHA_HELP["title"], CAPTCHA_HELP["body"]) == ""


def test_docs_listing_several_captcha_vendors_is_not_a_block():
    """Three vendor names in a glossary are documentation, not three wall markers."""
    assert challenge_reason(CAPTCHA_DOCS["title"], CAPTCHA_DOCS["body"]) == ""


def test_short_faq_quoting_an_interstitial_is_not_a_block():
    """Quoted wall text without the title fingerprint of a wall is content."""
    assert challenge_reason(FAQ_QUOTING_WALL["title"], FAQ_QUOTING_WALL["body"]) == ""


def test_help_page_with_a_subtitle_is_not_a_block():
    """``Access Denied: a practical guide to 403 responses`` is a guide, not a wall.

    A real interstitial appends the site's own label ("Access Denied -
    example.com"); a subtitle is a phrase. The separator match used to accept
    any suffix, so a doc about 403s that mentions a captcha was reported as a
    challenge page and never returned.
    """
    assert challenge_reason(ACCESS_DENIED_GUIDE["title"],
                            ACCESS_DENIED_GUIDE["body"]) == ""


def test_challenge_title_alone_is_not_enough():
    """A title is evidence only together with a marker in the body."""
    assert challenge_reason("Just a moment...", "Article body goes here.") == ""


def test_single_technical_fingerprint_in_a_short_doc_is_not_a_block():
    """A code sample mentioning ``cf_chl_opt`` once is documentation."""
    body = ("Cloudflare sets the cf_chl_opt cookie while a challenge is being "
            "solved. Clear it to retry the challenge.")
    assert challenge_reason("Cookie reference", body) == ""


def test_two_technical_fingerprints_block_even_without_a_title():
    """A real wall is recognisable from its own machinery, title or not."""
    body = ("Checking your browser before accessing example.com. "
            "Enable JavaScript and cookies to continue.")
    assert challenge_reason("", body)


def test_waf_titles_with_a_site_suffix_still_block():
    """A real denial page keeps its own suffix after the challenge title."""
    assert challenge_reason("Sucuri WebSite Firewall - Access Denied",
                            "Sucuri website firewall blocked this request.")
    assert challenge_reason("Access Denied - example.com",
                            "Reference #18.2f3d4c17")


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
