# Universal Web Retrieval MCP

A universal MCP server providing resilient web search and content retrieval through a **configurable provider chain** — AnySearch, Tavily, DDGS, Tencent Yuanbao Search, Bocha, DeepSeek, Kimi, Zhipu BigModel, Brave Search and Jina Reader — with automatic failover.

## Overview

Give any MCP-capable agent reliable web access through a single server. Universal Web Retrieval routes each request through an ordered provider chain, picks keyed or keyless authentication automatically based on which API keys are present, and falls over to the next provider on rate limits, outages, or upstream errors.

As of **0.2.0** the chain is no longer hard-coded: operators define it with environment variables, can disable individual providers, and agents can override the order for a single call.

```
Agent (any MCP client)
│
├── web_search ──▶ chain from UWR_SEARCH_CHAIN   (default: AnySearch → Tavily → DDGS)
└── web_fetch  ──▶ chain from UWR_FETCH_CHAIN    (default: AnySearch → Tavily → DDGS.extract)
```

## Features

- **Three standard tools** — `web_search`, `web_fetch`, and `list_providers`, clean JSON in/out.
- **Configurable chains** — `UWR_SEARCH_CHAIN` / `UWR_FETCH_CHAIN` set the order; `UWR_DISABLED_PROVIDERS` switches providers off without editing the chains.
- **Per-call override** — `web_search(..., providers=["bocha"])` or `["yuanbao","tavily"]` runs just that order for one call.
- **Automatic provider failover** — one provider failing never fails the tool.
- **Auto keyed/keyless** — key configured → official keyed API; key absent → official keyless access (where the provider offers one). Keyed-only providers (Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave) without a key are *skipped*, never attempted, so a missing key cannot 401 the chain.
- **Strict official APIs** — every provider is called through its official documented endpoint and auth mechanism. No scraping hacks, no undocumented endpoints.
- **Error classification** — rate limits / outages fall over; invalid credentials fail loudly (ERROR log) and move to the next provider; malformed input never triggers fallback.
- **Bounded latency** — per-provider timeouts plus an overall chain deadline.
- **Observable** — every response carries `provider`, `mode`, `latency_ms` and an `attempts` trail.
- **Zero agent coupling** — standard MCP stdio; works with Hermes, Claude Code, OpenCode, or any MCP client.

## Architecture

```
src/universal_web_retrieval/
├── server.py            # MCP tool surface (web_search / web_fetch / list_providers)
├── config.py            # env-driven settings, call-time key + chain reads
├── errors.py            # ProviderError classification, result models, Provider ABC
└── providers/
    ├── registry.py      # name -> provider instance; capability lookup
    ├── router.py        # chain resolution (env / override / default) + failover
    ├── anysearch.py     # AnySearch (keyed Bearer / keyless no-auth-header)
    ├── tavily.py        # Tavily (keyed Bearer / keyless X-Tavily-Access-Mode)
    ├── ddgs.py          # DDGS (official package; text + extract)
    ├── yuanbao.py       # Tencent Yuanbao Search (MCP over Streamable HTTP, Bearer)
    ├── bocha.py         # Bocha Web Search API (REST, Bearer)
    ├── deepseek.py      # DeepSeek Search + Fetch API (REST, Bearer)
    ├── kimi.py          # Kimi/Moonshot search + search_pro + fetch (REST, Bearer)
    ├── zhipu.py         # Zhipu BigModel web_search (REST, Bearer)
    ├── brave.py         # Brave Search API (REST, X-Subscription-Token)
    └── jina.py          # Jina Reader r.jina.ai (keyless or Bearer, fetch only)
```

## Installation

```bash
# from PyPI (distribution name):
pip install uwr
# or from source:
pip install git+https://github.com/withgardener/universal-web-retrieval-mcp.git

# run (stdio MCP):
uwr
# or:
PYTHONPATH=src python -m universal_web_retrieval
# version:
uwr --version
```

`ddgs` is a **runtime dependency** (the fixed final fallback of the default chains), not an optional extra. Dependencies are pinned to compatible ranges (`mcp>=2,<3`, `httpx>=0.27,<1`, `ddgs>=9.16,<10`) so a major-version breaking change never ships to a running server via a routine `pip update`.

