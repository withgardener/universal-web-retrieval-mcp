# Changelog

All notable changes to this project. Format: Keep a Changelog; versioning: SemVer.

## 0.3.0 — 2026-10-23

### Added

- **Fan-out search mode (opt-in).** `web_search` can now query up to N providers
  concurrently and merge what came back, instead of stopping at the first
  success. Select it per call with `mode="fanout"` or for the whole process with
  `UWR_SEARCH_MODE=fanout`; `UWR_SEARCH_FANOUT` (default `3`, clamped to the
  chain length) sets how many eligible providers run at once, and
  `UWR_FANOUT_GRACE` (default `5` s, `0` = wait for all) bounds how long the
  collector waits for stragglers after the first non-empty answer. The overall
  bound is still the shared chain deadline, and each provider gets the remaining
  budget as its timeout. Failover remains the default and its behaviour is
  unchanged.
- **Merge layer** (`fanout.py`): URL normalisation for dedupe (lower-cased
  scheme/host, leading `www.` stripped, fragment and tracking parameters
  dropped, remaining parameters sorted, trailing slash removed), Reciprocal Rank
  Fusion scoring (`k = 60`) with source-count / best-rank / chain-order
  tie-breaks, per-row title and URL from the best-ranked occurrence, longest
  non-empty snippet, first non-empty `published`/`site` in chain order, and a
  `sources` list in chain order.
- **Fan-out observability**: results report `provider: "fanout"` and a `mode` of
  `keyed`/`keyless`/`mixed` (new `AuthMode.MIXED`), `attempts` carries one entry
  per queried provider with `ok` + `results`/`latency_ms`, `error:<msg>`,
  `skipped:not_configured`, `skipped:fanout` for eligible chain members beyond N,
  `skipped:grace` or `skipped:deadline`, and
  `list_providers` reports `search_mode` plus `fanout: {n, grace_s}`.
- **Tests** (`tests/unit/test_fanout.py`, 49 cases, no network): mode selection
  from env and per call, invalid values, N clamping, `not_configured` skipping
  that does not consume a slot, real concurrency with sleeping stubs, grace
  cut-off, deadline enforcement, one provider failing while others succeed, all
  failing with the attempt trail attached, `fallback_eligible=False` isolation,
  URL-variant dedupe, RRF ordering and every tie-break, snippet/title/extra-field
  merging, `sources`, mixed mode, `list_providers`, and `web_fetch` staying
  failover.

### Changed

- `AuthMode` gains `MIXED`. `SearchResult.to_dict()` emits `sources` only for rows
  that carry it, so failover payloads are byte-for-byte identical to 0.2.3.
- `web_search` takes an optional `mode` argument; `web_fetch` does not and is
  always failover. README documents both modes with their quota/latency
  trade-off.
- Thread safety reviewed for fan-out: providers are sync HTTP clients run one per
  thread; no provider keeps unsynchronised per-call state. Yuanbao's per-instance
  MCP session id and JSON-RPC counter are guarded by an `RLock`, and a concurrent
  handshake race costs an extra session rather than correctness. A straggler is
  never joined (`shutdown(wait=False, cancel_futures=True)`); its daemon worker
  cannot delay interpreter exit, and its result is discarded.

## 0.2.3 — 2026-10-21

### Changed

- **npm releases use Trusted Publishing (OIDC).** `release-npm.yml` no longer reads
  any npm token; npmjs.com trusts this repository's `release-npm.yml` in the `npm`
  environment, and the job ensures npm >= 11.5.1. Provenance is unchanged. No code
  changes in the Python package or the launcher.

## 0.2.2 — 2026-10-21

### Changed

- **The npm launcher is published as `@withgardener/uwr` only.** The npm registry
  rejected the bare name `uwr` ("too similar to existing packages", E403), so
  0.2.1 never reached npm. Use `npx -y @withgardener/uwr`; the PyPI package is
  still `uwr` (`uvx uwr`). No Python code changes.

## 0.2.1 — 2026-10-21

### Added

- **npx launcher package `uwr`** (`npm/`): a zero-dependency package that starts
  the published PyPI server, so npm-only MCP marketplaces can validate it.
  It resolves a runner in order — `UWR_NPM_RUNNER` override, `uvx`, `uv`,
  `pipx`, then an auto-downloaded Astral uv whose sha256 is pinned in
  `npm/lib/uv-pin.json` and verified before unpacking — and caches that uv under
  `$XDG_CACHE_HOME/uwr-npm/`. `uwr --launcher-info` reports the launcher version,
  the pinned PyPI version and the resolved runner. Published as
  `@withgardener/uwr`.
