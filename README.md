# Universal Web Retrieval MCP

A universal MCP server providing resilient web search and content retrieval through a **configurable provider chain** — AnySearch, Tavily, DDGS, Tencent Yuanbao Search, Bocha and DeepSeek — with automatic failover.

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
- **Auto keyed/keyless** — key configured → official keyed API; key absent → official keyless access (where the provider offers one). Keyed-only providers (Yuanbao, Bocha, DeepSeek) without a key are *skipped*, never attempted, so a missing key cannot 401 the chain.
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
    └── deepseek.py      # DeepSeek Search API (REST, Bearer)
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
| `DEEPSEEK_API_KEY` | for `deepseek` | DeepSeek API key (Search API) |
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
| `UWR_YUANBAO_MCP_URL` | no | Override the Yuanbao MCP endpoint |
| `UWR_BOCHA_BASE_URL` | no | `https://api.bochaai.com` (or `https://api.bocha.cn`) |
| `UWR_DEEPSEEK_BASE_URL` | no | Default `https://api.deepseek.com` |

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

When a provider exposes publication date / site (Yuanbao, Bocha, DeepSeek), the result rows also carry `published`, `site` and (where scored) `score`. `provider` / `mode` / `latency_ms` / `attempts` are diagnostic metadata — consumers only need `results`.

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

| Capability | Default chain | Search-only members available on request |
|---|---|---|
| `web_search` | AnySearch → Tavily → DDGS | Yuanbao → Bocha → DeepSeek |
| `web_fetch` | AnySearch → Tavily → DDGS.extract | — (the three new providers are search-only) |

A link is skipped when: rate-limited (429), timed out, upstream 5xx, or any provider error. **Invalid credentials fail loudly** (ERROR log `AUTH FAILURE on <provider>`) and the chain moves to the next provider — a wrong key is never silently retried as keyless on the same provider. Malformed input (empty query, bad URL scheme) returns an immediate input error with no fallback.

**Recommended chains.** Defaults stay minimal so existing deployments are unchanged. If you have the keys, a China-friendly ordering is:

```yaml
UWR_SEARCH_CHAIN: anysearch,tavily,yuanbao,bocha,ddgs
UWR_FETCH_CHAIN: anysearch,tavily,ddgs
```

Keep `ddgs` last (keyless, no quota) and leave DeepSeek out of the default chain unless you want its results in the mix — it is billed per call and its index differs from the others.

## Provider behavior

See [`docs/providers.md`](docs/providers.md) for each provider's official endpoint, auth mechanism, keyless behavior, pricing notes and last-verified date.

| Provider | Capabilities | Auth | Notes |
|---|---|---|---|
| AnySearch | search + fetch | keyed Bearer / keyless (no auth header) | default first link |
| Tavily | search + fetch | keyed Bearer / keyless header | 1,000 free credits/month |
| DDGS | search + fetch | none | keyless package; JS/login pages not guaranteed |
| Yuanbao (元宝搜索) | search | keyed only (`Authorization: Bearer`) | Tencent MCP endpoint, `SearchPro`, Cnt 10–50 |
| Bocha (博查) | search | keyed only | `POST /v1/web-search`, count 1–50, `summary: true` |
| DeepSeek | search | keyed only | `POST /v1/tools/search`, count 1–50, $0.75/1k calls |

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
      # UWR_SEARCH_CHAIN: anysearch,tavily,yuanbao,bocha,ddgs
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
