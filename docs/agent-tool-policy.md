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
4. The agent should not bypass UWR to call AnySearch/Tavily/DDGS directly — provider
   selection, keyed/keyless auth, and failover are UWR's internal responsibility.
5. Only after **all three UWR providers fail** (the tool returns a failure result)
   may the hosting agent consider other retrieval capabilities.

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
