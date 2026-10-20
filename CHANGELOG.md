# Changelog

All notable changes to this project. Format: Keep a Changelog; versioning: SemVer.

## 0.2.0 — 2026-10-20

### Added

- **Configurable provider chains.** `UWR_SEARCH_CHAIN` / `UWR_FETCH_CHAIN` set the
  order; unknown names, providers without the capability, and duplicate names are
  dropped with a single warning per chain, and an empty/invalid chain falls back to
  the built-in default. Defaults are unchanged from 0.1.x (`anysearch,tavily,ddgs`).
- **`UWR_DISABLED_PROVIDERS`** — switch providers off without editing the chains.
- **Per-call `providers` argument** on `web_search` / `web_fetch` — overrides the
  order for one call (`["bocha"]`, `["yuanbao","tavily"]`). An override with no
  usable provider is rejected as invalid input instead of silently using defaults.
- **`attempts`** in every response: per-provider outcome trail
  (`ok`, `error:<message>`, `skipped:not_configured`, `skipped:deadline`).
- **`list_providers` tool** — capabilities, configured/auth status, enabled flag and
  1-based position per active chain; never exposes key values.
- **Yuanbao** (`yuanbao`) — Tencent Cloud Yuanbao Search via the official MCP
  endpoint (`SearchPro`, Bearer key, search-only).
- **Bocha** (`bocha`) — Bocha Web Search API (`/v1/web-search`, search-only).
- **DeepSeek** (`deepseek`) — official DeepSeek Search + Fetch API (`/v1/tools/search`,
  `/v1/tools/fetch`). Implemented as a structured search provider, not an
  answer-style one. Fetch 403 (security policy) and 404 (no usable content) are
  fallback-eligible and are deliberately *not* reported as credential problems.
- **Kimi** (`kimi`) — Moonshot's standalone REST tools: `/v1/tools/search`
  (`UWR_KIMI_SEARCH_TIER=pro` switches to `/v1/tools/search_pro`) and
  `/v1/tools/fetch`. Search **and** fetch. Key: `MOONSHOT_API_KEY` (or
  `KIMI_API_KEY`); host: `api.moonshot.cn` by default, `UWR_KIMI_BASE_URL` for
  the international `api.moonshot.ai`. The deprecated `$web_search` chat tool is
  not used.
- **Zhipu BigModel** (`zhipu`) — `POST /api/paas/v4/web_search`, search only,
  engines `search_std` (default) / `search_pro` / `search_pro_sogou` /
  `search_pro_quark` via `UWR_ZHIPU_ENGINE`. Key: `ZHIPU_API_KEY` (or
  `BIGMODEL_API_KEY`).
- **Brave Search** (`brave`) — `GET /res/v1/web/search` with the
  `X-Subscription-Token` header, search only. Key: `BRAVE_API_KEY`.
- **Jina Reader** (`jina`) — fetch only, `GET https://r.jina.ai/?url=…` with
  `Accept: application/json`. Keyless by default (official 20 RPM), optional
  `JINA_API_KEY` for the higher keyed limit. Because it works without a key it is
  **not** in either default chain and only runs when named.
- Optional per-provider timeouts (plus `UWR_JINA_TIMEOUT`, default 30s because
  Reader drives a browser) and base-URL overrides for all new providers.

### Changed

- `deepseek` now advertises **fetch** in addition to search; the fetch chain
  therefore accepts `deepseek` and `kimi` when an operator names them.
- Keyed-only providers (Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave) with no key
  are skipped as `skipped:not_configured` and never reach the network, so a
  missing key cannot 401 a chain. AnySearch, Tavily, Jina and DDGS keep their
  official keyless behaviour.
- Search rows may now carry `published`, `site` and `score` when a provider exposes
  them (additive; existing keys unchanged).
- `HTTP 402` is classified as insufficient balance/arrears (fallback-eligible).
- DeepSeek's documented rate limits are recorded as Search 100 QPS / Fetch 25 QPS
  (independent, account level, Fetch also capped per target domain). No 5 QPS cap
  is documented for either endpoint; every QPS figure in the repo now traces to
  the official table.
- Chain resolution, failover, deadline and timeout-override semantics unchanged.

### Testing

- 207 unit tests (was 114), all mocked — no network, no keys. The suite clears
  provider keys and `UWR_*` variables per test so it is hermetic. The additions
  cover, per new provider/capability: request shape, response mapping, error
  classification (including the 403/404 fetch cases that must not look like auth
  failures), truncation/clamping, and `skipped:not_configured` with zero HTTP
  calls. Default chains are asserted unchanged.

## 0.1.1 — 2026-09-14

### Changed

- Removed duplicate `_timeout` definitions that silently shadowed one another.
- Release workflow reads the version from `pyproject.toml` instead of hard-coding it.
- Tests install the project with `pip install -e .` instead of a hand-copied
  dependency list.

## 0.1.0 — 2026-09-13

### Added

- Initial release: `web_search` / `web_fetch` over AnySearch → Tavily → DDGS with
  automatic keyed/keyless auth, error classification, per-provider timeouts and an
  overall chain deadline.
