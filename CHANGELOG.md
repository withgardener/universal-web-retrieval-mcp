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
- **DeepSeek** (`deepseek`) — official DeepSeek Search API (`/v1/tools/search`,
  search-only). Implemented as a structured search provider, not an answer-style one.
- Optional per-provider timeouts and base-URL overrides for the three new providers.

### Changed

- Keyed-only providers (Yuanbao, Bocha, DeepSeek) with no key are skipped as
  `skipped:not_configured` and never reach the network, so a missing key cannot 401
  a chain. AnySearch, Tavily and DDGS keep their official keyless behaviour.
- Search rows may now carry `published`, `site` and `score` when a provider exposes
  them (additive; existing keys unchanged).
- `HTTP 402` is classified as insufficient balance/arrears (fallback-eligible).
- Chain resolution, failover, deadline and timeout-override semantics unchanged.

### Testing

- 114 unit tests (was 36), all mocked — no network, no keys. The suite clears
  provider keys and `UWR_*` variables per test so it is hermetic.

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
