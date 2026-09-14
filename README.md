# Universal Web Retrieval MCP

A universal MCP server providing resilient web search and content retrieval through automatic provider failover across AnySearch, Tavily, and DDGS.

## Overview

Give any MCP-capable agent reliable web access through a single server. Universal Web Retrieval routes each request through an ordered provider chain, picks keyed or keyless authentication automatically based on which API keys are present, and falls over to the next provider on rate limits, outages, or upstream errors.

```
Agent (any MCP client)
│
├── web_search ──▶ AnySearch ──fail──▶ Tavily ──fail──▶ DDGS
└── web_fetch  ──▶ AnySearch ──fail──▶ Tavily ──fail──▶ DDGS.extract
```

## Features

- **Two standard tools** — `web_search` and `web_fetch`, clean JSON in/out.
- **Automatic provider failover** — one provider failing never fails the tool.
- **Auto keyed/keyless** — key configured → official keyed API; key absent → official keyless access. No mode switches to configure.
- **Strict official APIs** — every provider is called through its official documented endpoint and auth mechanism. No scraping hacks, no undocumented endpoints.
- **Error classification** — rate limits / outages fall over; invalid credentials fail loudly (ERROR log) and move to the next provider; malformed input never triggers fallback.
- **Bounded latency** — per-provider timeouts plus an overall chain deadline.
- **Zero agent coupling** — standard MCP stdio; works with Hermes, Claude Code, OpenCode, or any MCP client.

## Architecture

```
src/universal_web_retrieval/
├── server.py            # MCP tool surface (web_search / web_fetch)
├── config.py            # env-driven settings, call-time key reads
├── errors.py            # ProviderError classification, result models, Provider ABC
└── providers/
    ├── router.py        # ProviderRouter: ordered chains + error classification
    ├── anysearch.py     # AnySearch (keyed Bearer / keyless no-auth-header)
    ├── tavily.py        # Tavily (keyed Bearer / keyless X-Tavily-Access-Mode)
    └── ddgs.py          # DDGS (official package; text + extract)
```

## Installation

```bash
pip install mcp httpx ddgs
# run:
PYTHONPATH=src python -m universal_web_retrieval
```

`ddgs` is a **runtime dependency** (the fixed final fallback of both chains), not an optional extra. Dependencies are pinned to compatible ranges (`mcp>=2,<3`, `httpx>=0.27,<1`, `ddgs>=9.16,<10`) so a major-version breaking change never ships to a running server via a routine `pip update`.

## Configuration

| Env var | Required | Effect |
|---|---|---|
| `ANYSEARCH_API_KEY` | no | AnySearch keyed mode; absent → official keyless |
| `TAVILY_API_KEY` | no | Tavily keyed mode; absent → official keyless (`X-Tavily-Access-Mode: keyless`) |
| `WEB_RETRIEVAL_TIMEOUT` | no | Overall chain deadline in seconds (default 45) |
| `WEB_RETRIEVAL_LOG_LEVEL` | no | `WARNING` default; logs go to **stderr** (stdout is MCP JSON-RPC) |
| `WEB_RETRIEVAL_MAX_RESULTS` | no | Cap for `web_search.limit` (default 20) |

## Tools

### `web_search(query, limit=5)`

```json
{
  "success": true,
  "provider": "anysearch",
  "mode": "keyless",
  "latency_ms": 1555,
  "results": [{"title": "...", "url": "...", "snippet": "..."}]
}
```

`provider` / `mode` / `latency_ms` are informational metadata — consumers only need `results`.

### `web_fetch(url | urls)`

Accepts a single `url` string or a `urls` array (max 5). Returns a JSON array:

```json
[{
  "url": "https://example.com",
  "content": "# Example Domain\n\n...",
  "content_type": "text_markdown",
  "title": "Example Domain",
  "provider": "anysearch",
  "mode": "keyless",
  "latency_ms": 191
}]
```

Failed URLs carry an `error` field instead of `content`. Content is Markdown/clean text — never raw HTML. JS-rendered, login-walled, or CAPTCHA-protected pages are not guaranteed (plain HTTP retrieval only; no browser automation).

## Routing & fallback

| Capability | Chain |
|---|---|
| `web_search` | AnySearch → Tavily → DDGS |
| `web_fetch` | AnySearch → Tavily → DDGS.extract |

A link is skipped when: rate-limited (429), timed out, upstream 5xx, or any provider error. **Invalid credentials fail loudly** (ERROR log `AUTH FAILURE on <provider>`) and the chain moves to the next provider — a wrong key is never silently retried as keyless on the same provider. Malformed input (empty query, bad URL scheme) returns an immediate input error with no fallback.

## Provider behavior

See [`docs/providers.md`](docs/providers.md) for each provider's official endpoint, auth mechanism, keyless behavior, and last-verified date.

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
        "TAVILY_API_KEY": "tvly-..."
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
    command: /path/to/venv/bin/python
    args: ["-m", "universal_web_retrieval"]
    transport: stdio
    enabled: true
    env:
      PYTHONPATH: /path/to/universal-web-retrieval-mcp/src
      TAVILY_API_KEY: tvly-...
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
pytest tests/unit -v            # router/auth/deadline unit tests (no network)

# Live provider smoke tests (hit real APIs; opt-in):
RUN_LIVE_TESTS=1 pytest tests/integration -v
```

Live tests cover all three providers in both keyless and keyed modes; keyed tests SKIP (not fail) when the corresponding key is not configured.

## Testing

Unit tests cover the router (fallback order, auth-failure semantics, invalid input), and integration smoke tests exercise all three providers live. See `tests/`.

## License

MIT
