# uwr — npx launcher

`npx -y uwr` starts [Universal Web Retrieval MCP](https://github.com/withgardener/universal-web-retrieval-mcp),
a stdio MCP server that gives any agent resilient web search and page fetching
through a configurable provider chain (AnySearch → Tavily → DDGS by default,
plus Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave and Jina Reader on request)
with automatic failover.

**This package contains no server code and no dependencies.** It is a thin
launcher that runs the published Python package
[`uwr`](https://pypi.org/project/uwr/) on PyPI, so there is exactly one
implementation to trust and one version to update.

## Quick Start

```json
{
  "mcpServers": {
    "universal-web-retrieval": {
      "command": "npx",
      "args": ["-y", "uwr"]
    }
  }
}
```

**No API key is required.** With no keys configured the server uses the keyless
paths of AnySearch/Tavily and falls back to keyless DDGS.

Keys are optional environment variables; add only the ones you have:

```json
{
  "mcpServers": {
    "universal-web-retrieval": {
      "command": "npx",
      "args": ["-y", "uwr"],
      "env": {
        "TAVILY_API_KEY": "tvly-...",
        "ANYSEARCH_API_KEY": "..."
      }
    }
  }
}
```

Recognised keys (see the [main README](https://github.com/withgardener/universal-web-retrieval-mcp#configuration)
for the full table): `ANYSEARCH_API_KEY`, `TAVILY_API_KEY`, `YUANBAO_API_KEY`,
`BOCHA_API_KEY`, `DEEPSEEK_API_KEY`, `MOONSHOT_API_KEY`, `ZHIPU_API_KEY`,
`BRAVE_API_KEY`, `JINA_API_KEY`. Chain and timeout tuning uses
`UWR_SEARCH_CHAIN`, `UWR_FETCH_CHAIN`, `UWR_DISABLED_PROVIDERS` and
`WEB_RETRIEVAL_TIMEOUT`.

You can also `npx -y @withgardener/uwr` — same launcher, scoped alias.

## How the runner is resolved

The launcher never bundles Python. It looks for a way to run `uwr==0.2.0` (the
version is pinned to this package's version) in this order:

1. `UWR_NPM_RUNNER` — force one of `uvx`, `pipx`, `python`, `bundled-uv`.
2. `uvx` on `PATH` → `uvx --from uwr==<version> uwr`
3. `uv` on `PATH` → `uv tool run --from uwr==<version> uwr`
4. `pipx` on `PATH` → `pipx run --spec uwr==<version> uwr`
5. **Automatic uv download** — the official Astral standalone build for this
   platform is fetched from the uv GitHub release, its sha256 is verified
   against `lib/uv-pin.json` *before* unpacking, and it is cached in
   `$XDG_CACHE_HOME/uwr-npm/uv-<ver>/` (`~/.cache` by default,
   `%LOCALAPPDATA%` on Windows). A hash mismatch aborts and deletes the
   download. A cached copy that is too small to be a real uv (an interrupted
   download) is discarded and fetched again instead of being trusted.
6. Otherwise it prints install hints and exits 1.

`python` (`UWR_NPM_RUNNER=python`) runs `python -m universal_web_retrieval.cli`, i.e.
an `uwr` you installed yourself with `pip install uwr`.

### Environment switches

| Variable | Effect |
|---|---|
| `UWR_NPM_RUNNER` | Force `uvx` / `pipx` / `python` / `bundled-uv` |
| `UWR_NPM_NO_DOWNLOAD=1` | Never download uv; fail with install hints instead |
| `XDG_CACHE_HOME` | Where the auto-downloaded uv is cached |

**Proxies:** the uv download uses Node's built-in `https` client, which ignores
`HTTPS_PROXY` / `NO_PROXY`; `NODE_USE_ENV_PROXY=1` does not change that (verified
on Node 24.11 — the download still connects directly). Behind a mandatory proxy,
install `uv`/`pipx` yourself, or `pip install uwr` and set
`UWR_NPM_RUNNER=python`, instead of relying on the automatic download.

**stdout is reserved for MCP JSON-RPC.** All launcher diagnostics go to stderr,
so an MCP client can parse the stream without filtering.

## Diagnose

```bash
npx -y uwr --launcher-info
# {"launcher_version":"0.2.0","pinned_pypi_version":"0.2.0","runner_resolved":"uvx"}
```

This prints the launcher version, the pinned PyPI version and the runner it
would use, without starting the server. Every other argument is forwarded
untouched to `uwr` (`npx -y uwr --version` → `uwr 0.2.0`).

## Prefer uvx directly?

If you already have uv, skip npm entirely:

```json
{"mcpServers": {"universal-web-retrieval": {"command": "uvx", "args": ["uwr"]}}}
```

## License

MIT — see [LICENSE](./LICENSE).
