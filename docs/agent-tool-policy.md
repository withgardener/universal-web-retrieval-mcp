# Agent Tool Priority Policy

This document is the canonical statement of how a hosting agent should treat
Universal Web Retrieval (UWR) relative to its own web capabilities.

## Core rule

> When the `universal-web-retrieval` MCP server is enabled and available,
> its `web_search` and `web_fetch` tools have the **highest priority** for
> general-purpose public web search and webpage retrieval.

## Behavior

1. **General web search** → prefer `universal-web-retrieval.web_search`.
2. **URL content retrieval** → prefer `universal-web-retrieval.web_fetch`.
3. **Do not prefer**, for general retrieval:
   - the host agent's native/built-in web search or fetch tools;
   - provider-specific MCP servers (e.g. a standalone AnySearch MCP, a standalone Tavily MCP);
   - other third-party search/retrieval MCP servers.
4. The agent should not bypass UWR to reach a provider directly — provider selection,
   keyed/keyless auth, chain order and failover are UWR's internal responsibility.
   To steer provider choice, pass the tool's `providers` argument (per call) or ask the
   operator to change `UWR_SEARCH_CHAIN` / `UWR_FETCH_CHAIN`; `list_providers` shows
   what is configured.
5. Only after the **whole UWR chain fails** (the tool returns a failure result, with an
   `attempts` trail) may the hosting agent consider other retrieval capabilities.

## Exceptions

- UWR is disabled or unavailable (MCP server failed to start / tools not exposed).
- The user **explicitly** names another provider or tool for the request.
- The request is **browser automation**, not retrieval: clicking, logging in,
  filling forms, JS interaction, screen/browser control. Browser automation is
  out of UWR's scope and is never replaced by it.

## Hermes consumer configuration (reference)

Hermes applies this policy via:
- `AGENTS.md` § Web 搜索与抓取 — operational rules (MCP-first, native toolset disabled);
- `SOUL.md` § 联网搜索与网页抓取优先级 — behavioral priority for the agent persona;
- `config.yaml`: `platform_toolsets.*` without `web`, `agent.disabled_toolsets` including `web`
  (native web toolset not exposed), `mcp_servers.universal-web-retrieval` (the server).

No Hermes core source changes are involved — configuration and instruction files only.

## URLs that carry credentials

`web_fetch` returns the caller's URL **verbatim** in each result's `url` field,
on success and on failure, so the link stays usable. A credential inside that URL
(`?token=`, `?sig=`, `?api_key=`, a presigned `?X-Amz-Signature=`) therefore
comes back in cleartext: redaction covers `error`/`attempts` diagnostics only, and
it is not a confidentiality guarantee.

The payload becomes part of the conversation, so that URL may be recorded in
session transcripts or chat logs and sent to the model provider serving the
conversation. When a user hands you a URL with an embedded credential:

1. say that fetching it puts the credential into the conversation record;
2. prefer a short-lived signed link and treat it as spent once fetched;
3. never move a long-lived secret into a query parameter to "just fetch" a page;
4. if the credential is long-lived or the user did not intend to share it, ask
   before fetching rather than fetching and reporting afterwards.