## Configuration

### Chains

| Env var | Default | Effect |
|---|---|---|
| `UWR_SEARCH_CHAIN` | `anysearch,tavily,ddgs` | Ordered search chain, e.g. `anysearch,tavily,yuanbao,bocha,ddgs` |
| `UWR_FETCH_CHAIN` | `anysearch,tavily,ddgs` | Ordered fetch chain (search-only providers are dropped) |
| `UWR_DISABLED_PROVIDERS` | *(empty)* | Names switched off everywhere, e.g. `bocha,deepseek` |

Resolution rules:

1. A per-call `providers` argument wins (it only affects that call).
2. Otherwise `UWR_SEARCH_CHAIN` / `UWR_FETCH_CHAIN`.
3. Otherwise the built-in defaults above — **0.2.0 is behaviour-compatible with 0.1.x out of the box**.

Unknown names, providers lacking the capability, and disabled providers are dropped with **one warning per distinct chain** (not once per request). A chain that resolves to nothing falls back to the defaults. An explicitly empty/invalid *per-call* override is an input error instead — `providers=["bocha"]` never silently becomes the whole default chain.

### Keys and tuning

| Env var | Required | Effect |
|---|---|---|
| `ANYSEARCH_API_KEY` | no | AnySearch keyed mode; absent → official keyless |
| `TAVILY_API_KEY` | no | Tavily keyed mode; absent → official keyless (`X-Tavily-Access-Mode: keyless`) |
| `YUANBAO_API_KEY` (alias `WSA_API_KEY`) | for `yuanbao` | Tencent Yuanbao Search service key |
| `BOCHA_API_KEY` | for `bocha` | Bocha Web Search API key |
| `DEEPSEEK_API_KEY` | for `deepseek` | DeepSeek API key (Search + Fetch API) |
| `MOONSHOT_API_KEY` (alias `KIMI_API_KEY`) | for `kimi` | Kimi / Moonshot platform key |
| `ZHIPU_API_KEY` (alias `BIGMODEL_API_KEY`) | for `zhipu` | Zhipu BigModel (智谱) key |
| `BRAVE_API_KEY` | for `brave` | Brave Search API subscription token |
| `JINA_API_KEY` | no | Jina Reader key — optional (keyless 20 RPM, with free key 500 RPM) |
| `UWR_KIMI_SEARCH_TIER` | no | `basic` (default) → `/v1/tools/search` $0.002/call; `pro` → `/v1/tools/search_pro` $0.003/call |
| `UWR_ZHIPU_ENGINE` | no | `search_std` (default) / `search_pro` / `search_pro_sogou` / `search_pro_quark` |
| `WEB_RETRIEVAL_TIMEOUT` | no | Overall chain deadline in seconds (default 45) |
| `WEB_RETRIEVAL_LOG_LEVEL` | no | `WARNING` default; logs go to **stderr** (stdout is MCP JSON-RPC) |
| `WEB_RETRIEVAL_MAX_RESULTS` | no | Cap for `web_search.limit` (default 20) |
| `WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT` | no | Max fetched-content chars (default 15000) |
| `WEB_RETRIEVAL_ANYSEARCH_CONNECT_TIMEOUT` / `_TIMEOUT` | no | AnySearch connect/read timeouts (5s / 20s) |
| `WEB_RETRIEVAL_TAVILY_CONNECT_TIMEOUT` / `_TIMEOUT` | no | Tavily connect/read timeouts (5s / 20s) |
| `WEB_RETRIEVAL_DDGS_TIMEOUT` | no | DDGS overall timeout (15s) |
| `UWR_YUANBAO_CONNECT_TIMEOUT` / `UWR_YUANBAO_TIMEOUT` | no | Yuanbao timeouts (5s / 20s) |
| `UWR_BOCHA_CONNECT_TIMEOUT` / `UWR_BOCHA_TIMEOUT` | no | Bocha timeouts (5s / 20s) |
| `UWR_DEEPSEEK_CONNECT_TIMEOUT` / `UWR_DEEPSEEK_TIMEOUT` | no | DeepSeek timeouts (5s / 20s) |
| `UWR_KIMI_CONNECT_TIMEOUT` / `UWR_KIMI_TIMEOUT` | no | Kimi timeouts (5s / 20s) |
| `UWR_ZHIPU_CONNECT_TIMEOUT` / `UWR_ZHIPU_TIMEOUT` | no | Zhipu timeouts (5s / 20s) |
| `UWR_BRAVE_CONNECT_TIMEOUT` / `UWR_BRAVE_TIMEOUT` | no | Brave timeouts (5s / 20s) |
| `UWR_JINA_CONNECT_TIMEOUT` / `UWR_JINA_TIMEOUT` | no | Jina timeouts (5s / 30s — Reader drives a browser) |
| `UWR_YUANBAO_MCP_URL` | no | Override the Yuanbao MCP endpoint |
| `UWR_BOCHA_BASE_URL` | no | `https://api.bochaai.com` (or `https://api.bocha.cn`) |
| `UWR_DEEPSEEK_BASE_URL` | no | Default `https://api.deepseek.com` |
| `UWR_KIMI_BASE_URL` | no | `https://api.moonshot.cn` (default) or `https://api.moonshot.ai` (international) |
| `UWR_ZHIPU_BASE_URL` | no | Default `https://open.bigmodel.cn` |
| `UWR_BRAVE_BASE_URL` | no | Default `https://api.search.brave.com` |
| `UWR_JINA_BASE_URL` | no | Default `https://r.jina.ai` |

