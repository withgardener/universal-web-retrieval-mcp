"""DDGS provider — official `ddgs` package (keyless by design; final chain fallback).

Official interface (ddgs 9.16.0 installed; verified live 2026-09-14):
  DDGS().text(query, max_results=N)                 -> [{title, href, body, ...}]
  DDGS().extract(url, fmt="text_markdown")          -> {"url": ..., "content": ...}
  fmt options: "text_markdown" | "text_plain" | "text_rich" | "text" (raw HTML) | "content" (bytes)

DDGS has no API key concept — always keyless. It is the last-resort plain-HTTP
retrieval: normal HTML/news/blog/docs pages are fine; JS-rendered, login-walled
or CAPTCHA-protected pages are NOT guaranteed.
"""
from __future__ import annotations

from .. import config
from ..errors import AuthMode, FetchResult, Provider, ProviderError, SearchResult


class DDGSProvider(Provider):
    NAME = "ddgs"

    def api_key(self) -> str:
        return ""  # no key concept; always keyless

    def _client(self):
        from ddgs import DDGS
        return DDGS(timeout=config.ddgs_timeout())

    def search(self, query: str, limit: int) -> SearchResult:
        def _call() -> SearchResult:
            rows = []
            with self._client() as client:
                for i, hit in enumerate(client.text(query, max_results=limit)):
                    if i >= limit:
                        break
                    rows.append({"title": str(hit.get("title", "")),
                                 "url": str(hit.get("href") or hit.get("url") or ""),
                                 "snippet": str(hit.get("body", ""))})
            if not rows:
                raise ProviderError("no results")
            return SearchResult(results=rows, provider=self.NAME, mode=self.mode())
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str) -> FetchResult:
        def _call() -> FetchResult:
            with self._client() as client:
                resp = client.extract(url, fmt="text_markdown")
            content = str(resp.get("content", ""))[:config.extract_char_limit()]
            if not content:
                raise ProviderError("empty content")
            return FetchResult(url=url, content=content, provider=self.NAME, mode=self.mode(),
                               content_type="text_markdown")
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result
