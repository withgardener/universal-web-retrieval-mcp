"""Provider abstraction and error classification."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Capability(Enum):
    SEARCH = "search"
    FETCH = "fetch"


class AuthMode(Enum):
    KEYED = "keyed"
    KEYLESS = "keyless"


class ProviderError(Exception):
    """Base for provider failures. `fallback_eligible` decides chain continuation."""

    def __init__(self, message: str, *, fallback_eligible: bool = True,
                 auth_failure: bool = False):
        super().__init__(message)
        self.fallback_eligible = fallback_eligible
        self.auth_failure = auth_failure
        # Chain-level failures carry the per-provider outcome trail.
        self.attempts: List[Dict[str, str]] = []


class InvalidInputError(Exception):
    """MCP input is malformed — never eligible for provider fallback."""


def classify_http_error(status: int, body: str, *, authenticated: bool) -> ProviderError:
    """Map an HTTP status + body to a classified ProviderError.

    ``authenticated`` reflects the request's actual auth mode: 401/403 on a KEYED
    request is a credentials problem (auth_failure=True, loud ERROR log); the same
    status on a KEYLESS request is an access/provider failure — NOT an auth
    failure, and must not tell the user to "check the configured API key".
    """
    text = (body or "")[:300]
    if status in (401, 403):
        return ProviderError(f"HTTP {status}: {text}", fallback_eligible=True,
                             auth_failure=authenticated)
    if status == 429:
        return ProviderError(f"HTTP 429 rate limited: {text}")
    if status == 402:
        # Payment required (DeepSeek: insufficient balance). Never retried in-process.
        return ProviderError(f"HTTP 402 insufficient balance / arrears: {text}")
    if 500 <= status < 600:
        return ProviderError(f"HTTP {status} upstream error: {text}")
    # Other 4xx: request-level problem, but could be provider-specific quirks —
    # treat as fallback-eligible so the chain still makes progress.
    return ProviderError(f"HTTP {status}: {text}")


@dataclass
class FetchResult:
    url: str
    content: str
    provider: str
    mode: AuthMode
    title: str = ""
    latency_ms: int = 0
    content_type: str = "text_markdown"
    # Per-provider outcome trail, e.g. [{"provider": "bocha", "outcome": "skipped:not_configured"}]
    attempts: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url, "content": self.content, "title": self.title,
            "content_type": self.content_type, "provider": self.provider,
            "mode": self.mode.value, "latency_ms": self.latency_ms,
            "attempts": list(self.attempts),
        }


# Free-form extra fields a search hit may carry (passed through when non-empty).
_SEARCH_EXTRA_FIELDS = ("published", "site", "score")


@dataclass
class SearchResult:
    results: List[Dict[str, Any]]
    provider: str
    mode: AuthMode
    latency_ms: int = 0
    attempts: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        rows = []
        for r in self.results:
            row = {"title": r.get("title", ""), "url": r.get("url", ""),
                   "snippet": r.get("snippet", "")}
            for key in _SEARCH_EXTRA_FIELDS:
                if r.get(key) not in (None, ""):
                    row[key] = r[key]
            rows.append(row)
        return {
            "success": True, "provider": self.provider, "mode": self.mode.value,
            "latency_ms": self.latency_ms, "attempts": list(self.attempts),
            "results": rows,
        }


class Provider(ABC):
    """One retrieval vendor. Auth mode is auto: key present -> keyed, absent -> keyless.

    Metadata drives the registry, the configurable chain and ``list_providers``:

    ``CAPABILITIES``  which router chains may include this provider.
    ``KEYED_ONLY``    True when the provider is useless without a key (Yuanbao,
                      Bocha, DeepSeek, Kimi, Zhipu, Brave). Such a link is skipped
                      with ``skipped:not_configured`` instead of being attempted and
                      401-ing the whole chain. AnySearch/Tavily/Jina keep their
                      official keyless behaviour and set this to False.
    ``KIND``          informational: "search" / "fetch" / "answer".
    """

    NAME: str = ""
    CAPABILITIES: frozenset = frozenset({Capability.SEARCH, Capability.FETCH})
    KEYED_ONLY: bool = False
    KIND: str = "search"

    def mode(self) -> AuthMode:
        return AuthMode.KEYED if self.api_key() else AuthMode.KEYLESS

    def supports(self, capability: Capability) -> bool:
        return capability in self.CAPABILITIES

    def is_configured(self) -> bool:
        """True when this provider can run right now.

        Keyless-capable providers are always configured; keyed-only providers
        need a key. Used for the ``not_configured`` skip and by ``list_providers``.
        """
        return bool(self.api_key()) or not self.KEYED_ONLY

    def not_configured(self) -> bool:
        return not self.is_configured()

    def auth_mode_name(self) -> str:
        return self.mode().value

    @abstractmethod
    def api_key(self) -> str:
        """Return the configured API key, or '' when absent (=> keyless)."""

    @abstractmethod
    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        """Web search. Raises ProviderError on failure. ``timeout_override`` caps
        this attempt's total time (chain deadline remainder)."""

    @abstractmethod
    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        """Fetch one URL's content. Raises ProviderError on failure. ``timeout_override``
        caps this attempt's total time (chain deadline remainder)."""

    def _timed(self, fn, *args, **kwargs):
        start = time.monotonic()
        result = fn(*args, **kwargs)
        elapsed = int((time.monotonic() - start) * 1000)
        return result, elapsed
