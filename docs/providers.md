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

## Yuanbao（腾讯云 元宝搜索, 产品代号 `wsa`）

| 项 | 值 |
|---|---|
| 选用形态 | **官方 MCP 端点**（JSON-RPC 2.0 over Streamable HTTP），不是签名 API |
| Endpoint | `POST https://api.wsa.cloud.tencent.com/Mcp` |
| 协议 | MCP `2025-06-18`；先 `initialize`（拿 `Mcp-Session-Id`），再 `notifications/initialized`，再 `tools/call` |
| Auth | `Authorization: Bearer <WSA_API_KEY>`（本实现同时接受 `YUANBAO_API_KEY`） |
| 工具 | 只有一个：`SearchPro` |
| SearchPro 入参 | `Query`（必填，≤1500 UTF-8 字节）、`Site`、`Mode`（0 公开网页 / 1 垂直权威 / 2 混合）、`Freshness`（d1-30 / m1-12 / y1-5 / 日期 / 日期区间）、`Cnt`（字符串 `"10"/"20"/"30"/"40"/"50"`）、`Industry`（gov/news/acad/finance） |
| 返回 | `Pages` 为**字符串数组**，每项是 JSON 字符串：`{title, date, url, passage, content, site, score, authority_level, pics, favicon, deeplinks}` |
| 本实现入参 | 只发 `Query` + `Cnt`（`Cnt` 由 `limit` 向上取整到 10/20/30/40/50，再本地截断；`Query` 超 1500 字节按码点截断） |
| 错误映射 | `UnauthorizedOperation` → auth_failure（ERROR 日志）；`ResourceNotFound` → 未开通服务；`ResourceUnavailable` → 欠费；`RequestLimitExceeded` → 限频；均 fallback-eligible |
| 为何不用 API-3.0 | `wsa.tencentcloudapi.com` + `X-TC-Action: SearchPro` + `X-TC-Version: 2025-05-08` 需要 TC3-HMAC-SHA256 签名（SecretId+SecretKey），字段契约与 MCP 版一致；MCP 版只需一个服务 key，是腾讯给 agent 的推荐接入方式 |
| 能力 | 仅 search（产品无正文抓取接口） |
| 文档来源 | https://cloud.tencent.com/developer/mcp/server/11764 （MCP 广场，2026-10-20 页面核对）；API-3.0 对照 https://cloud.tencent.com/document/product/1806/121811 ；产品/错误码 https://cloud.tencent.com/document/product/1806/121794 、/121820 |
| Last verified | 2026-10-20（文档核对；**未做真实调用**——无 key，按任务要求不产生真实请求） |

## Bocha（博查 Web Search API）

| 项 | 值 |
|---|---|
| Endpoint | `POST https://api.bochaai.com/v1/web-search`（当前文档 quick-start 写 `api.bocha.cn`，两者都是官方域名；`UWR_BOCHA_BASE_URL` 可切换） |
| Auth | `Authorization: Bearer <BOCHA_API_KEY>` + `Content-Type: application/json` |
| 入参 | `{"query", "summary": true, "freshness": "noLimit", "count": 1-50}`；`freshness` 可选 `noLimit/oneDay/oneWeek/oneMonth/oneYear/YYYY-MM-DD/YYYY-MM-DD..YYYY-MM-DD` |
| 返回 | `{code, log_id, msg, data: {"_type": "SearchResponse", "webPages": {"value": [{name, url, snippet, summary, siteName, siteIcon, datePublished, dateLastCrawled}]}}}` |
| 本实现映射 | `name→title`、`summary`（无则 `snippet`）→`snippet`、`datePublished→published`、`siteName→site` |
| 错误 | HTTP 401/403 → auth_failure；body `code` 401/403 → auth_failure，429 → 限频；其余按 `code` 报错 |
| 能力 | 仅 search（正文抓取是另一个产品，未接入） |
| 文档来源 | https://open.bochaai.com/ （首页 curl + 响应样例）、Web Search API 文档 https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK 、官方 MCP 实现 https://github.com/BochaAI/bocha-search-mcp/blob/master/src/bocha_search_mcp/server.py |
| Last verified | 2026-10-20（文档 + 官方 MCP 源码核对；未做真实调用） |

