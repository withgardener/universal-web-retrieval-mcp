"""Kimi / Moonshot official web search + URL fetch APIs (standalone REST).

Verified 2026-10-20 against the official API reference:

  Basic search : https://platform.kimi.ai/docs/api/tools-search.md
  Pro search   : https://platform.kimi.ai/docs/api/tools-search-pro.md
  URL fetch    : https://platform.kimi.ai/docs/api/tools-fetch.md
  Pricing      : https://platform.kimi.ai/docs/pricing/websearch
  Guide        : https://platform.kimi.ai/docs/guide/use-web-search

    POST {base}/v1/tools/search           (Web Search Basic, $0.002/call)
    POST {base}/v1/tools/search_pro       (Web Search Pro,   $0.003/call)
    Headers  Authorization: Bearer <MOONSHOT_API_KEY>  +  Content-Type: application/json
    Body     {"text_query": str (required, non-empty),
              "limit": int (1-20, default 5),
              "timeout_seconds": int (1-60, optional),
              "include_content": bool (default false; BASIC ONLY)}
             plus, on /search_pro only:
              "sites": [str] (<= 5, OR'd, no whitespace/parens),
              "time_window": {"start": "YYYY[-MM[-DD]]", "end": ...}
    Result   {"search_results": [{authority, date, icon, mime, site_name, snippet,
                                  text, title, url,
                                  chunks: [{text, score}]        # pro only
                                 }]}

    POST {base}/v1/tools/fetch            (URL Fetch, $0.002/call)
    Body     {"url": str}                  # http/https only
    Result   {"url": str, "markdown": str, "title": str}

The query field is ``text_query`` (NOT ``query``) and the result list is
``search_results`` — both differ from every other provider in this package, so
they are spelled out above rather than inferred.

Billing only counts HTTP 200 with a non-empty ``search_results`` (search) or a
non-blank ``markdown`` (fetch); failures and empty results are free.

Errors (official table): 400 invalid_request/invalid_url | 401 missing or invalid
key (no body) | 403 account inactive or suspended (no body) — but 403
``security_risk`` on /fetch means the URL was blocked | 404 ``markdown_not_found``
(fetch only) | 408 ``client_canceled`` | 429 ``rate_limited`` |
500 ``internal_error`` | 502 ``upstream_failed`` | 504 ``timeout``.

``UWR_KIMI_BASE_URL`` selects the host: ``https://api.moonshot.cn`` (China,
default) or ``https://api.moonshot.ai`` (international). Both platforms serve the
same three endpoints, but keys are not interchangeable between them.
``UWR_KIMI_SEARCH_TIER=pro`` switches search to the higher-recall endpoint.
"""
from __future__ import annotations

from typing import Any, Dict

import httpx

from .. import config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult)

MAX_LIMIT = 20          # official: limit must be between 1 and 20
MAX_SITES = 5           # search_pro only