Every key is read **at call time**, so a restart of the MCP server process is enough for new values to take effect (for Hermes: the `mcp_servers` env is frozen at gateway start — restart the gateway or kill the child process).

## Tools

### `web_search(query, limit=5, providers=None)`

```json
{
  "success": true,
  "provider": "anysearch",
  "mode": "keyless",
  "latency_ms": 1555,
  "attempts": [{"provider": "anysearch", "outcome": "ok"}],
  "results": [{"title": "...", "url": "...", "snippet": "..."}]
}
```

When a provider exposes publication date / site (Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave), the result rows also carry `published`, `site` and (where scored) `score`. `provider` / `mode` / `latency_ms` / `attempts` are diagnostic metadata — consumers only need `results`.

`attempts` is the per-provider outcome trail in chain order:

| Outcome | Meaning |
|---|---|
| `ok` | This provider produced the answer |
| `error:<classification: message>` | Attempted and failed; the chain moved on |
| `skipped:not_configured` | Keyed-only provider without a key — never called |
| `skipped:deadline` | Chain deadline was already exhausted |

Sample failure of a mixed chain (`UWR_SEARCH_CHAIN=yuanbao,bocha,ddgs` with no Yuanbao/Bocha keys):

```json
{
  "success": false,
  "error": "all providers failed: yuanbao: skipped (not_configured) | bocha: skipped (not_configured) | ddgs(keyless): HTTP 429 rate limited: ...",
  "attempts": [
    {"provider": "yuanbao", "outcome": "skipped:not_configured"},
    {"provider": "bocha", "outcome": "skipped:not_configured"},
    {"provider": "ddgs", "outcome": "error:HTTP 429 rate limited: ..."}
  ]
}
```

Per-call override:

```json
{"query": "腾讯 财报", "providers": ["bocha"]}          // Bocha only
{"query": "腾讯 财报", "providers": ["yuanbao", "tavily"]}  // try Yuanbao first
```

An override that contains no usable provider (unknown name, wrong capability, disabled) is rejected with `invalid input: none of the requested providers can serve <capability> ... — available: ...`.

### `web_fetch(url | urls, providers=None)`

Accepts a single `url` string or a `urls` array (max 5) and an optional `providers` override. Returns a JSON array:

```json
[{
  "url": "https://example.com",
  "content": "# Example Domain\n\n...",
  "content_type": "text_markdown",
  "title": "Example Domain",
  "provider": "anysearch",
  "mode": "keyless",
  "latency_ms": 191,
  "attempts": [{"provider": "anysearch", "outcome": "ok"}]
}]
```

Failed URLs carry an `error` field instead of `content`. Content is Markdown/clean text — never raw HTML. JS-rendered, login-walled, or CAPTCHA-protected pages are not guaranteed (plain HTTP retrieval only; no browser automation).

### `list_providers()`

