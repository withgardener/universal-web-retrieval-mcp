"""Jina Reader (r.jina.ai) — URL-to-Markdown, fetch only, keyless by default.

Verified 2026-10-20 against the official docs and OpenAPI spec:

  Product page : https://jina.ai/reader           (endpoint + rate-limit table)
  OpenAPI      : https://docs.jina.ai/openapi.json

    GET https://r.jina.ai/?url=<target>          (OpenAPI path `/`, query `url`)
    GET https://r.jina.ai/<target>               (documented Usage 1)
    Headers  Accept: application/json            -> JSON envelope
             Authorization: Bearer <JINA_API_KEY>  (OPTIONAL)
    Result   {"code": 200, "status": 20000,
              "data": {"title": str, "url": str, "content": str,
                       "description": str, "publishedTime": str, ...},
              "meta": {...}}

The OpenAPI spec declares the endpoint's security as ``[{"HTTPBearer": []}, {}]``
— i.e. an empty requirement is allowed — and the product page's rate-limit table
lists "w/o API Key **20 RPM**", "w/ Free API Key 500 RPM", "w/ Paid API Key 5000
RPM". Keyless use is therefore documented, so this provider is ``KEYED_ONLY =
False`` and, precisely because it works without a key, it is **not** part of any
default chain: it only runs when the operator names it.

The ``?url=`` query form is used rather than ``/<url>`` so a target that itself
carries a query string cannot be re-parsed as a Reader parameter.

Content-type note: ``Accept: application/json`` is what makes the reply a JSON
envelope; without it Reader returns raw Markdown text. Both are documented, and
the JSON form is used because it carries the title and status metadata.

Errors (OpenAPI response set): 400/401/402/403/409/413/422/429/451/500/503. A
401 only means "bad key" when a key was actually sent — a keyless 401 is an
access/quota problem and ``classify_http_error`` is told so.

``UWR_JINA_BASE_URL`` overrides the host, ``UWR_JINA_TIMEOUT`` the read budget
(Reader drives a headless browser; default 30s). ``X-Timeout`` is capped at 180s
by the API and is set from the remaining chain budget.
"""
from __future__ import annotations

from typing import Dict

import httpx

from .. import config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult, classify_http_error)

MAX_X_TIMEOUT = 180


class JinaProvider(Provider):
    NAME = "jina"
    CAPABILITIES = frozenset({Capability.FETCH})
    KEYED_ONLY = False
    KIND = "fetch"

    def api_key(self) -> str:
        return config.jina_api_key()

    def _timeout(self, override: float | None):
        configured = config.jina_timeout()
        if override is None:
            return configured
        bounded = min(override, 1.0) if override < 1.0 else override
        return (min(configured[0], bounded), min(configured[1], bounded))

    def _headers(self, override: float | None) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key():
            headers["Authorization"] = f"Bearer {self.api_key()}"
        # Align Reader's own server-side cutoff with the chain budget so a slow
        # render cannot outlive the caller's deadline. Official range: 1-180s.
        budget = override if override is not None else config.jina_timeout()[1]
        headers["X-Timeout"] = str(max(1, min(MAX_X_TIMEOUT, int(budget))))
        return headers

    @staticmethod
    def _body_error(payload) -> str:
        if isinstance(payload, dict):
            for key in ("readableMessage", "message"):
                if payload.get(key):
                    return str(payload[key])
            data = payload.get("data")
            if isinstance(data, dict) and data.get("warning"):
                return str(data["warning"])
        return ""

    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        raise ProviderError("jina: search capability not offered by this provider")

    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        def _call() -> FetchResult:
            target = (url or "").strip()
            if not target.lower().startswith(("http://", "https://")):
                raise ProviderError(f"jina: unsupported url scheme: {target[:60]}")
            resp = httpx.get(f"{config.jina_base_url()}/",
                             params={"url": target},
                             timeout=self._timeout(timeout_override),
                             headers=self._headers(timeout_override))
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001 — an error page may be plain text
                payload = {}

            if resp.status_code >= 400:
                detail = self._body_error(payload) or (resp.text or "")[:300]
                if resp.status_code == 451:
                    raise ProviderError(f"jina: content unavailable for legal reasons "
                                        f"(HTTP 451): {detail[:200]}")
                raise classify_http_error(resp.status_code, detail,
                                          authenticated=bool(self.api_key()))

            code = payload.get("code") if isinstance(payload, dict) else None
            data = payload.get("data") if isinstance(payload, dict) else None
            if code not in (None, 200) or not isinstance(data, dict):
                detail = self._body_error(payload) or str(payload)[:300]
                raise ProviderError(f"jina: envelope code {code}: {detail[:200]}")

            content = str(data.get("content") or "")[:config.extract_char_limit()]
            if not content.strip():
                raise ProviderError("jina: empty content")
            return FetchResult(url=str(data.get("url") or target), content=content,
                               provider=self.NAME, mode=self.mode(),
                               title=str(data.get("title") or ""),
                               content_type="text_markdown")
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result
