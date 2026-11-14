"""0.3.1 — the URL a caller passes is echoed back, credentials included.

``web_fetch`` returns the caller's URL verbatim in the top-level ``url`` field of
every entry — success and failure alike — because a rewritten URL is a broken
link. That trade-off is deliberate; it is also a disclosure. A ``?token=`` or
``?sig=`` inside the caller's URL comes back in cleartext, the payload becomes
part of the conversation, and from there it can reach session transcripts, chat
logs and the model service that serves the conversation.

The redaction split (see ``redact``) is what makes this easy to get wrong in
prose: diagnostics *are* scrubbed, so a reader can assume the whole payload is.
These tests pin two things so the note cannot quietly become false:

* **behaviour** — ``url`` is never pattern-rewritten, on every result path
  (success, provider error, invalid input, batch deadline), while ``error`` and
  ``attempts`` stay scrubbed;
* **documentation** — README, CHANGELOG, ``docs/agent-tool-policy.md`` and the
  *served* ``web_fetch`` description each state the boundary, and none of them
  claims the URL is scrubbed or that redaction guarantees confidentiality.

No behaviour changed in this change set: the ``url`` field never was scrubbed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from universal_web_retrieval import server
from universal_web_retrieval.errors import AuthMode, FetchResult, ProviderError

REPO = Path(__file__).resolve().parents[2]
SECRET_URL = "https://files.example.com/report.pdf?token=abc123&sig=deadbeef"


def _ok(url: str) -> FetchResult:
    return FetchResult(url=url, content="body", provider="anysearch",
                       mode=AuthMode.KEYLESS)


# ---------------------------------------------------------------- result paths
def test_success_echoes_caller_url_verbatim(monkeypatch):
    """The usable link is the point: a signed URL comes back byte-for-byte."""
    monkeypatch.setattr(server.router, "fetch", lambda *a, **kw: _ok(SECRET_URL))
    out = json.loads(server.web_fetch_tool(url=SECRET_URL))
    assert out[0]["url"] == SECRET_URL
    assert "token=abc123" in out[0]["url"]
    assert "sig=deadbeef" in out[0]["url"]


def test_provider_error_echoes_caller_url_verbatim(monkeypatch):
    """A failed URL is still echoed as given, so the caller can retry it."""
    def boom(*args, **kwargs):
        raise ProviderError("HTTP 403 from upstream")

    monkeypatch.setattr(server.router, "fetch", boom)
    out = json.loads(server.web_fetch_tool(url=SECRET_URL))
    assert out[0]["url"] == SECRET_URL
    assert out[0]["error"].startswith("HTTP 403")


def test_invalid_input_echoes_caller_url_verbatim():
    """A rejected scheme is reported against the exact string that was given."""
    url = "ftp://files.example.com/report.pdf?token=abc123"
    out = json.loads(server.web_fetch_tool(url=url))
    assert out[0]["url"] == url
    assert out[0]["error"].startswith("invalid input:")


def test_batch_deadline_entry_echoes_caller_url_verbatim(monkeypatch):
    """No network call happens, yet the entry still carries the caller's URL."""
    monkeypatch.setattr(server.config, "chain_deadline", lambda: 0.0)
    urls = [SECRET_URL, "https://files.example.com/two.pdf?sig=cafebabe"]
    out = json.loads(server.web_fetch_tool(urls=urls))
    assert [row["url"] for row in out] == urls
    assert all("deadline" in row["error"] for row in out)


# ------------------------------------------------------- scrub boundary itself
def test_error_is_scrubbed_while_url_is_not(monkeypatch):
    """The split in one assertion: diagnostics scrubbed, caller URL untouched."""
    url = "https://api.example.com/v1?token=SEKRET&q=1"

    def boom(*args, **kwargs):
        exc = ProviderError(f"HTTP 403 for {url}")
        exc.attempts = [{"provider": "anysearch", "outcome": f"error:403 for {url}"}]
        raise exc

    monkeypatch.setattr(server.router, "fetch", boom)
    out = json.loads(server.web_fetch_tool(url=url))
    assert out[0]["url"] == url                    # deliberately kept
    assert "SEKRET" not in out[0]["error"]         # diagnostic scrubbed
    assert "token=" in out[0]["error"]             # parameter name stays
    assert "SEKRET" not in json.dumps(out[0]["attempts"])


def test_a_configured_provider_key_is_still_stripped_from_url(monkeypatch):
    """Documented exception: echoing our own key would be a leak, link or not."""
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as-live-0123456789")
    url = "https://files.example.com/report.pdf?token=as-live-0123456789"
    monkeypatch.setattr(server.router, "fetch", lambda *a, **kw: _ok(url))
    out = json.loads(server.web_fetch_tool(url=url))
    assert "as-live-0123456789" not in json.dumps(out)
    assert "[REDACTED]" in out[0]["url"]


# ------------------------------------------------------------------ docs
def _served_web_fetch_description() -> str:
    """The description an MCP client actually receives, not the source text."""
    tools = asyncio.run(server.app.list_tools())
    match = [tool for tool in tools if tool.name == "web_fetch"]
    assert len(match) == 1
    return match[0].description or ""


def _required_phrases(text: str) -> list[str]:
    lowered = text.lower()
    return [phrase for phrase in (
        "verbatim",            # the URL is echoed as given
        "cleartext",           # credentials in it come back unscrubbed
        "transcript",          # where it ends up: the conversation record
        "log",                 # ...and logs
        "model provider",      # ...and the model service
        "confidentiality guarantee",  # and what redaction is NOT
        "short-lived",         # the advice that follows from it
    ) if phrase not in lowered]


@pytest.mark.parametrize("path", [
    REPO / "README.md",
    REPO / "CHANGELOG.md",
    REPO / "docs" / "agent-tool-policy.md",
])
def test_docs_state_the_url_privacy_boundary(path):
    missing = _required_phrases(path.read_text(encoding="utf-8"))
    assert not missing, f"{path.name} is missing the privacy boundary: {missing}"


def test_served_tool_description_states_the_url_privacy_boundary():
    missing = _required_phrases(_served_web_fetch_description())
    assert not missing, f"web_fetch description is missing: {missing}"


def test_docs_never_claim_the_caller_url_is_scrubbed():
    """The opposite claim is the tempting one; refuse it everywhere."""
    sources = {
        "README.md": (REPO / "README.md").read_text(encoding="utf-8"),
        "CHANGELOG.md": (REPO / "CHANGELOG.md").read_text(encoding="utf-8"),
        "agent-tool-policy.md": (REPO / "docs" / "agent-tool-policy.md").read_text(
            encoding="utf-8"),
        "web_fetch description": _served_web_fetch_description(),
    }
    for claim in ("url is redacted", "url field is scrubbed",
                  "credentials in the url are scrubbed",
                  "never leaves the server", "never recorded"):
        for name, text in sources.items():
            assert claim not in text.lower(), f"overclaim in {name}: {claim!r}"