Returns every provider this build knows, with capability, configuration status, enabled flag and its 1-based position in each active chain. Key values are never returned.

```json
{
  "chains": {"search": ["anysearch", "tavily", "ddgs"], "fetch": ["anysearch", "tavily", "ddgs"]},
  "defaults": {"search": ["anysearch", "tavily", "ddgs"], "fetch": ["anysearch", "tavily", "ddgs"]},
  "disabled": [],
  "providers": [
    {"name": "bocha", "kind": "search", "capabilities": ["search"], "keyed_only": true,
     "configured": false, "auth_mode": null, "enabled": true, "position": {"search": null}},
    {"name": "anysearch", "kind": "search", "capabilities": ["fetch", "search"], "keyed_only": false,
     "configured": true, "auth_mode": "keyed", "enabled": true,
     "position": {"search": 1, "fetch": 1}}
  ]
}
```

### Network scope & SSRF note

`web_fetch` retrieves any URL the **host environment** can reach — this deliberately includes private-network and `localhost` targets, which is a legitimate capability for a local agent tool. Redirects may land on private or link-local destinations. When deploying against **untrusted agent input**, apply network-level isolation (container/netns/firewall) as appropriate for your threat model. A future `WEB_RETRIEVAL_BLOCK_PRIVATE=1` opt-in may add in-process filtering; it is intentionally not implemented yet.

Batch behavior: `urls` is capped at **5 per call** (larger batches are rejected with an explicit error, not silently truncated), duplicates are de-duplicated preserving first-seen order, and the **whole batch shares one deadline** — URLs whose turn arrives after the deadline return a timeout error without any network attempt. Error text containing URLs has credential-looking query parameters (`token=`, `api_key=`, ...) redacted.

## Routing & fallback

| Capability | Default chain | Optional members, on request only |
|---|---|---|
| `web_search` | AnySearch → Tavily → DDGS | Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave |
| `web_fetch` | AnySearch → Tavily → DDGS.extract | DeepSeek, Kimi, Jina (search-only providers are dropped from fetch chains) |

A link is skipped when: rate-limited (429), timed out, upstream 5xx, or any provider error. **Invalid credentials fail loudly** (ERROR log `AUTH FAILURE on <provider>`) and the chain moves to the next provider — a wrong key is never silently retried as keyless on the same provider. Malformed input (empty query, bad URL scheme) returns an immediate input error with no fallback.

**Example chains (not defaults).** Defaults stay minimal so existing deployments are unchanged. If you hold the keys, this is a sensible China-friendly ordering — deepseek/kimi give independent indexes and correct `published` dates, and `ddgs` stays last as the keyless floor:

```yaml
UWR_SEARCH_CHAIN: anysearch,tavily,deepseek,kimi,yuanbao,ddgs
UWR_FETCH_CHAIN: anysearch,tavily,deepseek,kimi,ddgs
```

Every one of those links is either free or priced per call (see the table below), and each one falls through on error, so a longer chain costs nothing when earlier links succeed. Two rules worth keeping:

- **Keep `ddgs` last** — keyless, no quota, the floor of the chain.
- **Never put `jina` in a default chain** — it works without a key, so a stale/keyless deployment would start sending every fetch through it. Add it explicitly when you want its Markdown quality, e.g. `UWR_FETCH_CHAIN: anysearch,tavily,jina,ddgs`.

## Provider behavior

See [`docs/providers.md`](docs/providers.md) for each provider's official endpoint, auth mechanism, keyless behavior, pricing notes and last-verified date.

