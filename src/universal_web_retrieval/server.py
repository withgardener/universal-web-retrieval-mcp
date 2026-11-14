"""universal-web-retrieval MCP server — standard MCP stdio, zero agent coupling.

Tools:
  web_search(query, limit=5, providers=None)   — configurable chain, auto keyed/keyless
  web_fetch(url | urls, providers=None)        — same chain model, per-URL attempts
  list_providers()                             — capabilities, key status, chain positions
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time

from mcp.server.mcpserver import MCPServer

from . import config
from .config import max_results_cap as _max_results_cap
from .errors import Capability, InvalidInputError, ProviderError
from .providers import registry as provider_registry
from .providers.router import ProviderRouter
from .redact import scrub_diagnostic, scrub_exact_secrets, scrub_payload

logging.basicConfig(level=getattr(logging, config.log_level(), logging.WARNING),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    stream=sys.stderr)

router = ProviderRouter()


def _json(payload) -> str:
    """Serialise a tool payload without breaking successful content.

    Diagnostic fields (``error``, ``attempts`` outcomes) are scrubbed
    structurally; after serialisation only *configured credential values* are
    removed from the whole document. Credential-looking query parameters are
    NOT rewritten here: a successful payload's signed URLs must keep working
    (see ``redact`` for the trust boundary).
    """
    return scrub_exact_secrets(json.dumps(scrub_payload(payload),
                                          ensure_ascii=False, indent=2))


# Backwards-compatible name: the diagnostic scrubber (also used by the router's
# log lines). Kept because callers and tests referenced it before 0.3.1.
_redact_secrets = scrub_diagnostic

MAX_RESULTS_CAP = _max_results_cap()

from . import __version__

app = MCPServer(
    "universal-web-retrieval",
    version=__version__,
    description=("A universal MCP server providing resilient web search and content "
                 "retrieval through a configurable provider chain (AnySearch, Tavily, "
                 "DDGS, Tencent Yuanbao Search, Bocha, DeepSeek, Kimi, Zhipu, Brave, "
                 "Jina Reader) with automatic failover."),
)


@app.tool(name="web_search", description=(
    "Web search through a configurable provider chain with automatic failover. "
    "Two modes: `mode=\"failover\"` (default) walks the chain one provider at a time "
    "and returns the first success; `mode=\"fanout\"` queries up to UWR_SEARCH_FANOUT "
    "providers concurrently, then merges and rank-fuses the results (provider=\"fanout\", "
    "every row carries `sources`; costs N× provider quota per search). "
    "A provider answering OK with zero hits is an empty ANSWER, not an error: failover "
    "keeps walking but a query with no matches succeeds with `results: []` (attempt "
    "``{\"outcome\": \"empty\"}``), never \"all providers failed\". "
    "Default chain: AnySearch -> Tavily -> DDGS; operators can reorder/extend it "
    "with UWR_SEARCH_CHAIN (e.g. anysearch,tavily,yuanbao,bocha,deepseek,kimi,ddgs) "
    "and pick the default mode with UWR_SEARCH_MODE. Auth is "
    "automatic: a configured API key enables keyed mode, otherwise the provider's "
    "official keyless access is used. Returns JSON: "
    "{success, provider, mode, latency_ms, attempts, results: [{title, url, snippet, "
    "published?, site?, sources?}]}. `providers` overrides the chain order for this "
    "one call (e.g. [\"bocha\"] or [\"yuanbao\",\"tavily\"]); in fanout mode its first "
    "N eligible entries are queried."))
def web_search_tool(query: str, limit: int = 5, providers: list[str] | None = None,
                    mode: str | None = None) -> str:
    try:
        result = router.search(str(query),
                               max(1, min(int(limit or 5), MAX_RESULTS_CAP)),
                               providers=providers, mode=mode)
        return _json(result.to_dict())
    except InvalidInputError as exc:
        return _json({"success": False, "error": f"invalid input: {exc}"})
    except ProviderError as exc:
        return _json({"success": False, "error": str(exc)[:500],
                      "attempts": getattr(exc, "attempts", [])})


@app.tool(name="web_fetch", description=(
    "Fetch one URL's page content as Markdown with automatic provider failover "
    "(default: AnySearch -> Tavily -> DDGS.extract; operators can reorder the chain "
    "with UWR_FETCH_CHAIN). Auth is automatic (keyed when a key is configured, else "
    "official keyless). Accepts a single url string, or urls array (max 5) for "
    "batch. `providers` overrides the chain order for this call. Returns JSON array "
    "of {url, content, title, content_type, truncated, original_length, provider, "
    "mode, latency_ms, attempts} — `truncated: true` means `content` was cut at "
    "WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT and `original_length` is the pre-cut character "
    "count; failed URLs carry an `error` field instead. A CDN/CAPTCHA interstitial "
    "(Cloudflare \"Just a moment…\", Akamai \"Access Denied\") is not returned as "
    "content: it is a fallback-eligible error, so the chain tries the next provider. "
    "Private, loopback and link-local targets are fetched like any other URL by "
    "default; with WEB_RETRIEVAL_BLOCK_PRIVATE=1 they are refused instead. That "
    "opt-in check is a target heuristic, not SSRF protection (no protection "
    "against DNS rebinding or a redirect hop at the provider). Note: JS-rendered, "
    "login-walled or CAPTCHA-protected pages are not guaranteed."))
def web_fetch_tool(url: str = "", urls: list[str] | None = None,
                   providers: list[str] | None = None) -> str:
    raw = [u for u in ([url] if url else []) + list(urls or []) if u]
    if not raw:
        return _json({"success": False, "error": "provide `url` (string) or `urls` (array, max 5)"})
    if len(raw) > 5:
        return _json({"success": False,
                      "error": f"too many URLs: {len(raw)} (max 5 per call)"})
    # Stable dedup preserving first-seen order.
    targets = list(dict.fromkeys(raw))
    # Validate the per-call chain once, before any network attempt, so a typo does
    # not silently degrade to the default chain for every URL in the batch.
    try:
        router.chain(Capability.FETCH, providers)
    except InvalidInputError as exc:
        return _json({"success": False, "error": f"invalid input: {exc}"})
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
            out.append(router.fetch(u, deadline=batch_deadline,
                                    providers=providers).to_dict())
        except InvalidInputError as exc:
            out.append({"url": u, "error": f"invalid input: {exc}", "content": "",
                        "title": "", "provider": "", "mode": ""})
        except ProviderError as exc:
            out.append({"url": u, "error": str(exc)[:400], "content": "",
                        "title": "", "provider": "", "mode": "",
                        "attempts": getattr(exc, "attempts", [])})
    return _json(out)


@app.tool(name="list_providers", description=(
    "List every provider this server knows: capabilities (search/fetch), whether it "
    "is usable right now (configured / key present), whether it is enabled, and its "
    "1-based position in each active chain. Also reports the active search mode "
    "(failover/fanout), the fan-out fan width/grace, and the fetch policy "
    "(private-network access, block-page detection, extract limit). Never returns key "
    "values. Use it to decide what to pass in web_search/web_fetch `providers`."))
def list_providers_tool() -> str:
    disabled = config.disabled_providers()
    positions = {}
    for capability in (Capability.SEARCH, Capability.FETCH):
        for index, provider in enumerate(router.chain(capability), start=1):
            positions.setdefault(provider.NAME, {})[capability.value] = index
    rows = []
    for name in provider_registry.provider_names():
        provider = provider_registry.registry()[name]
        rows.append({
            "name": name,
            "kind": provider.KIND,
            "capabilities": sorted(c.value for c in provider.CAPABILITIES),
            "keyed_only": provider.KEYED_ONLY,
            "configured": provider.is_configured(),
            "auth_mode": provider.auth_mode_name() if provider.is_configured() else None,
            "enabled": name not in disabled,
            "position": {cap: positions.get(name, {}).get(cap)
                         for cap in ("search", "fetch")
                         if provider.supports(Capability(cap))},
        })
    return _json({
        "chains": {"search": router.resolve_names(Capability.SEARCH),
                   "fetch": router.resolve_names(Capability.FETCH)},
        "defaults": {"search": list(config.DEFAULT_SEARCH_CHAIN),
                     "fetch": list(config.DEFAULT_FETCH_CHAIN)},
        "search_mode": config.search_mode(),
        "fanout": {"n": config.fanout_providers(), "grace_s": config.fanout_grace()},
        "fetch_policy": {
            "block_private_network": config.block_private_network(),
            "block_detection": config.block_detection_enabled(),
            "extract_char_limit": config.extract_char_limit(),
        },
        "disabled": sorted(disabled),
        "providers": rows,
    })


def main() -> None:
    asyncio.run(app.run_stdio_async())


if __name__ == "__main__":
    main()
