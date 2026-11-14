# Changelog

All notable changes to this project. Format: Keep a Changelog; versioning: SemVer.

## 0.3.1 — 2026-11-14

Hardening release: correctness and security fixes from an independent review. No
new provider, no new dependency, no default behaviour change — private-network
fetches stay allowed, the block stays opt-in, and interstitial detection stays on.

### Fixed

- **An empty provider answer is an answer, not an error.** Every provider used to
  raise `ProviderError("no results")` when a well-formed reply contained zero
  hits, so a query with no matches reported `all providers failed: …` and fan-out
  recorded `error:no results` — contradicting the documented contract that an
  all-empty answer set is a successful empty result. New rule, stated once in
  `errors.result_container()`: a **missing** results container (schema change,
  gateway error page, truncated JSON) is still a malformed reply and a
  `ProviderError`; a **present-but-empty** container is an empty answer and
  returns `SearchResult(results=[])`. JSON `null` is malformed too: it is what a
  broken envelope or an error page rendered as JSON serialises to, and it used to
  be returned as `[]`, telling the caller "the web has nothing for this query"
  about a reply that said nothing. Same rule for an explicit `"Pages": null` in
  the Yuanbao envelope and for Tavily's extract container. Failover records
  `{"outcome": "empty"}`, keeps walking (a later link may have hits) and returns
  the first empty answer as a success when nothing produced hits. Fan-out already
  counted empty OK answers as OK; it now also starts the grace clock on one, so
  "no matches" is not the slowest possible query. API-level error envelopes
  (e.g. Zhipu 1703) remain errors: the provider said it failed, we did not merely
  get nothing.
- **Fetch truncation is explicit.** Content was cut at
  `WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT` with nothing in the payload saying so.
  `FetchResult` now reports `truncated` (bool) and `original_length` (pre-cut
  character count) and all six fetch providers go through `content.truncate()`.
  Additive: `content` is still a clean prefix at the limit with no marker text.
