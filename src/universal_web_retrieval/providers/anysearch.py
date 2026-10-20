"""AnySearch provider — official API, keyed (Bearer) or keyless (no auth header).

Official interface (docs: https://anysearch.com/install/mcp-install.md, verified 2026-09-14):
  POST https://api.anysearch.com/v1/search   {"query", "max_results"}          -> {code:0, data:{results:[{title,url,snippet,content}]}}
  POST https://api.anysearch.com/v1/extract  {"url"}                           -> {code:0, data:{url,title,content}}
  Keyed:   Authorization: Bearer <ANYSEARCH_API_KEY>
  Keyless: omit the Authorization header entirely (official instruction).
"""
from __future__ import annotations

import httpx

from .. import config
from ..errors import AuthMode, FetchResult, Provider, ProviderError, SearchResult, classify_http_error


class AnySearchProvider(Provider):
    NAME = "anysearch"
    TIMEOUT_CONFIG = staticmethod(config.anysearch_timeout)

    def api_key(self) -> str:
        return config.anysearch_api_key()

    def _headers(self) -> dict:
        # Keyless AnySearch must omit Authorization entirely.
        key = self.api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        def _call() -> SearchResult:
            r = httpx.post("https://api.anysearch.com/v1/search",
                           json={"query": query, "max_results": min(limit, 10)},
                           timeout=self._timeout(timeout_override), headers=self._headers())
            if r.status_code >= 400:
                raise classify_http_error(r.status_code, r.text, authenticated=(self.mode() == AuthMode.KEYED))
            data = r.json()
            if data.get("code") != 0:
                raise ProviderError(f"API code {data.get('code')}: {data.get('message','')}")
            rows = [{"title": str(x.get("title", "")), "url": str(x.get("url", "")),
                     "snippet": str(x.get("snippet") or x.get("content") or "")}
                    for x in data.get("data", {}).get("results", [])]
            if not rows:
                raise ProviderError("no results")
            return SearchResult(results=rows, provider=self.NAME, mode=self.mode())
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        def _call() -> FetchResult:
            r = httpx.post("https://api.anysearch.com/v1/extract",
                           json={"url": url}, timeout=self._timeout(timeout_override), headers=self._headers())
            if r.status_code >= 400:
                raise classify_http_error(r.status_code, r.text, authenticated=(self.mode() == AuthMode.KEYED))
            data = r.json()
            if data.get("code") != 0:
                raise ProviderError(f"API code {data.get('code')}: {data.get('message','')}")
            d = data.get("data", {})
            content = str(d.get("content", ""))[:config.extract_char_limit()]
            if not content:
                raise ProviderError("empty content")
            return FetchResult(url=url, content=content, provider=self.NAME, mode=self.mode(),
                               title=str(d.get("title", "")), content_type="text_markdown")
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result
