"""Tavily provider — official API, keyed (Bearer) or keyless (X-Tavily-Access-Mode).

Official interface (docs: https://docs.tavily.com/documentation/keyless, verified 2026-09-14):
  POST https://api.tavily.com/search   {"query", "max_results", ...}
  POST https://api.tavily.com/extract  {"urls": [...]}
  Keyed:   Authorization: Bearer <TAVILY_API_KEY>
  Keyless: X-Tavily-Access-Mode: keyless header (official keyless access; same
           request/response schema as keyed).
"""
from __future__ import annotations

import httpx

from .. import config
from ..errors import AuthMode, FetchResult, Provider, ProviderError, SearchResult, classify_http_error
from ..errors import AuthMode, FetchResult, Provider, ProviderError, SearchResult, classify_http_error


class TavilyProvider(Provider):
    NAME = "tavily"

    def api_key(self) -> str:
        return config.tavily_api_key()

    def _headers(self) -> dict:
        if self.api_key():
            return {"Authorization": f"Bearer {self.api_key()}"}
        # Official keyless access header.
        return {"X-Tavily-Access-Mode": "keyless"}

    def search(self, query: str, limit: int) -> SearchResult:
        def _call() -> SearchResult:
            r = httpx.post("https://api.tavily.com/search",
                           json={"query": query, "max_results": min(limit, 20),
                                 "include_raw_content": False, "include_images": False},
                           timeout=config.tavily_timeout(), headers=self._headers())
            if r.status_code >= 400:
                raise classify_http_error(r.status_code, r.text)
            rows = [{"title": str(x.get("title", "")), "url": str(x.get("url", "")),
                     "snippet": str(x.get("content", ""))}
                    for x in r.json().get("results", [])]
            return SearchResult(results=rows, provider=self.NAME, mode=self.mode())
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str) -> FetchResult:
        def _call() -> FetchResult:
            r = httpx.post("https://api.tavily.com/extract",
                           json={"urls": [url], "include_images": False},
                           timeout=config.tavily_timeout(), headers=self._headers())
            if r.status_code >= 400:
                raise classify_http_error(r.status_code, r.text)
            resp = r.json()
            results = resp.get("results", [])
            if not results:
                failed = resp.get("failed_results", []) or resp.get("failed_urls", [])
                msg = str(failed[0].get("error", "extraction failed")) if failed else "no results"
                raise ProviderError(msg)
            x = results[0]
            content = str(x.get("raw_content") or x.get("content") or "")[:config.extract_char_limit()]
            if not content:
                raise ProviderError("empty content")
            return FetchResult(url=url, content=content, provider=self.NAME, mode=self.mode(),
                               title=str(x.get("title", "")), content_type="text_markdown")
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result