- **CDN/CAPTCHA interstitials are no longer returned as page content, and pages
  that discuss them are no longer mistaken for one.**
  `content.enforce_not_blocked()` weighs two kinds of evidence in a short body
  (`UWR_BLOCK_DETECTION_MAX_CHARS`, default 4000): **technical fingerprints** that
  only a challenge implementation emits (`cf_chl_opt`, "checking your browser",
  "enable javascript and cookies to continue", `_incapsula_resource`, …) and
  **prose hints** that walls and documentation share (captcha, turnstile,
  "ray id", "reference #"). A page is blocked only on **two distinct technical
  fingerprints**, or on a genuine **challenge title plus one marker**. A title
  counts only as the whole title or the challenge phrase followed by the site's
  own short label ("Access Denied - example.com", "Attention Required! |
  Cloudflare"); a subtitle after a colon — "Access Denied: a practical guide to
  403 responses" — is prose and no longer matches, and a bare word such as
  "captcha" is never a title marker. Length alone is never a signal. Real
  Cloudflare/Akamai/Sucuri/DDoS-Guard walls still raise a fallback-eligible error
  so the chain tries the next provider; a single marker, a code sample or an FAQ
  quoting the wall text stays content. False negatives (a novel interstitial, a
  JS-only shell) are preferred to hiding a real page.
  `UWR_BLOCK_DETECTION=off` disables the check.
- **Successful URLs are no longer rewritten by the redaction regex.** `_json()`
  applied one global `[?&](token|key|sig|…)=` regex to the whole serialised
  payload, so every signed URL inside fetched Markdown or a search row came back
  as `<redacted>` — a broken link. Scrubbing is now structural and split by
  trust: diagnostic text (`error`, `attempts`, `AUTH FAILURE` log lines) gets the
  full scrub (configured key values, `NAME=value` assignments, bearer tokens,
  credential query parameters), while successful payloads only lose exact
  configured-secret values. New `redact` module;
  `server._redact_secrets` remains as an alias for the diagnostic scrubber.
- **Nested `error`/`errors` values are scrubbed recursively.** Only top-level
  diagnostic *strings* were walked, so `{"error": {"url": "…?token=…"}}` and
  `{"errors": ["…?api_key=…", {"message": …}]}` passed through with their
  credentials intact. Every string inside an `error`/`errors`/`message`/… subtree
  is now treated as diagnostic text at any depth (list or mapping), while codes,
  counts and booleans keep their types; `attempts` entries get the same walk.
  Successful rows are still untouched apart from exact configured-secret removal,
  so signed URLs keep working.
- **A multi-request provider now honours one budget.** Yuanbao made up to five
  HTTP requests per search (initialize, initialized notification, tools/call,
  re-handshake + retry), each with the full configured read timeout, so a 20 s
  budget could take 100 s. New `budget.TimeBudget`: one countdown per provider
  call, `min(configured, remaining)` per sub-request (connect shares the budget
  with read), and no new request once `MIN_REQUEST_SECONDS` is left.

### Security

- **Optional private-target block for `web_fetch`: `WEB_RETRIEVAL_BLOCK_PRIVATE`.**
  **Off by default**, so 0.3.0 behaviour is unchanged: `web_fetch` still fetches
  intranet, `localhost` and metadata URLs, which is a deliberate capability for a
  local agent tool. Set `WEB_RETRIEVAL_BLOCK_PRIVATE=1` (the name this README has
  reserved since 0.1.0) to refuse loopback, RFC1918, link-local, CGNAT and other
  non-public targets before any provider is called; the refusal is per URL
  (`invalid input:` error), so a batch keeps working. New `netguard` module:
  IP literals are decided exactly and without DNS, including bracketed and
  IPv4-mapped IPv6, IPv6 zone ids (`[fe80::1%25eth0]`) and every legacy numeric
  IPv4 spelling the C library accepts (`127.1`, `0177.0.0.1`, `0x7f.0.0.1`,
  `0x7f000001`, `2130706433`); a numeric-only host that cannot be decoded is
  refused. `localhost`, `localhost.` and `*.localhost` are refused without DNS
  (RFC 6761), so a lookup that fails cannot turn one into an allow. Hostnames use
  the platform `socket.getaddrinfo` (so `/etc/hosts`, systemd-resolved and
  host-level controls still apply — no private DNS client is added and the
  dependent host's constraints are not bypassed); the lookup is bounded by
  `WEB_RETRIEVAL_NETGUARD_DNS_TIMEOUT` (default 2 s) and a lookup that does not
  answer is **refused**, not allowed. Every returned address must be globally
  routable, so a public+private answer is rejected. A provider-reported final URL
  is re-checked and fails the call closed when it is non-public.
- **This is not SSRF protection** (stated in `netguard`, the README, the tool
  description and the error text). The fetch happens on the provider's network,
  so there is no connection binding: a redirect hop is only re-checked after it
  happened, and DNS rebinding between our check and the provider's connect cannot
  be excluded. A hostname that does not resolve locally is allowed (logged at
  DEBUG), so an internal-only name is not blocked. Only a design that resolves,
  connects and verifies the peer address can claim that; network isolation
  (container, netns, egress firewall, provider deny-list) remains the outer
  boundary for untrusted input.

### Changed

- `FetchResult.to_dict()` gains `truncated` and `original_length` (additive).
- `web_search` attempts gain `empty` (failover) and `mode` on fan-out `ok` entries
  (which auth mode answered); `list_providers` gains `fetch_policy`
  (`block_private_network`, `block_detection`, `extract_char_limit`).
- No behaviour change by default: private/loopback/link-local fetches work
  exactly as in 0.3.0 unless `WEB_RETRIEVAL_BLOCK_PRIVATE` is set. Default
  chains, provider order, timeouts and the failover payload shape are unchanged
  (the 0.3.0 golden test still passes as-is).
- Tests: offline suites `test_031_empty_answers.py`, `test_031_fetch_content.py`,
  `test_031_redaction.py`, `test_031_budget.py` and `test_031_netguard.py` pin the
  new rules — empty versus malformed containers, truncation reporting, the
  interstitial shapes that used to be false positives (a "CAPTCHA help" page, a
  vendor glossary, an FAQ quoting "Just a moment…", a doc showing one
  `cf_chl_opt` sample, and a help page titled "Access Denied: a practical guide
  to 403 responses"), the default and opt-in netguard paths (legacy numeric
  spellings, zone ids, loopback names, resolver timeout, per-URL tool error,
  redirect fail-closed) and a docs test that rejects SSRF overclaiming.
  `tests/conftest.py` clears `WEB_RETRIEVAL_*` and stubs `netguard._resolve`, so
  the offline suite never touches DNS; the live-provider file stays skipped
  unless it is explicitly enabled.

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
- **Merge layer** (`fanout.py`): URL normalisation for a scheme-less dedupe key
  (`//host[:port]/path?query`, so `http://` and `https://` for one page collapse
  and a default `:80`/`:443` is dropped; leading `www.` stripped, fragment and
  tracking parameters dropped, remaining parameters sorted, trailing slash
  removed), Reciprocal Rank
  Fusion scoring (`k = 60`) with source-count / best-rank / chain-order
  tie-breaks, per-row title and URL from the best-ranked occurrence, longest
  non-empty snippet, first non-empty `published`/`site` in chain order, and a
  `sources` list in chain order.
- **Fan-out observability**: results report `provider: "fanout"` and a `mode` of
  `keyed`/`keyless`/`mixed` (new `AuthMode.MIXED`), `attempts` carries one entry
  per queried provider with `ok` + `results`/`latency_ms`, `error:<msg>`,
  `skipped:not_configured`, `skipped:fanout` for eligible chain members beyond N,
  `skipped:grace` or `skipped:deadline`, and
  `list_providers` reports `search_mode` plus `fanout: {n, grace_s}`. Success
  needs one provider to answer OK, not one hit: an all-empty answer set is a
  successful empty result, as in failover.
- **Tests** (`tests/unit/test_fanout.py`, 67 cases, no network): mode selection
  from env and per call, invalid values, N clamping, `not_configured` skipping
  that does not consume a slot, real concurrency with sleeping stubs, grace
  cut-off including late-success filtering and late-error diagnostics, deadline
  enforcement, one provider failing while others succeed, all failing with the
  attempt trail attached, `fallback_eligible=False` isolation,
  URL-variant dedupe, RRF ordering and every tie-break, snippet/title/extra-field
  merging, `sources`, mixed mode, `list_providers`, and `web_fetch` staying
  failover. A golden-dict test pins the failover payload byte-for-byte to the
  0.2.3 shape.

### Changed

- `AuthMode` gains `MIXED`. `SearchResult.to_dict()` emits `sources` only for rows
  that carry it, so failover payloads are byte-for-byte identical to 0.2.3.
- `web_search` takes an optional `mode` argument; `web_fetch` does not and is
  always failover. README documents both modes with their quota/latency
  trade-off.
- Thread safety reviewed for fan-out: providers are sync HTTP clients run one per
  thread; no provider keeps unsynchronised per-call state. Yuanbao's per-instance
  MCP session id and JSON-RPC counter are guarded by an `RLock`, and a concurrent
  handshake race costs an extra session rather than correctness. Fan-out starts one
  daemon `threading.Thread` per selected provider and collects `(provider, outcome)`
  records from a `queue.Queue`; only public standard-library APIs are used (no
  `concurrent.futures` internals), so the behaviour is portable across Python
  versions. A late successful answer is discarded; a late error stays in the
  attempt trail for diagnosis. A straggler is never joined: it finishes in its
  daemon thread, which cannot delay interpreter exit.

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
