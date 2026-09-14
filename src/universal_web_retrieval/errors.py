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


class InvalidInputError(Exception):
    """MCP input is malformed — never eligible for provider fallback."""


def classify_http_error(status: int, body: str, *, authenticated: bool) -> ProviderError:
    """Map an HTTP status + body to a classified ProviderError.

    ``authenticated`` reflects the request's actual auth mode: 401/403 on a KEYED
    request is a credentials problem (auth_failure=True, loud ERROR log); the same
    status on a KEYLESS request is an access/provider failure — NOT an auth
    failure, and must not tell the user to "check the configured API key"."""
    text = (body or "")[:300]
    if status in (401, 403):
        return ProviderError(f"HTTP {status}: {text}", fallback_eligible=True,
                             auth_failure=authenticated)
    if status == 429:
        return ProviderError(f"HTTP 429 rate limited: {text}")
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url, "content": self.content, "title": self.title,
            "content_type": self.content_type, "provider": self.provider,
            "mode": self.mode.value, "latency_ms": self.latency_ms,
        }


@dataclass
class SearchResult:
    results: List[Dict[str, Any]]
    provider: str
    mode: AuthMode
    latency_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": True, "provider": self.provider, "mode": self.mode.value,
            "latency_ms": self.latency_ms,
            "results": [{"title": r.get("title", ""), "url": r.get("url", ""),
                         "snippet": r.get("snippet", "")} for r in self.results],
        }


class Provider(ABC):
    """One retrieval vendor. Auth mode is auto: key present -> keyed, absent -> keyless."""

    NAME: str = ""

    def mode(self) -> AuthMode:
        return AuthMode.KEYED if self.api_key() else AuthMode.KEYLESS

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