class KimiProvider(Provider):
    NAME = "kimi"
    CAPABILITIES = frozenset({Capability.SEARCH, Capability.FETCH})
    KEYED_ONLY = True
    KIND = "search"

    def api_key(self) -> str:
        return config.kimi_api_key()

    def _timeout(self, override: float | None):
        configured = config.kimi_timeout()
        if override is None:
            return configured
        bounded = min(override, 1.0) if override < 1.0 else override
        return (min(configured[0], bounded), min(configured[1], bounded))

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key()}",
                "Content-Type": "application/json"}

    @staticmethod
    def _error_type(payload: Any) -> str:
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                return str(err.get("type") or "")
        return ""

    @staticmethod
    def _error_message(payload: Any) -> str:
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or "")
        return ""

    def _raise_http(self, status: int, payload: Any, text: str, *, is_fetch: bool) -> None:
        """Map the official per-endpoint status table onto ProviderError.

        401 is always a credential problem. 403 is *ambiguous*: a bare 403 means
        the account is inactive/suspended, while a 403 with ``type ==
        security_risk`` on /fetch means the URL was blocked — that one must not
        be reported as "check your API key".
        """
        etype = self._error_type(payload)
        message = self._error_message(payload)
        detail = f"{etype}: {message}" if message else (text or "")[:200]
        if status == 401:
            raise ProviderError(f"kimi: missing or invalid API key (HTTP 401): {detail}",
                                auth_failure=True)
        if status == 403:
            if is_fetch and etype == "security_risk":
                raise ProviderError(f"kimi: fetch blocked by security risk control (HTTP 403): {detail}")
            raise ProviderError(f"kimi: account inactive or suspended (HTTP 403): {detail}")
        if status == 404:
            raise ProviderError(f"kimi: no extractable content (HTTP 404): {detail}")
        if status == 408:
            raise ProviderError(f"kimi: client canceled the request (HTTP 408): {detail}")
        if status == 429:
            raise ProviderError(f"kimi: HTTP 429 rate limited: {detail}")
        if 500 <= status < 600:
            raise ProviderError(f"kimi: HTTP {status} upstream error: {detail}")
        raise ProviderError(f"kimi: HTTP {status}: {detail}")

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        tier = config.kimi_search_tier()
        path = "/v1/tools/search_pro" if tier == "pro" else "/v1/tools/search"

        def _call() -> SearchResult:
            if not self.api_key():
                raise ProviderError("kimi: no API key configured")
            body: Dict[str, Any] = {
                "text_query": (query or "").strip(),
                "limit": max(1, min(int(limit or 5), MAX_LIMIT)),
            }
            if tier == "pro":
                # /search_pro documents no include_content; it returns chunks.
                pass
            else:
                body["include_content"] = False
            resp = httpx.post(f"{config.kimi_base_url()}{path}", json=body,
                              timeout=self._timeout(timeout_override),
                              headers=self._headers())
            payload: Any = {}
            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except Exception:  # noqa: BLE001 — 401/403 carry no body at all
                    payload = {}
                self._raise_http(resp.status_code, payload, resp.text, is_fetch=False)

            data = resp.json()
            rows = []
            for item in (data.get("search_results") or []):
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                snippet = str(item.get("snippet") or "")
                chunks = item.get("chunks") or []
                score = None
                if chunks:
                    first = chunks[0] if isinstance(chunks[0], dict) else {}
                    snippet = snippet or str(first.get("text") or "")
                    score = first.get("score")
                elif item.get("text"):
                    # include_content=true would fill `text`; we ask for false,
                    # but honour it if a gateway ever returns it anyway.
                    snippet = snippet or str(item.get("text") or "")
                rows.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "snippet": snippet,
                    "published": str(item.get("date") or ""),
                    "site": str(item.get("site_name") or ""),
                    "score": score,
                })
                if len(rows) >= limit:
                    break
            if not rows:
                # Empty search_results is a documented, non-billable answer.
                raise ProviderError("no results")
            return SearchResult(results=rows, provider=self.NAME, mode=AuthMode.KEYED)
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        def _call() -> FetchResult:
            if not self.api_key():
                raise ProviderError("kimi: no API key configured")
            target = (url or "").strip()
            if not target.lower().startswith(("http://", "https://")):
                # Documented: only http/https are supported (400 invalid_url).
                raise ProviderError(f"kimi: invalid url, only http/https supported: {target[:80]}")
            resp = httpx.post(f"{config.kimi_base_url()}/v1/tools/fetch",
                              json={"url": target},
                              timeout=self._timeout(timeout_override),
                              headers=self._headers())
            payload: Any = {}
            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except Exception:  # noqa: BLE001
                    payload = {}
                self._raise_http(resp.status_code, payload, resp.text, is_fetch=True)

            data = resp.json()
            content = str(data.get("markdown") or "")[:config.extract_char_limit()]
            if not content.strip():
                raise ProviderError("kimi: empty markdown")
            return FetchResult(url=str(data.get("url") or target), content=content,
                               provider=self.NAME, mode=AuthMode.KEYED,
                               title=str(data.get("title") or ""),
                               content_type="text_markdown")
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result
