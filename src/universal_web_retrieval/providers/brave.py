"""Brave Search API — official Web Search endpoint, search only.

Verified 2026-10-20 against the official docs:

  API reference : https://api-dashboard.search.brave.com/api-reference/web/search/get
  Overview      : https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
  Official skill: https://github.com/brave/brave-search-skills/blob/main/skills/web-search/SKILL.md
  Pricing       : https://api-dashboard.search.brave.com/documentation/pricing

    GET https://api.search.brave.com/res/v1/web/search?q=...&count=...
    Headers  X-Subscription-Token: <BRAVE_API_KEY>   (required)
             Accept: application/json
    Params   q      str   required, max 600 characters / 75 words
             count  int   1-20, default 20  (applies to web results only)
             offset int   0-9, default 0
             country / search_lang / ui_lang / safesearch / freshness /
             text_decorations / result_filter / extra_snippets (not sent — defaults)
    Result   {"type": "search",
              "query": {"original", "altered", "more_results_available", ...},
              "web": {"type": "search",
                      "results": [{title, url, description, age, page_age,
                                   language, profile: {name, url, long_name},
                                   extra_snippets, ...}]}}

Both GET and POST are documented; GET is used because the query is a single
short string and the URL stays debuggable.

Pricing (official pricing page): Search plan **$5.00 per 1,000 requests**, with
**$5 of free credits every month** (≈1,000 free requests) applied automatically,
50 requests/second capacity. No China-specific host: the API is a single global
endpoint, so latency from mainland China depends on the network path.

Search only: Brave's LLM Context and Answers endpoints are separate products and
are not wired into the fetch chain.
"""
from __future__ import annotations

from typing import Any, Dict

import httpx

from .. import config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult, classify_http_error)

MAX_COUNT = 20
MAX_QUERY_CHARS = 600


class BraveProvider(Provider):
    NAME = "brave"
    CAPABILITIES = frozenset({Capability.SEARCH})
    KEYED_ONLY = True
    KIND = "search"

    def api_key(self) -> str:
        return config.brave_api_key()

    def _timeout(self, override: float | None):
        configured = config.brave_timeout()
        if override is None:
            return configured
        bounded = min(override, 1.0) if override < 1.0 else override
        return (min(configured[0], bounded), min(configured[1], bounded))

    def _headers(self) -> Dict[str, str]:
        # The subscription token is a HEADER, not a Bearer credential — sending
        # "Authorization: Bearer" here would simply 401.
        return {"X-Subscription-Token": self.api_key(),
                "Accept": "application/json"}

    @staticmethod
    def _error_text(payload: Any, fallback: str) -> str:
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                detail = err.get("detail") or err.get("message") or ""
                code = err.get("code") or ""
                if detail or code:
                    return f"{code}: {detail}".strip(": ")
            if isinstance(err, str) and err:
                return err
        return (fallback or "")[:300]

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        def _call() -> SearchResult:
            if not self.api_key():
                raise ProviderError("brave: no API key configured")
            params = {
                "q": (query or "").strip()[:MAX_QUERY_CHARS],
                "count": max(1, min(int(limit or 10), MAX_COUNT)),
            }
            resp = httpx.get(f"{config.brave_base_url()}/res/v1/web/search",
                             params=params, timeout=self._timeout(timeout_override),
                             headers=self._headers())
            if resp.status_code >= 400:
                try:
                    detail = self._error_text(resp.json(), resp.text)
                except Exception:  # noqa: BLE001
                    detail = (resp.text or "")[:300]
                if resp.status_code == 422:
                    # Documented: unprocessable query/parameter combination.
                    raise ProviderError(f"brave: unprocessable request (HTTP 422): {detail}")
                raise classify_http_error(resp.status_code, detail, authenticated=True)

            data = resp.json()
            rows = []
            for item in ((data.get("web") or {}).get("results") or []):
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                profile = item.get("profile") or {}
                rows.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": str(item.get("description") or ""),
                    # page_age is the ISO publication time; `age` is the
                    # human-readable variant ("2 days ago").
                    "published": str(item.get("page_age") or item.get("age") or ""),
                    "site": str((profile or {}).get("name") or "")
                            or str(((item.get("meta_url") or {}).get("hostname")) or ""),
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
        raise ProviderError("brave: fetch capability not offered by this provider")
