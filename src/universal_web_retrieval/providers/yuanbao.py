"""Tencent Cloud Yuanbao Search (元宝搜索, product code ``wsa``) — official MCP endpoint.

Official interface (verified 2026-10-20):
  MCP marketplace page: https://cloud.tencent.com/developer/mcp/server/11764
  API-3.0 sibling doc:  https://cloud.tencent.com/document/product/1806/121811

    POST https://api.wsa.cloud.tencent.com/Mcp
    Transport        JSON-RPC 2.0 over Streamable HTTP, MCP protocol 2025-06-18
    Auth             Authorization: Bearer <WSA_API_KEY>   (a.k.a. YUANBAO_API_KEY)
    Tools            exactly one: SearchPro
    SearchPro args   Query (required, <=1500 UTF-8 bytes), Site, Mode (0 web /
                     1 vertical / 2 mixed), Freshness (d1-30 / m1-12 / y1-5),
                     Cnt ("10"|"20"|"30"|"40"|"50"), Industry
    Result           Pages: array of JSON *strings*, each {title, date, url,
                     passage, content, site, score, authority_level, pics,
                     favicon, deeplinks}

Why the MCP endpoint rather than the signed API-3.0 endpoint
------------------------------------------------------------
API-3.0 (``wsa.tencentcloudapi.com``, ``X-TC-Action: SearchPro``,
``X-TC-Version: 2025-05-08``) needs TC3-HMAC-SHA256 request signing, i.e. a
SecretId *and* a SecretKey, plus a re-implementation of Tencent's signature
algorithm. The MCP endpoint takes a single service key and is what Tencent
documents for agent use, so it is both simpler and the more stable contract for
this server. Both return the same fields (the MCP tool mirrors API-3.0).

Error mapping (from the MCP page + product error codes):
  UnauthorizedOperation  -> auth_failure (wrong/deleted key)
  ResourceNotFound       -> service not activated      (fallback-eligible)
  ResourceUnavailable    -> account arrears            (fallback-eligible)
  RequestLimitExceeded   -> rate limit                 (fallback-eligible)

Search-only: the product exposes no page-extraction API.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from .. import __version__, config
from ..errors import (AuthMode, Capability, FetchResult, Provider, ProviderError,
                      SearchResult, classify_http_error)

log = logging.getLogger("universal_web_retrieval.yuanbao")

ALLOWED_CNT = (10, 20, 30, 40, 50)
MAX_QUERY_BYTES = 1500
PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "SearchPro"


def cnt_for_limit(limit: int) -> str:
    """Round a requested result count UP to the nearest supported bucket.

    Yuanbao only accepts 10/20/30/40/50; asking for fewer than 10 still bills
    one call, so we take the smallest bucket that can satisfy ``limit`` and
    truncate locally afterwards.
    """
    try:
        wanted = int(limit)
    except (TypeError, ValueError):
        wanted = 10
    for bucket in ALLOWED_CNT:
        if wanted <= bucket:
            return str(bucket)
    return str(ALLOWED_CNT[-1])


def clamp_query(query: str) -> str:
    """Trim a query to 1500 UTF-8 bytes without splitting a character."""
    raw = (query or "").encode("utf-8")
    if len(raw) <= MAX_QUERY_BYTES:
        return query
    return raw[:MAX_QUERY_BYTES].decode("utf-8", "ignore")


def _classify_rpc_error(payload: Dict[str, Any], *, status: Optional[int] = None) -> ProviderError:
    """Map a JSON-RPC error / Tencent error code to a classified ProviderError."""
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        text = f"{err.get('message', '')} {json.dumps(err.get('data'), ensure_ascii=False)[:200]}"
        code = str(err.get("code", ""))
    else:
        text = json.dumps(payload, ensure_ascii=False)[:300] if payload else ""
        code = ""
    probe = f"{code} {text}"
    low = probe.lower()

    def _match(*needles: str) -> bool:
        return any(n in probe or n.lower() in low for n in needles)

    if status in (401, 403) or _match("UnauthorizedOperation", "AuthFailure", "unauthorized"):
        return ProviderError(f"yuanbao unauthorized: {text[:200]}", auth_failure=True)
    if _match("ResourceNotFound"):
        return ProviderError(f"yuanbao service not activated (ResourceNotFound): {text[:160]}")
    if _match("ResourceUnavailable"):
        return ProviderError(f"yuanbao account unavailable / arrears (ResourceUnavailable): {text[:160]}")
    if _match("RequestLimitExceeded"):
        return ProviderError(f"yuanbao rate limited (RequestLimitExceeded): {text[:160]}")
    if _match("InvalidParameter", "InvalidParameterValue"):
        return ProviderError(f"yuanbao rejected the request parameters: {text[:160]}")
    return ProviderError(f"yuanbao error: {text[:200]}")


def _unwrap(payload: Any) -> Dict[str, Any]:
    """Accept either the MCP tool payload or an API-3.0 ``{"Response": {...}}`` shell."""
    if not isinstance(payload, dict):
        raise ProviderError(f"yuanbao: unexpected payload type {type(payload).__name__}")
    if "Response" in payload and isinstance(payload["Response"], dict):
        payload = payload["Response"]
    return payload


def _pages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull ``Pages`` out of the payload, decoding the per-item JSON strings."""
    raw = payload.get("Pages")
    if raw is None:
        for alt in ("pages", "Results", "results", "Data", "data"):
            if isinstance(payload.get(alt), (list,)):
                raw = payload[alt]
                break
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
        elif isinstance(item, str):
            try:
                decoded = json.loads(item)
            except ValueError:
                continue
            if isinstance(decoded, dict):
                out.append(decoded)
    return out


