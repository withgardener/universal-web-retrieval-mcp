"""0.3.1 — credentials never leak, and successful URLs are never rewritten.

The bug: ``server._json()`` ran a global ``[?&](token|key|sig|...)=...`` regex
over the **entire serialised payload**. Errors were scrubbed correctly, but so
were successful results — every signed URL (``?sig=…``, ``?token=…``,
``?key=…``) inside fetched Markdown or a search row was silently rewritten to
``<redacted>``, producing links that no longer work.

The trust boundary drawn here:

* **Diagnostic text** (``error`` strings, ``attempts`` outcomes, log lines) is
  written by us or by a provider and may echo *our* request URL or headers, so
  it gets the full scrub: configured key values, ``NAME=value`` assignments,
  bearer tokens, and credential-bearing query parameters.
* **Successful content** is the user-requested page plus the URLs the provider
  returned. It is never pattern-rewritten, because a signed URL is an opaque
  credential of the *page* and rewriting it breaks the link while protecting
  nothing. The one exception is an exact configured secret value: if a page or
  provider echoes our own key, that is a leak by definition, so it is redacted
  in place.

Residual risk (documented in README): a page that embeds a *third-party* signed
URL is returned as-is — that URL belongs to the page's publisher, not to this
server, and the payload is only handed to the calling agent.
"""
from __future__ import annotations

import json
import logging

import pytest

from universal_web_retrieval import server
from universal_web_retrieval.errors import (AuthMode, FetchResult, ProviderError,
                                            SearchResult)
from universal_web_retrieval.redact import (scrub_diagnostic, scrub_exact_secrets)

SIGNED = "https://cdn.example/pic.jpg?token=abc123&sig=deadbeef&X-Amz-Date=20260101"


# ---------------------------------------------------------------- primitives
def test_scrub_diagnostic_redacts_url_credentials_but_keeps_keys():
    out = scrub_diagnostic("failed on https://api.example/v1?api_key=SECRET&q=1")
    assert "SECRET" not in out
    assert "api_key=" in out          # the parameter name stays: it aids diagnosis
    assert "q=1" in out


def test_scrub_diagnostic_redacts_bearer_tokens():
    out = scrub_diagnostic("headers: Authorization: Bearer sk-abcdef1234567890")
    assert "sk-abcdef1234567890" not in out
    assert "Bearer" in out


def test_scrub_diagnostic_redacts_key_assignments():
    out = scrub_diagnostic("BOCHA_API_KEY=zzz-secret-value")
    assert "zzz-secret-value" not in out


def test_scrub_diagnostic_redacts_configured_secret_values(monkeypatch):
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as-live-0123456789")
    out = scrub_diagnostic("upstream said: bad token as-live-0123456789")
    assert "as-live-0123456789" not in out
    assert "[REDACTED]" in out


def test_scrub_exact_secrets_leaves_ordinary_text_alone(monkeypatch):
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as-live-0123456789")
    text = "Read more at https://example.com/a?sig=abc&token=def"
    assert scrub_exact_secrets(text) == text


# ---------------------------------------------------------------- success path
def test_successful_fetch_keeps_signed_urls(monkeypatch):
    payload = FetchResult(url="https://example.com/a",
                          content=f"[image]({SIGNED})",
                          provider="anysearch", mode=AuthMode.KEYLESS).to_dict()
    out = json.loads(server._json(payload))
    assert SIGNED in out["content"]
    assert "<redacted>" not in out["content"]


def test_successful_search_keeps_key_style_query_parameters(monkeypatch):
    payload = SearchResult(results=[{"title": "t", "url": "https://x.example/a?key=pub",
                                     "snippet": "see https://y.example/?token=t0k3n"}],
                           provider="a", mode=AuthMode.KEYLESS).to_dict()
    out = json.loads(server._json(payload))
    assert out["results"][0]["url"] == "https://x.example/a?key=pub"
    assert "token=t0k3n" in out["results"][0]["snippet"]


