"""Bocha AI Web Search API (博查) — official REST endpoint.

Official interface (verified 2026-10-20):
  Platform:     https://open.bochaai.com/            (curl sample on the landing page)
  Docs (Feishu): https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
  Official MCP:  https://github.com/BochaAI/bocha-search-mcp  (src/bocha_search_mcp/server.py)

    POST https://api.bochaai.com/v1/web-search
    Headers  Authorization: Bearer <BOCHA_API_KEY>  +  Content-Type: application/json
    Body     {"query": str, "summary": bool, "freshness": str, "count": int}
             freshness: noLimit (default) | oneDay | oneWeek | oneMonth | oneYear |
                        YYYY-MM-DD | YYYY-MM-DD..YYYY-MM-DD
             count:     1-50
    Result   {"code": 200, "log_id": ..., "msg": null,
              "data": {"_type": "SearchResponse",
                       "webPages": {"value": [{name, url, snippet, summary,
                                               siteName, siteIcon, datePublished,
                                               dateLastCrawled, ...}]}}}

The docs' quick-start uses ``api.bocha.cn`` while the platform homepage and the
official MCP server use ``api.bochaai.com``; both are first-party hosts. We
default to the platform/MCP host and allow ``UWR_BOCHA_BASE_URL`` to switch.

``summary: true`` is sent because Bocha's long ``summary`` field is markedly
better for an LLM consumer than the short ``snippet``; we fall back to
``snippet`` when a hit has no summary.

Search-only: Bocha's page-extraction API is a separate product we do not wire in.
"""
from __future__ import annotations

from typing import Any, Dict

import httpx

from .. import config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult, classify_http_error)

MAX_COUNT = 50


class BochaProvider(Provider):
    NAME = "bocha"
    CAPABILITIES = frozenset({Capability.SEARCH})
    KEYED_ONLY = True
    KIND = "search"
    TIMEOUT_CONFIG = staticmethod(config.bocha_timeout)

    def api_key(self) -> str:
        return config.bocha_api_key()

    @staticmethod
    def _body_code(payload: Any) -> int | None:
        """Bocha mirrors the HTTP status in a top-level ``code`` field."""
        if not isinstance(payload, dict):
            return None
        for key in ("code", "status"):
            value = payload.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
        return None

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        def _call() -> SearchResult:
            if not self.api_key():
                raise ProviderError("bocha: no API key configured")
            payload = {
                "query": query,
                "summary": True,
                "freshness": "noLimit",
                "count": max(1, min(int(limit or 10), MAX_COUNT)),
            }
            resp = self.bearer_json_post(httpx.post,
                                         f"{config.bocha_base_url()}/v1/web-search",
                                         payload, timeout=self._timeout(timeout_override))
            if resp.status_code >= 400:
                raise classify_http_error(resp.status_code, resp.text, authenticated=True)
            data = resp.json()
            code = self._body_code(data)
            if code is not None and code not in (0, 200):
                msg = ""
                if isinstance(data, dict):
                    msg = str(data.get("msg") or data.get("message") or "")
                if code in (401, 403):
                    raise ProviderError(f"bocha code {code}: {msg[:200]}", auth_failure=True)
                if code == 429:
                    raise ProviderError(f"bocha rate limited: {msg[:200]}")
                raise ProviderError(f"bocha code {code}: {msg[:200]}")
            body = data.get("data") if isinstance(data, dict) else None
            pages = ((body or {}).get("webPages") or {}).get("value") or []
            rows = []
            for page in pages:
                url = str(page.get("url") or "").strip()
                if not url:
                    continue
                rows.append({
                    "title": str(page.get("name") or page.get("title") or ""),
                    "url": url,
                    "snippet": str(page.get("summary") or page.get("snippet") or ""),
                    "published": str(page.get("datePublished") or ""),
                    "site": str(page.get("siteName") or ""),
                })
                if len(rows) >= limit:
                    break
            if not rows:
                raise ProviderError("no results")
            return SearchResult(results=rows, provider=self.NAME, mode=AuthMode.KEYED)
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        raise ProviderError("bocha: fetch capability not offered by this provider")