- **npm release workflow** (`.github/workflows/release-npm.yml`): on `release:
  published`, runs the launcher tests, requires tag == `npm/package.json` ==
  `pyproject` version, waits for that version on PyPI, then publishes
  `@withgardener/uwr` with provenance, skipping a version already on the registry.
- **Version guard** `tests/unit/test_npm_version_sync.py`: the launcher version
  must equal the project version, and every pinned uv asset needs a sha256.
- **README Quick Start**: uvx / npx / pip configurations, keyless by default.

### Changed

- README installation routes: `uvx`, `npx` and `pip` are the documented ways to
  run the server; `PYTHONPATH=src` is now only the from-source development path.

### Fixed

- **`python -m universal_web_retrieval` ignored its arguments.** `__main__.py`
  started the server directly, so `--version` printed nothing; it now delegates
  to the same CLI as the `uwr` console script (no flags still starts the server).
- **Launcher `--launcher-info`**: only honoured before `--` (so
  `uwr -- --launcher-info` reaches the server), and when nothing resolves the
  reason (for example an unsupported `UWR_NPM_RUNNER`) is printed on stderr.
- **Launcher accepts `UWR_NPM_RUNNER=uv`**, matching the automatic order.
- **npm release waits for PyPI**: the launcher is published only after
  `uwr==<version>` is visible on PyPI, so `npx uwr` never points at a missing
  release.
- **Launcher `python` runner silently dropped every argument.**
  `UWR_NPM_RUNNER=python` ran `python -m universal_web_retrieval`, which starts
  `__main__.py` and ignores `sys.argv`, so `npx -y uwr --version` printed nothing
  and exited 0. It now runs `python -m universal_web_retrieval.cli` — the same
  entry point as the `uwr` console script — so forwarded flags behave as
  documented.
- **README claimed a proxy switch the downloader never honoured.** The npm README
  table listed `NODE_USE_ENV_PROXY=1` as making the uv download honour
  `HTTPS_PROXY` / `NO_PROXY`; the download uses Node's built-in `https` client,
  which ignores those variables (measured on Node 24.11 with a logging proxy: zero
  proxied requests). The row is replaced by a note saying the download connects
  directly and pointing proxy users at `uv`/`pipx`/`UWR_NPM_RUNNER=python`. A new
  `npm/test/docs.test.js` fails when the README table documents a switch the
  launcher never reads.
- **Launcher trusted a truncated `uv` in its cache forever.** The cache check only
  tested the executable bit, so a 0-byte or half-written `uv` — an interrupted
  download, a disk-full kill, or damage left by an older launcher that unpacked
  straight into the cache dir — was reused on every run. Because libuv falls back
  to `/bin/sh` for a file without ELF/Mach-O magic, `npx uwr` then printed nothing
  and exited 0 forever. A cached binary smaller than 1 MiB is now treated as stale:
  it is deleted and re-downloaded (verifying the pin again), and `ensureUv` repairs
  the cache on the same path.

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
- **Empty per-call provider lists are invalid.** `providers=None` uses the configured
  chain; `providers=[]` returns invalid input and cannot silently invoke defaults.
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
  `/v1/tools/fetch`. Search **and** fetch. Search requests include the documented
  integer `timeout_seconds` bound to the effective chain timeout (1–60 seconds).
  Key: `MOONSHOT_API_KEY` (or `KIMI_API_KEY`); host: `api.moonshot.cn` by default,
  `UWR_KIMI_BASE_URL` for the international `api.moonshot.ai`. The deprecated
  `$web_search` chat tool is not used.
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
- Chain resolution and default routing order are unchanged; timeout handling now
  enforces the shared hard deadline and the 100 ms no-new-attempt margin.

### Fixed

- Enforced a 100 ms minimum remaining chain budget before starting a provider;
  near-deadline links are recorded as `skipped:deadline` instead of being launched
  with an unusably small timeout.
- Removed the process-wide HTTP lock from the shared Bearer JSON helper so
  independent provider calls can execute concurrently. Yuanbao now locks only its
  session state and JSON-RPC ID updates; it does not hold a lock during network I/O.
- Capped provider connect/read timeouts to remaining chain budget without rounding
  tiny budgets up to one second.
- Redacted API key values from provider-named error strings in tool responses.

### Testing

- 225 unit tests (was 114), all mocked — no network, no keys. The suite clears
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