def test_configured_secret_is_still_removed_from_successful_content(monkeypatch):
    """Never blanket-allow a success payload: our own key must not pass through."""
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as-live-0123456789")
    payload = FetchResult(url="https://example.com/a",
                          content="debug echo: as-live-0123456789 done",
                          provider="anysearch", mode=AuthMode.KEYLESS).to_dict()
    out = json.loads(server._json(payload))
    assert "as-live-0123456789" not in out["content"]
    assert "[REDACTED]" in out["content"]


def test_page_content_that_documents_bearer_headers_is_preserved():
    body = "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig'"
    payload = FetchResult(url="https://docs.example/a", content=body,
                          provider="jina", mode=AuthMode.KEYLESS).to_dict()
    out = json.loads(server._json(payload))
    assert "eyJhbGciOiJIUzI1NiJ9.payload.sig" in out["content"]


# ---------------------------------------------------------------- error path
def test_error_payload_redacts_credentials():
    out = json.loads(server._json({"success": False,
                                   "error": "HTTP 500 for https://api.example/v1"
                                            "?token=SECRETVAL&x=1"}))
    assert "SECRETVAL" not in out["error"]
    assert out["error"].startswith("HTTP 500")


def test_error_attempts_are_scrubbed():
    out = json.loads(server._json({"success": False, "error": "boom",
                                   "attempts": [
                                       {"provider": "a",
                                        "outcome": "error:HTTP 401 at "
                                                   "https://p.example/s?api_key=SEKRET"}]}))
    assert "SEKRET" not in json.dumps(out)


def test_bearer_token_is_scrubbed_from_error_text():
    out = json.loads(server._json({"success": False,
                                   "error": "sent Authorization: Bearer sk-abcdef1234567890"}))
    assert "sk-abcdef1234567890" not in out["error"]


def test_no_token_but_similar_words_survive():
    out = json.loads(server._json({"success": False,
                                   "error": "monkey=1 keeps attention=yes"}))
    assert "monkey=1" in out["error"]
    assert "attention=yes" in out["error"]


# ---------------------------------------------------------------- tool + logs
def test_web_fetch_tool_keeps_signed_urls(monkeypatch):
    result = FetchResult(url="https://example.com/a", content=f"![i]({SIGNED})",
                         provider="anysearch", mode=AuthMode.KEYLESS)
    monkeypatch.setattr(server.router, "fetch", lambda *a, **kw: result)
    out = json.loads(server.web_fetch_tool(url="https://example.com/a"))
    assert SIGNED in out[0]["content"]


def test_web_fetch_tool_redacts_error_credentials(monkeypatch):
    def boom(*args, **kwargs):
        raise ProviderError("HTTP 403 for https://api.example/v1?token=SEKRET")

    monkeypatch.setattr(server.router, "fetch", boom)
    out = json.loads(server.web_fetch_tool(url="https://example.com/a"))
    assert "SEKRET" not in json.dumps(out)


def test_auth_failure_log_does_not_print_the_key(monkeypatch, caplog):
    """The loud AUTH FAILURE log must never carry the configured key."""
    from universal_web_retrieval.providers.router import ProviderRouter
    from universal_web_retrieval.errors import Capability, Provider

    monkeypatch.setenv("ANYSEARCH_API_KEY", "as-live-0123456789")

    class Bad(Provider):
        NAME = "bad"
        CAPABILITIES = frozenset({Capability.SEARCH})
        TIMEOUT_CONFIG = staticmethod(lambda: 5.0)

        def api_key(self):
            return "as-live-0123456789"

        def search(self, query, limit, timeout_override=None):
            raise ProviderError("HTTP 401: bad token as-live-0123456789",
                                auth_failure=True)

        def fetch(self, url, timeout_override=None):
            raise AssertionError("not used")

    r = ProviderRouter(providers={"bad": Bad()})
    r._providers = {Capability.SEARCH: [Bad()]}
    with caplog.at_level(logging.ERROR, logger="universal_web_retrieval.router"):
        with pytest.raises(ProviderError):
            r.search("q", 5)
    assert "as-live-0123456789" not in caplog.text