## DeepSeek（官方 Search API）

| 项 | 值 |
|---|---|
| 结论 | **官方支持**，且是**结构化搜索 API**，不是"AI 合成答案"型工具 → 本实现按普通 search provider 接入（`kind: search`） |
| Endpoint | `POST https://api.deepseek.com/v1/tools/search` |
| Auth | `Authorization: Bearer <DEEPSEEK_API_KEY>` + `Content-Type: application/json` |
| 入参 | `{"query"（1-128 字符）, "count"（1-50，默认 10）, "need_content"（默认 false）, "ori_question", "localization"}`；查询支持 `site:` / `-site:` / `"短语"` / `lang:` / `after:` / `before:` 运算符 |
| 返回 | `{"results": [{rerank_score, title, url, snippet, summary, content, site_name, site_icon, date, authority}]}`；无命中返回空数组 |
| 本实现映射 | `snippet`（无则 `summary`）→`snippet`、`date→published`、`site_name→site`、`rerank_score→score`；`need_content=false`（不拉正文，省延迟） |
| 错误 | 400 参数、401 auth_failure、402 余额不足、403 抓取被策略拒绝、404 无可取内容、429 QPS、5xx/504 上游 → 均 fallback-eligible |
| 计费 | Search 与 Fetch 均 **$0.75 / 1000 次成功请求**；只有 HTTP 200 且返回非空结果才计费，失败/空结果/超时不收费；`count` 不影响单价 |
| 模型参数 | Search API **没有 `model` 参数**，因此本实现不提供 `UWR_DEEPSEEK_MODEL`（加了也是空转，会误导运维） |
| 能力 | 仅 search（官方另有 `POST /v1/tools/fetch`，未接入） |
| 文档来源 | https://api-docs.deepseek.com/guides/search （完整字段/错误/计费/QPS 表）、发布说明 https://api-docs.deepseek.com/news/news261017 |
| 第三方说法 | npm 项目声称走 Anthropic 兼容端点的服务端工具 `web_search_20250305`；DeepSeek 官方文档没有这个工具，本实现不采用 |
| Last verified | 2026-10-20（文档核对；未做真实调用） |

## 定价与免费额度（2026-10-20 核验）

| Provider | 目录价 | 免费额度 | 来源 |
|---|---|---|---|
| Yuanbao 极速版 | 14 元/千次 | 控制台"活动专区"发放，**额度数值需登录查看，未核实** | https://cloud.tencent.com/document/product/1806/121798 、 https://cloud.tencent.com/product/wsa |
| Yuanbao 标准版 | 20 元/千次；资源包 5 万次 950 元/年、100 万次 18,500 元/年、1000 万次 175,000 元/年 | 同上 | 同上 |
| Yuanbao 旗舰版 | 26 元/千次（仅企业用户可开通） | 同上 | 同上 |
| Bocha Web Search | 0.036 元/次（≈36 元/千次）；体验包 1000 次 3.6 元；标准包 36 元；1 万次 320 元；10 万次 3000 元；100 万次 28,000 元；1000 万次 250,000 元 | **免费试用 1000 次（0 元，3 个月）** | https://bocha-ai.feishu.cn/wiki/JYSbwzdPIiFnz4kDYPXcHSDrnZb |
| DeepSeek Search | $0.75 / 1000 次成功请求 | 无按量免费额度；2026-11-01 00:00（北京时间）前，经 DeepSeek Harness / Claude Code / Codex 的服务端工具调用免费——**直接调 API 不在该优惠内** | https://api-docs.deepseek.com/guides/search |

注：Yuanbao 的服务调用失败不计费；Bocha 与 DeepSeek 的计费口径见各自官方页。上表中"未核实"项不得当作可用额度。
