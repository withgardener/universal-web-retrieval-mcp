# Universal Web Retrieval MCP

A universal MCP server providing resilient web search and content retrieval through a **configurable provider chain** — AnySearch, Tavily, DDGS, Tencent Yuanbao Search, Bocha, DeepSeek, Kimi, Zhipu BigModel, Brave Search and Jina Reader — with automatic failover.

## Quick Start

**No API key is required.** With no keys configured the server uses the keyless
mode of AnySearch/Tavily and falls back to keyless DDGS.

uvx (zero config, recommended):

```json
{"mcpServers": {"universal-web-retrieval": {"command": "uvx", "args": ["uwr"]}}}
```

npx (same server, no Python toolchain needed — the launcher fetches a runner):

```json
{"mcpServers": {"universal-web-retrieval": {"command": "npx", "args": ["-y", "@withgardener/uwr"]}}}
```

pip:

```bash
pip install uwr
uwr        # stdio MCP server
```

API keys are **optional**: add `TAVILY_API_KEY`, `ANYSEARCH_API_KEY` (or any of
the others) to the `env` block when you have them. See [Configuration](#configuration).

## Overview

Give any MCP-capable agent reliable web access through a single server. Universal Web Retrieval routes each request through an ordered provider chain, picks keyed or keyless authentication automatically based on which API keys are present, and moves to the next provider on rate limits, outages, or upstream errors.

The active chains are selected in this order:

1. `providers` argument passed for the call.
2. Environment configured chain (`UWR_SEARCH_CHAIN` or `UWR_FETCH_CHAIN`).
3. Built-in default (`anysearch,tavily,ddgs`).

`providers=None` selects the configured chain. An explicitly empty list, `providers=[]`, is invalid input; it never means "use defaults". Jina Reader works keyless and is deliberately absent from both defaults. New providers are opt-in, preserving the default behavior from 0.1.x.

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
- **Opt-in fan-out** — `mode="fanout"` queries up to N providers concurrently and rank-fuses the answers (RRF) instead of taking the first one. See [Search modes](#search-modes).
- **Auto keyed/keyless** — key configured → official keyed API; key absent → official keyless access (where the provider offers one). Keyed-only providers (Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave) without a key are *skipped*, never attempted, so a missing key cannot 401 the chain.
- **Strict official APIs** — every provider is called through its official documented endpoint and auth mechanism. No scraping hacks, no undocumented endpoints.
- **Error classification** — rate limits / outages fall over; invalid credentials fail loudly (ERROR log) and move to the next provider; malformed input never triggers fallback.
- **Bounded latency** — per-provider timeouts are capped to the remaining chain budget; the router does not start a provider call once less than 100 ms remain.
- **Observable** — every response carries `provider`, `mode`, `latency_ms` and an `attempts` trail.
- **Zero agent coupling** — standard MCP stdio; works with Hermes, Claude Code, OpenCode, or any MCP client.

## Architecture

```
src/universal_web_retrieval/
├── server.py            # MCP tool surface (web_search / web_fetch / list_providers)
├── config.py            # env-driven settings, call-time key + chain reads
├── errors.py            # ProviderError classification, result models, Provider ABC
├── fanout.py            # URL dedupe + Reciprocal Rank Fusion merge (fan-out mode)
└── providers/
    ├── registry.py      # name -> provider instance; capability lookup
    ├── router.py        # chain resolution (env / override / default) + failover + fan-out
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
pip install uwr                    # from PyPI
uvx uwr                            # or run without installing
npx -y @withgardener/uwr           # or through the npm launcher

uwr --version                      # uwr 0.3.1
uwr                                # run the stdio MCP server
```

`ddgs` is a **runtime dependency** (the fixed final fallback of the default chains), not an optional extra. Dependencies are pinned to compatible ranges (`mcp>=2,<3`, `httpx>=0.27,<1`, `ddgs>=9.16,<10`) so a major-version breaking change never ships to a running server via a routine `pip update`.

### From source (development)

```bash
git clone https://github.com/withgardener/universal-web-retrieval-mcp.git
cd universal-web-retrieval-mcp
pip install -e .

uwr                                # or: PYTHONPATH=src python -m universal_web_retrieval
```

## Configuration

### Chains

| Env var | Default | Effect |
|---|---|---|
| `UWR_SEARCH_CHAIN` | `anysearch,tavily,ddgs` | Ordered search chain, e.g. `anysearch,tavily,yuanbao,bocha,ddgs` |
| `UWR_FETCH_CHAIN` | `anysearch,tavily,ddgs` | Ordered fetch chain (search-only providers are dropped) |
| `UWR_DISABLED_PROVIDERS` | *(empty)* | Names switched off everywhere, e.g. `bocha,deepseek` |
| `UWR_SEARCH_MODE` | `failover` | `failover` (first success wins) or `fanout` (query N providers, merge). Unknown values warn once and use `failover` |
| `UWR_SEARCH_FANOUT` | `3` | Max providers queried concurrently in fan-out mode (≥ 1, clamped to the chain length) |
| `UWR_FANOUT_GRACE` | `5` | Seconds to wait for stragglers after the first fan-out answer; `0` waits for all (still deadline-bounded) |

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
| `WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT` | no | Max fetched-content chars (default 15000). Results report `truncated`/`original_length` |
| `WEB_RETRIEVAL_BLOCK_PRIVATE` | no | `1` makes `web_fetch` refuse private/loopback/link-local hosts (default: off — they are fetched). A pre-flight heuristic, not SSRF protection |
| `WEB_RETRIEVAL_NETGUARD_DNS_TIMEOUT` | no | Seconds the opt-in pre-flight DNS lookup may take before the target is refused (default 2). Only used with `WEB_RETRIEVAL_BLOCK_PRIVATE=1` |
| `UWR_BLOCK_DETECTION` | no | `off` disables CDN/CAPTCHA interstitial detection in `web_fetch` (default: on) |
| `UWR_BLOCK_DETECTION_MAX_CHARS` | no | Bodies at/above this many chars are never judged "blocked" (default 4000) |
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

### `web_search(query, limit=5, providers=None, mode=None)`

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

`mode` selects the search mode for this call (`"failover"` or `"fanout"`, see [Search modes](#search-modes)); omit it to use `UWR_SEARCH_MODE`. In fan-out mode `provider` is `"fanout"`, `mode` is the shared auth mode of the answering providers (or `"mixed"`), `attempts` entries for successful providers also carry `results` and `latency_ms`, and every result row carries `sources` (provider names in chain order):

```json
{
  "success": true, "provider": "fanout", "mode": "keyed", "latency_ms": 812,
  "attempts": [
    {"provider": "bocha", "outcome": "ok", "results": 5, "latency_ms": 640, "mode": "keyed"},
    {"provider": "tavily", "outcome": "ok", "results": 5, "latency_ms": 790, "mode": "keyed"},
    {"provider": "ddgs", "outcome": "skipped:grace"}
  ],
  "results": [{"title": "...", "url": "...", "snippet": "...", "sources": ["bocha", "tavily"]}]
}
```

`attempts` is the per-provider outcome trail in chain order:

| Outcome | Meaning |
|---|---|
| `ok` | This provider produced the answer (fan-out entries also carry `results`, `latency_ms`, `mode`) |
| `empty` | *(failover)* This provider answered OK with zero hits — an answer, not a failure; the walk continued |
| `error:<msg>` | Attempted and failed; the chain moved on |
| `skipped:not_configured` | Keyed-only provider without a key — never called |
| `skipped:deadline` | The chain deadline passed before this provider could start (failover) or answer (fan-out) |
| `skipped:grace` | *(fan-out only)* Still running when the grace window after the first answer closed; its result is discarded |
| `skipped:fanout` | *(fan-out only)* Eligible chain member beyond the configured N-provider fanout cutoff |

**An empty answer is not an error (0.3.1).** A provider that replies OK with zero hits is recorded as `empty` (failover keeps walking, because a later link may have hits) and, if no provider produces hits, the call **succeeds** with `results: []`. "Nothing matched" is no longer reported as `all providers failed`.

**A `null` container is malformed, not empty.** `{"results": null}` — the shape a gateway error page, a truncated body or a schema change serialises to — is reported as `malformed response (results is null, not a list)` and falls over to the next provider, rather than being turned into a successful empty result. Only a present, genuinely empty list is an empty answer.

**Success schema (search):** `{"success": true, "provider": ..., "mode": ..., "latency_ms": ..., "attempts": [...], "results": [...]}`. **Provider error schema:** `{"success": false, "error": "...", "attempts": [...]}`. **Invalid-input schema:** `{"success": false, "error": "invalid input: ..."}` (no `attempts`). Fetch returns one object per URL with `url`, `content`, `content_type`, `title`, `truncated`, `original_length`, `provider`, `mode`, `latency_ms`, and `attempts`; failed URLs carry `error` and `attempts` instead of success metadata.

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

An override that contains no usable provider (including `providers=[]`) is an input error. `providers=None` means use the configured chain; an empty explicit list never falls back to the defaults.

### `web_fetch(url | urls, providers=None)`

Accepts a single `url` string or a `urls` array (max 5) and an optional `providers` override. Returns a JSON array:

```json
[{
  "url": "https://example.com",
  "content": "# Example Domain\n\n...",
  "content_type": "text_markdown",
  "title": "Example Domain",
  "truncated": false,
  "original_length": 1256,
  "provider": "anysearch",
  "mode": "keyless",
  "latency_ms": 191,
  "attempts": [{"provider": "anysearch", "outcome": "ok"}]
}]
```

Failed URLs carry an `error` field instead of `content`. Content is Markdown/clean text — never raw HTML. JS-rendered, login-walled, or CAPTCHA-protected pages are not guaranteed (plain HTTP retrieval only; no browser automation).

**Truncation is explicit (0.3.1).** `content` is cut at `WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT` characters; `truncated: true` says it happened and `original_length` is the pre-cut character count (characters, not bytes). No marker text is appended — the fields are the marker, so `content` stays a clean prefix.

**Blocked pages are errors, not content (0.3.1).** A CDN/CAPTCHA interstitial (Cloudflare "Just a moment…", "Checking your browser", Akamai "Access Denied" with a `Reference #`) raises a fallback-eligible error so the chain tries the next provider. Detection needs evidence, never a single word: inside a body shorter than `UWR_BLOCK_DETECTION_MAX_CHARS` it blocks on **two distinct technical fingerprints** (`cf_chl_opt`, "checking your browser", "enable javascript and cookies to continue", `_incapsula_resource`, …) or on a **challenge title** ("Just a moment…", "Access Denied", "Attention Required! | Cloudflare") plus one marker. A short help page titled "CAPTCHA help", a glossary listing reCAPTCHA/hCaptcha/Turnstile, an FAQ quoting "Just a moment…" or a doc showing one `cf_chl_opt` sample all pass through — they were false positives of the earlier title-word rule. Length alone is never a signal either. `UWR_BLOCK_DETECTION=off` disables it.

### `list_providers()`

Returns every provider this build knows, with capability, configuration status, enabled flag and its 1-based position in each active chain, plus the active `search_mode`, the fan-out settings (`fanout.n`, `fanout.grace_s`) and the fetch policy (`fetch_policy.block_private_network`, `.block_detection`, `.extract_char_limit`). Key values are never returned.

```json
{
  "chains": {"search": ["anysearch", "tavily", "ddgs"], "fetch": ["anysearch", "tavily", "ddgs"]},
  "defaults": {"search": ["anysearch", "tavily", "ddgs"], "fetch": ["anysearch", "tavily", "ddgs"]},
  "search_mode": "failover",
  "fanout": {"n": 3, "grace_s": 5.0},
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

### Network scope & private targets

**Private-network fetches are allowed, as in 0.3.0. The block is opt-in.** `web_fetch` retrieves any URL the host can reach, including intranet pages, `localhost` dev servers and cloud metadata endpoints — a deliberate capability for a local agent tool. Set `WEB_RETRIEVAL_BLOCK_PRIVATE=1` (the name reserved here since 0.1.0) to refuse private, loopback, link-local, CGNAT and otherwise non-public targets before any provider is called; the refusal is per URL (`error: invalid input: refusing to fetch …`), so a batch keeps working. **Off by default** — the check is off unless that variable is set, so upgrading changes nothing.

How far the check reaches when it is on, and where it stops:

- **IP literals** are decided exactly, with no DNS: bracketed IPv6, IPv4-mapped IPv6, an IPv6 zone id (`[fe80::1%25eth0]`), and every **legacy numeric IPv4 spelling** the C library also accepts — `127.1`, `0177.0.0.1`, `0x7f.0.0.1`, `0x7f000001` and the integer `2130706433`. Those forms are decoded here rather than handed to the resolver, because `ipaddress.ip_address` rejects them and anything that reaches the resolver can dodge the check. A numeric-only host that cannot be decoded (`1.2.3.4.5`, `09.1.1.1`) is refused: it cannot be a domain name, and guessing is the wrong direction for a deny check.
- **Hostnames** are resolved with `socket.getaddrinfo` — the platform resolver — so `/etc/hosts`, systemd-resolved and host-level controls still apply. No private DNS client is added: bypassing the dependent host's resolution policy would defeat its existing security constraints. **Every** returned address must be globally routable, so a public+private answer (what a rebinding attempt looks like from here) is rejected.
- **The lookup is bounded** (`WEB_RETRIEVAL_NETGUARD_DNS_TIMEOUT`, default 2 s) and a lookup that does not answer in time is **refused** rather than treated as allowed — a stalled resolver would otherwise be a way to skip the check. That is a conservative choice: a genuinely slow resolver will now refuse targets until you raise the timeout.
- **Redirects** are followed by the provider's own network, not by this process. A final URL a provider reports is re-checked and fails the call closed when it is non-public, but the hop has already happened — there is no connection binding to inspect, so this cannot *prevent* that hop. Preventing it outright needs a local fetching proxy, which this server does not have.
- **DNS rebinding** between our check and the provider's connect cannot be excluded for the same reason. Only a design that resolves, connects and then verifies the peer address can claim that; a third-party extraction API does not expose it.
- **A hostname that does not resolve here is still allowed** (logged at DEBUG). The provider resolves it on its own network, so a definitive `NXDOMAIN` proves nothing about the provider's view — residual gap: an internal-only name that only resolves inside your network is not detectable from this host. A timeout is treated differently, as above.
- **The answer is only as good as this host's resolver.** A host with a poisoned, split-horizon or overridden resolver gets the answer that resolver gives.

**This is not SSRF protection, and it is not a guarantee.** It is a cheap pre-flight deny of obvious targets that only runs when you ask for it; it cannot stop DNS rebinding or a provider-side redirect hop, and it does not sandbox the extraction service. For untrusted agent input in a hardened deployment, keep **network isolation** (container or netns without a route to the intranet, egress firewall rules, and a deny-list at the provider) as the outer boundary. Treat the check as extra friction on top of that, never as a replacement for it.

Batch behavior: `urls` is capped at **5 per call** (larger batches are rejected with an explicit error, not silently truncated), duplicates are de-duplicated preserving first-seen order, and the **whole batch shares one deadline** — URLs whose turn arrives after the deadline return a timeout error without any network attempt.

**Credential handling (0.3.1, changed).** Diagnostic text — `error` strings, `attempts` outcomes, the `AUTH FAILURE` log lines — is scrubbed of configured key values, `NAME=value` key assignments, bearer tokens and credential-bearing query parameters (`?token=`, `?sig=`, `?api_key=`, …). A structured diagnostic field is scrubbed **recursively**: an `error` or `errors` value that is a mapping or a list has every string inside it treated the same way, at any depth, while codes and counts keep their types. **Successful payloads are no longer pattern-rewritten**: a fetched page may legitimately contain signed URLs (`?sig=…`) and rewriting them produced links that no longer work. The only pass over a success payload removes the exact values of credentials configured in this process (a page echoing your key is a leak by definition), and that pass also covers anything nested. Trust boundary: the payload goes to the calling agent only and is never written to a log; third-party signed URLs inside page content belong to that page's publisher and are returned as-is.

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

## Search modes

`web_search` has two modes. The default is unchanged from every earlier release.

| Mode | Behaviour | Cost per search |
|---|---|---|
| `failover` (default) | Walks the chain in order; the first provider that returns OK wins, even with no hits | one provider call |
| `fanout` | Queries the first N eligible providers **concurrently**, waits `UWR_FANOUT_GRACE` seconds for stragglers once the first answer (hit or empty) arrives, then merges and rank-fuses everything that arrived | N provider calls |

The trade-off: fan-out spends **N× provider quota per search**, and its latency is
bounded by the grace window (at most `UWR_FANOUT_GRACE` seconds more than the
fastest provider) and by the shared chain deadline (`WEB_RETRIEVAL_TIMEOUT`) —
never by the slowest provider. In exchange it buys agreement: a page several
providers return outranks a page only one of them found, which is what you want
when a wrong first hit is expensive.

### Latency and quota

Measured on the default chain (`anysearch,tavily,ddgs`) with N=3 and the default
5 s grace:

| Scenario | Wall time |
|---|---|
| `failover`, first provider healthy | ~2 s |
| `fanout`, third provider slow | ~7 s |
| `fanout`, all three providers fast | ~2.5 s |

In the slow case the first two providers answered at about 2.1 s and `ddgs` was
still running when the grace window closed: the response came back at about 7 s
with `ddgs` recorded as `skipped:grace`. All three calls were made either way, so
every fan-out spends N× provider quota — including the ones whose answers are
thrown away. An abandoned provider keeps running in its daemon thread and may
still complete (and be billed) in the background; the grace window decides what
you wait for, not what you pay for.

Selecting a mode:

- `UWR_SEARCH_MODE=fanout` (or `failover`) sets the default for every call.
  An unknown value logs one warning and falls back to `failover`.
- `web_search(..., mode="fanout")` overrides the default for one call. An unknown
  per-call value is an input error — it never silently switches modes.
- In fan-out mode `providers` still defines the candidate order; its first N
  **eligible** entries are queried. Keyed-only providers without a key are
  recorded as `skipped:not_configured` and do **not** consume a slot.
- `UWR_SEARCH_FANOUT` (default `3`, minimum `1`, clamped to the chain length) sets N.

Merging (`fanout.py`):

1. URLs are normalised for dedupe into a scheme-less key,
   `//host[:port]/path?query`, so `http://` and `https://` for one page collapse
   — as does a default port (`:80` for http, `:443` for https). The host is
   IDNA-normalised with any leading `www.` stripped (non-default ports and IPv6
   literals preserved), the fragment and tracking parameters (`utm_*`, `gclid`,
   `fbclid`, `mc_cid`, `mc_eid`, `ref`, `ref_src`, `spm`) dropped, remaining
   parameters sorted, and a trailing `/` removed (except for the root path). The
   output keeps the best-ranked occurrence's original URL, not the key.
2. Reciprocal Rank Fusion: `score = Σ 1/(60 + rank)` over every provider that
   returned the URL, with a 1-based rank inside that provider's list. Ties break
   on number of sources, then best single rank, then chain order.
3. Per row: title and URL from the best-ranked occurrence, the longest non-empty
   snippet, and `published` / `site` from the first non-empty value in chain
   order. Every row gains `sources` — the provider names, in chain order.

Fan-out responses set `provider: "fanout"` and `mode` to the shared auth mode of
the answering providers, or `"mixed"` when they differ. `attempts` carries one
entry per queried provider: `ok` (plus `results`, `latency_ms` and the `mode` that
answered), `error:<msg>`, `skipped:not_configured`, `skipped:fanout` (beyond the
N-provider cutoff), `skipped:grace` (a late successful answer is discarded; a late
error stays visible) or `skipped:deadline`. Success
requires at least one provider to answer OK, not at least one hit: if every
answering provider returns an empty list, the response is a success with
`results: []` and `ok` / `results: 0` entries in `attempts` — the same thing
failover returns for that query. Only when no provider answers OK at all is it
the same `all providers failed: ...` error with the trail attached, and one
provider's `fallback_eligible=False` error does not abort the others.

```json
{"query": "rust async runtime", "mode": "fanout",
 "providers": ["bocha", "tavily", "ddgs"]}
```

`web_fetch` is always failover: there is nothing to merge for a single URL.

Concurrency: providers are synchronous HTTP clients, so fan-out starts one daemon
thread per selected provider — plain `threading.Thread` plus a `queue.Queue` of
`(provider, outcome)` records, no new dependency and no private interpreter
internals. Each worker gets its own timeout budget and shares no mutable per-call
state. Yuanbao is the only provider holding per-instance state (MCP session id and
JSON-RPC counter); both are guarded by an `RLock`, and a concurrent handshake race
costs one extra session, not correctness. A straggler that outlives the grace
window keeps running in its daemon thread; the collector never joins it, it cannot
delay interpreter exit, and the result it eventually posts is dropped.

## Provider behavior

The built-in chain remains `anysearch,tavily,ddgs` for search and fetch; all newly added providers are opt-in unless explicitly listed in `UWR_SEARCH_CHAIN`, `UWR_FETCH_CHAIN`, or a per-call `providers` override. Jina is keyless but deliberately absent from defaults.

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

All provider keys below are placeholders or environment-variable references; never put live credentials in client configuration files.

### Generic MCP client (stdio)

```json
{
  "mcpServers": {
    "universal-web-retrieval": {
      "command": "uvx",
      "args": ["uwr"],
      "env": {
        "ANYSEARCH_API_KEY": "${ANYSEARCH_API_KEY}",
        "TAVILY_API_KEY": "${TAVILY_API_KEY}",
        "UWR_SEARCH_CHAIN": "anysearch,tavily,bocha,ddgs"
      }
    }
  }
}
```

Swap `"command": "uvx", "args": ["uwr"]` for `"command": "npx", "args": ["-y", "@withgardener/uwr"]`
where npm is the only package manager available, or for
`"command": "python", "args": ["-m", "universal_web_retrieval"]` when running the
[from-source](#from-source-development) checkout with `PYTHONPATH` set.

### Hermes Agent

In `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  universal-web-retrieval:
    command: /home/hermes/.local/bin/uvx
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
  --env TAVILY_API_KEY="${TAVILY_API_KEY}" \
  -- uvx uwr
```

### Cursor

Add a project `.cursor/mcp.json` or user MCP configuration with environment variables supplied by the launching process:

```json
{
  "mcpServers": {
    "universal-web-retrieval": {
      "command": "uvx",
      "args": ["uwr"],
      "env": {
        "ANYSEARCH_API_KEY": "${ANYSEARCH_API_KEY}",
        "TAVILY_API_KEY": "${TAVILY_API_KEY}"
      }
    }
  }
}
```

For Cursor releases using its `${env:NAME}` interpolation syntax, use `"${env:ANYSEARCH_API_KEY}"` and `"${env:TAVILY_API_KEY}"`. Verify environment substitution on your Cursor version; do not replace these references with literal credentials.

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