class YuanbaoProvider(Provider):
    NAME = "yuanbao"
    CAPABILITIES = frozenset({Capability.SEARCH})
    KEYED_ONLY = True   # no keyless/anonymous access exists for this product
    KIND = "search"

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._session_for: Optional[tuple] = None
        self._rpc_id = 0

    # ---- transport -----------------------------------------------------
    def api_key(self) -> str:
        return config.yuanbao_api_key()

    def _timeout(self, override: float | None):
        configured = config.yuanbao_timeout()
        if override is None:
            return configured
        bounded = min(override, 1.0) if override < 1.0 else override
        return (min(configured[0], bounded), min(configured[1], bounded))

    def _headers(self, session: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key()}",
            "Content-Type": "application/json",
            # Streamable HTTP servers may answer either with JSON or with SSE.
            "Accept": "application/json, text/event-stream",
        }
        if session:
            headers["Mcp-Session-Id"] = session
        return headers

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    @staticmethod
    def _decode(resp: httpx.Response) -> Dict[str, Any]:
        """Decode a JSON-RPC response body — plain JSON or an SSE ``data:`` stream."""
        text = resp.text or ""
        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype or text.lstrip().startswith(("event:", "data:", "id:")):
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        return json.loads(chunk)
                    except ValueError:
                        continue
            raise ProviderError("yuanbao: SSE response contained no JSON-RPC payload")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ProviderError(f"yuanbao: non-JSON response ({text[:120]!r})") from exc

    def _post(self, body: Dict[str, Any], timeout, session: Optional[str]) -> httpx.Response:
        return httpx.post(config.yuanbao_mcp_url(), json=body,
                          timeout=timeout, headers=self._headers(session))

    def _check_http(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400 and resp.status_code != 404:
            # 404 is handled by the caller (session recovery); anything else here
            # is a transport-level failure.
            raise classify_http_error(resp.status_code, resp.text, authenticated=True)

    def _initialize(self, timeout) -> Optional[str]:
        resp = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "universal-web-retrieval", "version": __version__},
            },
        }, timeout, session=None)
        self._check_http(resp)
        if resp.status_code == 404:
            # Endpoint answered but does not implement initialize — fall through to a
            # stateless tools/call rather than failing the link outright.
            return None
        payload = self._decode(resp)
        if "error" in payload:
            raise _classify_rpc_error(payload, status=resp.status_code)
        session = resp.headers.get("mcp-session-id")
        # Best-effort "initialized" notification (no id, no response expected).
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout, session)
        except Exception:  # noqa: BLE001 — a missing ack must not break the search
            log.debug("yuanbao: initialized notification failed (ignored)", exc_info=True)
        self._session_id = session
        self._session_for = (config.yuanbao_mcp_url(), self.api_key())
        return session

    def _session(self, timeout) -> Optional[str]:
        key = (config.yuanbao_mcp_url(), self.api_key())
        if self._session_id and self._session_for == key:
            return self._session_id
        return self._initialize(timeout)

    def _reset_session(self) -> None:
        self._session_id = None
        self._session_for = None

    def _call_tool(self, arguments: Dict[str, Any], timeout) -> Dict[str, Any]:
        for attempt in range(2):
            session = self._session(timeout)
            resp = self._post({
                "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                "params": {"name": TOOL_NAME, "arguments": arguments},
            }, timeout, session)
            if resp.status_code == 404 and session and attempt == 0:
                # Session expired/unknown server-side: re-handshake once.
                self._reset_session()
                continue
            self._check_http(resp)
            payload = self._decode(resp)
            if "error" in payload:
                raise _classify_rpc_error(payload, status=resp.status_code)
            result = payload.get("result")
            if not isinstance(result, dict):
                raise ProviderError(f"yuanbao: malformed tools/call result: {str(payload)[:200]}")
            if result.get("isError"):
                raise _classify_rpc_error(result)
            return result
        raise ProviderError("yuanbao: session could not be established (HTTP 404 after re-initialize)")

    @staticmethod
    def _payload_from_result(result: Dict[str, Any]) -> Dict[str, Any]:
        """Extract the SearchPro payload from an MCP ``tools/call`` result."""
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and structured:
            return _unwrap(structured)
        texts = []
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
        blob = "\n".join(t for t in texts if t).strip()
        if not blob:
            raise ProviderError("yuanbao: empty tools/call content")
        try:
            return _unwrap(json.loads(blob))
        except ValueError:
            # Some gateways wrap the JSON in prose; salvage the outermost object.
            start, end = blob.find("{"), blob.rfind("}")
            if 0 <= start < end:
                try:
                    return _unwrap(json.loads(blob[start:end + 1]))
                except ValueError:
                    pass
            raise ProviderError(f"yuanbao: unparseable SearchPro payload ({blob[:160]!r})")

    # ---- capability ----------------------------------------------------
    def search(self, query: str, limit: int, timeout_override: float | None = None) -> SearchResult:
        def _call() -> SearchResult:
            if not self.api_key():
                raise ProviderError("yuanbao: no API key configured", fallback_eligible=True)
            result = self._call_tool({"Query": clamp_query(query), "Cnt": cnt_for_limit(limit)},
                                     self._timeout(timeout_override))
            payload = self._payload_from_result(result)
            if payload.get("Error"):
                raise _classify_rpc_error(payload)
            rows = []
            for page in _pages(payload):
                url = str(page.get("url") or "").strip()
                if not url:
                    continue
                rows.append({
                    "title": str(page.get("title") or ""),
                    "url": url,
                    "snippet": str(page.get("passage") or page.get("content") or ""),
                    "published": str(page.get("date") or ""),
                    "site": str(page.get("site") or ""),
                    "score": page.get("score"),
                })
                if len(rows) >= limit:
                    break
            if not rows:
                raise ProviderError("yuanbao: no results")
            return SearchResult(results=rows, provider=self.NAME, mode=AuthMode.KEYED)
        result, elapsed = self._timed(_call)
        result.latency_ms = elapsed
        return result

    def fetch(self, url: str, timeout_override: float | None = None) -> FetchResult:
        raise ProviderError("yuanbao: fetch capability not offered by this provider")
