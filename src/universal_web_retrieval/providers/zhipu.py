"""Zhipu BigModel (智谱开放平台) Web Search API — search only.

Verified 2026-10-20 against the official pages:

  API reference : https://docs.bigmodel.cn/api-reference/工具-api/网络搜索
                  (OpenAPI: servers `https://open.bigmodel.cn/api/`, path `/paas/v4/web_search`)
  Product guide : https://docs.bigmodel.cn/cn/guide/tools/web-search   (engine table + prices)
  Pricing page  : https://bigmodel.cn/pricing

    POST https://open.bigmodel.cn/api/paas/v4/web_search
    Headers  Authorization: Bearer <ZHIPU_API_KEY>  +  Content-Type: application/json
    Body     {"search_query": str (recommended <= 70 chars, schema maxLength 70),
              "search_engine": "search_std" | "search_pro" |
                               "search_pro_sogou" | "search_pro_quark",   (required)
              "search_intent": bool (required, default false),            (required)
              "count": int (1-50, default 10),
              "search_domain_filter": str, "search_recency_filter": str,
              "content_size": "medium" | "high",
              "request_id": str, "user_id": str}
    Result   {"id": str, "created": int, "request_id": str,
              "search_intent": [{query, intent, keywords}],
              "search_result": [{title, content, link, media, icon, refer, publish_date}]}

Errors are a single envelope — ``{"error": {"code": str, "message": str}}`` — with
these documented codes: 1701 search concurrency upper bound reached (retry later
or reduce concurrency), 1702 no usable search-engine service, 1703 the engine
returned no valid data (adjust the query).

Engine prices (official guide, per call): search_std 0.01 CNY, search_pro 0.03,
search_pro_sogou 0.05, search_pro_quark 0.05. ``UWR_ZHIPU_ENGINE`` picks the
engine (default ``search_std``); an unrecognised value falls back to the default
instead of being sent.

Search only: BigModel's page-reading features are separate LLM/agent products,
not a REST fetch endpoint, so there is nothing to wire into the fetch chain.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from .. import config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult, classify_http_error)

MAX_COUNT = 50
MAX_QUERY_CHARS = 70     # schema maxLength on search_query

log = logging.getLogger("universal_web_retrieval.zhipu")


class ZhipuProvider(Provider):
    NAME = "zhipu"
    CAPABILITIES = frozenset({Capability.SEARCH})
    KEYED_ONLY = True
    KIND = "search"
    TIMEOUT_CONFIG = staticmethod(config.zhipu_timeout)

    def api_key(self) -> str:
        return config.zhipu_api_key()

    @staticmethod
    def _body_error(payload: Any) -> tuple:
        """Return ``(code, message)`` from the documented error envelope."""
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                return str(err.get("code") or ""), str(err.get("message") or "")
            if isinstance(err, str) and err:
                # Some gateway deployments return {"error": "message"}.
                return "", err
        return "", ""

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        def _call() -> SearchResult:
            if not self.api_key():
                raise ProviderError("zhipu: no API key configured")
            source_query = (query or "").strip()
            search_query = source_query[:MAX_QUERY_CHARS]
            if len(source_query) > MAX_QUERY_CHARS:
                log.debug("zhipu: query truncated from %d to %d characters",
                          len(source_query), len(search_query))
            body = {
                "search_query": search_query,
                "search_engine": config.zhipu_engine(),
                # Required by the schema; false = skip intent detection and
                # search directly, which is what an agent tool wants.
                "search_intent": False,
                "count": max(1, min(int(limit or 10), MAX_COUNT)),
            }
            resp = self.bearer_json_post(httpx.post,
                                         f"{config.zhipu_base_url()}/api/paas/v4/web_search",
                                         body, timeout=self._timeout(timeout_override))
            payload: Any = {}
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001 — non-JSON error pages are possible
                payload = {}

            code, message = self._body_error(payload)
            if resp.status_code >= 400:
                if code in ("1701",):
                    raise ProviderError(f"zhipu: search concurrency limit reached "
                                        f"(code {code}): {message[:200]}")
                if resp.status_code in (401, 403):
                    raise ProviderError(f"zhipu: HTTP {resp.status_code}: {message or resp.text[:200]}",
                                        auth_failure=True)
                raise classify_http_error(resp.status_code, message or resp.text,
                                          authenticated=True)
            if code:
                # Documented body-level codes, returned on an otherwise 200 reply.
                if code == "1701":
                    raise ProviderError(f"zhipu: search concurrency limit reached (code 1701): {message[:200]}")
                if code == "1702":
                    raise ProviderError(f"zhipu: no usable search-engine service (code 1702): {message[:200]}")
                if code == "1703":
                    raise ProviderError(f"no results (zhipu code 1703): {message[:200]}")
                raise ProviderError(f"zhipu code {code}: {message[:200]}")

            if isinstance(payload, dict) and "error" in payload:
                raise ProviderError(f"zhipu error: {message[:200] or 'provider returned an error envelope'}")

            rows = []
            for item in (payload.get("search_result") or []):
                link = str(item.get("link") or "").strip()
                if not link:
                    continue
                rows.append({
                    "title": str(item.get("title") or ""),
                    "url": link,
                    "snippet": str(item.get("content") or ""),
                    "published": str(item.get("publish_date") or ""),
                    "site": str(item.get("media") or ""),
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
        raise ProviderError("zhipu: fetch capability not offered by this provider")
