# Provider 官方规范核验记录

> 目的：记录每个 provider 的官方调用契约与验证时间，避免半年后凭印象重写。
> 任何 provider 实现改动前，先回到官方文档核对本页。

## AnySearch

| 项 | 值 |
|---|---|
| Search | `POST https://api.anysearch.com/v1/search` body `{"query", "max_results"}` |
| Extract | `POST https://api.anysearch.com/v1/extract` body `{"url"}` |
| 响应壳 | `{code: 0, message, data: {...}}`；`code != 0` 为错误 |
| Search 返回 | `data.results[] = {title, url, snippet, content}` |
| Extract 返回 | `data = {url, title, content}`（content 为 Markdown，50k 截断） |
| Keyed auth | `Authorization: Bearer <ANYSEARCH_API_KEY>` |
| Keyless | **完全省略 Authorization header**（官方 mcp-install.md："remove the Authorization entry"） |
| 无效 key | HTTP 401 `{"code":-1,"message":"Invalid API key."}` |
| 文档来源 | https://anysearch.com/install/mcp-install.md（官方） |
| Official MCP implementation | https://github.com/anysearch-ai/anysearch-mcp-server（服务端闭源，README 为官方契约；commit f4ca4d4, 2026-08-24 reviewed） |
| Behaviors mirrored | keyed `Authorization: Bearer`；keyless 完全省略 Authorization header；`{code:0}` 响应壳 |
| Intentional deviations | **X-Anysearch-Client header intentionally not mirrored** — 官方示例带它做客户端遥测分类，Universal 作为独立实现不冒充任何官方 client |
| Last verified | 2026-09-14（keyed 401 实测 + keyless 成功实测 + 官方 MCP README 对照） |

## Tavily

| 项 | 值 |
|---|---|
| Search | `POST https://api.tavily.com/search` body `{"query", "max_results", "include_raw_content", "include_images"}` |
| Extract | `POST https://api.tavily.com/extract` body `{"urls": [...], "include_images"}` |
| Keyed auth | `Authorization: Bearer <TAVILY_API_KEY>` |
| Keyless | `X-Tavily-Access-Mode: keyless` header（官方 Keyless Access，schema 与 keyed 相同） |
| Keyless 范围 | 仅 `/search` 与 `/extract`；`/crawl` `/map` `/research` 需 key |
| Keyless 限速 | 有限速但官方不公开数值；超限返回自然语言提示 |
| 免费注册 | 1,000 credits/月，无需信用卡 |
| 文档来源 | https://docs.tavily.com/documentation/keyless（官方） |
| Official MCP implementation | https://github.com/tavily-ai/tavily-mcp（commit cf38d5c, 2026-09-10 reviewed） |
| Behaviors mirrored | keyed `Authorization: Bearer`；keyless `X-Tavily-Access-Mode: keyless`；keyed/keyless schema 相同（共享 request 构造与解析器） |
| Intentional deviations | **X-Client-Source / X-Session-Id / X-Human-Id intentionally not mirrored** — 官方 MCP 的遥测与会话追踪头，Universal 不冒充 tavily-mcp 客户端、不做会话关联；我们用自有 `X-Client-Name: universal-web-retrieval` 标识 |
| Last verified | 2026-09-14（keyed extract 实测 200 + keyless search 实测 200 + 官方 MCP 源码逐行对照） |

## DDGS

| 项 | 值 |
|---|---|
| 包 | `ddgs`（Dux Distributed Global Search），**实测版本 9.16.0** |
| Search | `DDGS(timeout=N).text(query, max_results=N)` → `[{title, href, body, ...}]` |
| Extract | `DDGS(timeout=N).extract(url, fmt="text_markdown")` → `{"url": ..., "content": ...}` |
| fmt 选项 | `"text_markdown"` / `"text_plain"` / `"text_rich"` / `"text"`(raw HTML) / `"content"`(bytes) |
| Auth | 无 key 概念，始终 keyless |
| 能力边界 | 普通 HTML/新闻/博客/文档可抓；JS 渲染/登录墙/CAPTCHA 不保证 |
| 文档来源 | 包内 docstring（`inspect.getdoc`）+ 实测（无官方 MCP，`ddgs` 包即官方 SDK） |
| Last verified | 2026-09-14（extract 真实抓取 example.com 0.2s + hermes-agent.nousresearch.com 成功） |
