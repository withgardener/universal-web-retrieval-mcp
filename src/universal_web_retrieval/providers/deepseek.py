"""DeepSeek native web search — official Search API (not an answer-style provider).

Verified 2026-10-20 against the official docs:

  Guide:  https://api-docs.deepseek.com/guides/search
  Release note: https://api-docs.deepseek.com/news/news261017
  ("Search & Fetch APIs are now open to everyone ... $0.75 per 1,000 calls")

    POST https://api.deepseek.com/v1/tools/search
    Headers  Authorization: Bearer <DEEPSEEK_API_KEY>  +  Content-Type: application/json
    Body     {"query": str (1-128 chars),
              "count": int (1-50, default 10),
              "need_content": bool (default false),
              "ori_question": str (optional),
              "localization": str (optional IP)}
    Result   {"results": [{rerank_score, title, url, snippet, summary, content,
                           site_name, site_icon, date, authority}]}

The Search API returns structured results directly — no model call, no
AI-synthesised answer — so this is a plain ``search`` provider, NOT an
``answer`` provider: it is fast, deterministic and costs one flat $0.75/1k
calls rather than tokens. (A third-party npm package advertises a server-side
``web_search_20250305`` tool over the Anthropic-compatible endpoint; that is
not what DeepSeek documents, and is not used here.)

Errors (official table):
  400 invalid request | 401 bad key (auth_failure) | 402 insufficient balance
  403 fetch rejected by policy | 404 nothing retrievable | 429 QPS exceeded
  500 internal | 504 downstream timeout

``UWR_DEEPSEEK_BASE_URL`` overrides the host; there is deliberately no model
knob — the Search API takes no model parameter.
"""
from __future__ import annotations

from typing import Dict

import httpx

from .. import config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult, classify_http_error)

MAX_COUNT = 50
MAX_QUERY_CHARS = 128


class DeepSeekProvider(Provider):
    NAME = "deepseek"
    CAPABILITIES = frozenset({Capability.SEARCH})
    KEYED_ONLY = True
    KIND = "search"

    def api_key(self) -> str:
        return config.deepseek_api_key()

    def _timeout(self, override: float | None):
        configured = config.deepseek_timeout()
        if override is None:
            return configured
        bounded = min(override, 1.0) if override < 1.0 else override
        return (min(configured[0], bounded), min(configured[1], bounded))

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key()}",
                "Content-Type": "application/json"}

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        def _call() -> SearchResult:
            if not self.api_key():
                raise ProviderError("deepseek: no API key configured")
            # The API caps the query at 128 characters (not bytes).
            q = (query or "").strip()[:MAX_QUERY_CHARS]
            resp = httpx.post(f"{config.deepseek_base_url()}/v1/tools/search",
                              json={"query": q,
                                    "count": max(1, min(int(limit or 10), MAX_COUNT)),
                                    "need_content": False},
                              timeout=self._timeout(timeout_override),
                              headers=self._headers())
            if resp.status_code >= 400:
                if resp.status_code == 404:
                    raise ProviderError(f"deepseek: nothing retrievable (HTTP 404): {resp.text[:200]}")
                raise classify_http_error(resp.status_code, resp.text, authenticated=True)
            data = resp.json()
            rows = []
            for item in (data.get("results") or []):
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                rows.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": str(item.get("snippet") or item.get("summary") or ""),
                    "published": str(item.get("date") or ""),
                    "site": str(item.get("site_name") or ""),
                    "score": item.get("rerank_score"),
                })
                if len(rows) >= limit:
                    break
            if not rows:
                # Official docs: "a normal search with no matches returns an empty
                # array" — an empty list is a valid, non-billable answer, but for
                # chain purposes an empty result must not silently end the walk.
                raise ProviderError("no results")
            return SearchResult(results=rows, provider=self.NAME, mode=AuthMode.KEYED)
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        raise ProviderError("deepseek: fetch capability not offered by this provider")
