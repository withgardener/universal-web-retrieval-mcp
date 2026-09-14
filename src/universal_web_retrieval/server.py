"""universal-web-retrieval MCP server — standard MCP stdio, zero agent coupling.

Tools:
  web_search(query, limit=5)  — AnySearch -> Tavily -> DDGS, auto keyed/keyless
  web_fetch(url | urls)       — AnySearch -> Tavily -> DDGS.extract, same auth auto
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time

from mcp.server.mcpserver import MCPServer

from . import config
from .config import max_results_cap as _max_results_cap
from .errors import InvalidInputError, ProviderError
from .providers.router import ProviderRouter

logging.basicConfig(level=getattr(logging, config.log_level(), logging.WARNING),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stderr)

router = ProviderRouter()

_URL_SECRET_RE = re.compile(r"(?i)([?&](?:token|api_key|apikey|key|secret|password|sig|signature|access_token)=)[^&\s]+")


def _redact_secrets(text: str) -> str:
    """Redact credential-looking query parameters in URLs inside error text."""
    return _URL_SECRET_RE.sub(r"\1<redacted>", text)
MAX_RESULTS_CAP = _max_results_cap()

from . import __version__

app = MCPServer(
    "universal-web-retrieval",
    version=__version__,
    description=("A universal MCP server providing resilient web search and content "
                 "retrieval through automatic provider failover across AnySearch, "
                 "Tavily, and DDGS."),
)


@app.tool(name="web_search", description=(
    "Web search with automatic provider failover (AnySearch -> Tavily -> DDGS). "
    "Auth is automatic: a configured API key enables keyed mode, otherwise the "
    "provider's official keyless access is used. Returns JSON: "
    "{success, provider, mode, latency_ms, results: [{title, url, snippet}]}."))
def web_search_tool(query: str, limit: int = 5) -> str:
    try:
        result = router.search(str(query), max(1, min(int(limit or 5), MAX_RESULTS_CAP)))
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    except InvalidInputError as exc:
        return json.dumps({"success": False, "error": f"invalid input: {exc}"}, ensure_ascii=False)
    except ProviderError as exc:
        return json.dumps({"success": False, "error": _redact_secrets(str(exc))[:500]}, ensure_ascii=False)


@app.tool(name="web_fetch", description=(
    "Fetch one URL's page content as Markdown with automatic provider failover "
    "(AnySearch -> Tavily -> DDGS.extract). Auth is automatic (keyed when a key "
    "is configured, else official keyless). Accepts a single url string, or "
    "urls array (max 5) for batch. Returns JSON array of "
    "{url, content, title, content_type, provider, mode, latency_ms} — failed "
    "URLs carry an `error` field instead. Note: JS-rendered, login-walled or "
    "CAPTCHA-protected pages are not guaranteed."))
def web_fetch_tool(url: str = "", urls: list[str] | None = None) -> str:
    raw = [u for u in ([url] if url else []) + list(urls or []) if u]
    if not raw:
        return json.dumps({"success": False, "error": "provide `url` (string) or `urls` (array, max 5)"},
                          ensure_ascii=False)
    if len(raw) > 5:
        return json.dumps({"success": False,
                           "error": f"too many URLs: {len(raw)} (max 5 per call)"},
                          ensure_ascii=False)
    # Stable dedup preserving first-seen order.
    targets = list(dict.fromkeys(raw))
    # ONE overall deadline for the whole batch (not per-URL).
    batch_deadline = time.monotonic() + config.chain_deadline()
    out = []
    for u in targets:
        remaining = batch_deadline - time.monotonic()
        if remaining <= 0:
            out.append({"url": u, "error": f"batch deadline ({config.chain_deadline():.0f}s) reached "
                        "before this URL — no network call attempted", "content": "",
                        "title": "", "provider": "", "mode": ""})
            continue
        try:
            out.append(router.fetch(u, deadline=batch_deadline).to_dict())
        except InvalidInputError as exc:
            out.append({"url": u, "error": f"invalid input: {exc}", "content": "",
                        "title": "", "provider": "", "mode": ""})
        except ProviderError as exc:
            out.append({"url": u, "error": _redact_secrets(str(exc))[:400], "content": "",
                        "title": "", "provider": "", "mode": ""})
    return json.dumps(out, ensure_ascii=False, indent=2)


def main() -> None:
    asyncio.run(app.run_stdio_async())


if __name__ == "__main__":
    main()