| Provider | Capabilities | Auth | Key env | Price / free tier | Notes |
|---|---|---|---|---|---|
| AnySearch | search + fetch | keyed Bearer / keyless (no auth header) | `ANYSEARCH_API_KEY` | plan-based | default first link |
| Tavily | search + fetch | keyed Bearer / keyless header | `TAVILY_API_KEY` | 1,000 free credits/month | default second link |
| DDGS | search + fetch | none | — | free, no quota | default last link; JS/login pages not guaranteed |
| Yuanbao (元宝搜索) | search | keyed only | `YUANBAO_API_KEY` / `WSA_API_KEY` | 14–26 CNY / 1k calls by tier | Tencent MCP endpoint, `SearchPro`, Cnt 10–50 |
| Bocha (博查) | search | keyed only | `BOCHA_API_KEY` | 0.036 CNY/call; 1,000 free trial calls (3 months) | `POST /v1/web-search`, count 1–50, `summary: true` |
| DeepSeek | search + fetch | keyed only | `DEEPSEEK_API_KEY` | $0.75 / 1k successful calls (same for search and fetch) | `POST /v1/tools/search`, `/v1/tools/fetch`; 100 QPS search, 25 QPS fetch |
| Kimi (Moonshot) | search + fetch | keyed only | `MOONSHOT_API_KEY` / `KIMI_API_KEY` | $0.002 basic search, $0.003 pro search, $0.002 fetch; billed only on HTTP 200 with results | `text_query` + `search_results`; CN `api.moonshot.cn`, intl `api.moonshot.ai` |
| Zhipu BigModel (智谱) | search | keyed only | `ZHIPU_API_KEY` / `BIGMODEL_API_KEY` | `search_std` 0.01, `search_pro` 0.03, `search_pro_sogou` 0.05, `search_pro_quark` 0.05 CNY/call | `POST /api/paas/v4/web_search`; engine via `UWR_ZHIPU_ENGINE` |
| Brave Search | search | keyed only | `BRAVE_API_KEY` | $5.00 / 1k requests, **$5 free credits every month** (≈1k requests) | `X-Subscription-Token` header, count 1–20, 50 req/s |
| Jina Reader | fetch | keyless (20 RPM) or keyed Bearer (500 RPM free key) | `JINA_API_KEY` (optional) | free tier: 20 RPM keyless, 500 RPM with a free key | `GET https://r.jina.ai/?url=…` + `Accept: application/json`; **never in a default chain** |

Official sources for every row are listed in [`docs/providers.md`](docs/providers.md#定价与免费额度2026-10-20-核验). Prices are catalogue list prices as published on 2026-10-20 and may change; the provider's own pricing page is authoritative.

## Examples

### Generic MCP client (stdio)

```json
{
  "mcpServers": {
    "universal-web-retrieval": {
      "command": "python",
      "args": ["-m", "universal_web_retrieval"],
      "env": {
        "PYTHONPATH": "/path/to/universal-web-retrieval-mcp/src",
        "ANYSEARCH_API_KEY": "as_sk_...",
        "TAVILY_API_KEY": "tvly-...",
        "UWR_SEARCH_CHAIN": "anysearch,tavily,bocha,ddgs"
      }
    }
  }
}
```

### Hermes Agent

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  universal-web-retrieval:
    command: /root/.local/bin/uvx
    args: ["uwr"]
    transport: stdio
    enabled: true
    env:
      ANYSEARCH_API_KEY: ${ANYSEARCH_API_KEY}
      TAVILY_API_KEY: ${TAVILY_API_KEY}
      # optional, only if the corresponding key is provided:
      # YUANBAO_API_KEY: ${YUANBAO_API_KEY}
      # BOCHA_API_KEY: ${BOCHA_API_KEY}
      # DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY}
      # MOONSHOT_API_KEY: ${MOONSHOT_API_KEY}
      # ZHIPU_API_KEY: ${ZHIPU_API_KEY}
      # BRAVE_API_KEY: ${BRAVE_API_KEY}
      # JINA_API_KEY: ${JINA_API_KEY}
      # UWR_SEARCH_CHAIN: anysearch,tavily,deepseek,kimi,yuanbao,ddgs
      # UWR_FETCH_CHAIN: anysearch,tavily,deepseek,kimi,ddgs
```

### Claude Code

```bash
claude mcp add universal-web-retrieval \
  -- python -m universal_web_retrieval
# with PYTHONPATH set, or after `pip install -e .`
```

## Development

```bash
pip install pytest
pytest tests/unit -v            # chain/override/attempts/provider unit tests (no network)

# Live provider smoke tests (hit real APIs; opt-in):
RUN_LIVE_TESTS=1 pytest tests/integration -v
```

Unit tests clear provider keys and `UWR_*` variables per test, so the suite is hermetic — it passes with or without keys exported in your shell. New providers are covered with mocked HTTP only (no keys needed, no live calls).

## License

MIT
