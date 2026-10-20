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

## DeepSeek（官方 Search + Fetch API）

| 项 | 值 |
|---|---|
| 结论 | **官方支持**，且是**结构化搜索 API**，不是"AI 合成答案"型工具 → 本实现按普通 search provider 接入（`kind: search`） |
| Search Endpoint | `POST https://api.deepseek.com/v1/tools/search` |
| Fetch Endpoint | `POST https://api.deepseek.com/v1/tools/fetch`，body `{"url"}`（≤8192 字符、必须 http/https、必须有 host、不得含用户名密码），返回 `{title, content}`，`content` 为 Markdown |
| Auth | `Authorization: Bearer <DEEPSEEK_API_KEY>` + `Content-Type: application/json` |
| Search 入参 | `{"query"（1-128 字符）, "count"（1-50，默认 10）, "need_content"（默认 false）, "ori_question", "localization"}`；查询支持 `site:` / `-site:` / `"短语"` / `lang:` / `after:` / `before:` 运算符 |
| Search 返回 | `{"results": [{rerank_score, title, url, snippet, summary, content, site_name, site_icon, date, authority}]}`；无命中返回空数组 |
| 本实现映射 | `snippet`（无则 `summary`）→`snippet`、`date→published`、`site_name→site`、`rerank_score→score`；`need_content=false`（不拉正文，省延迟）；fetch 的 `content` 按 `WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT` 截断 |
| 错误 | 400 参数、401 auth_failure、402 余额不足、403 抓取被安全策略拒绝（**非鉴权**）、404 无可取内容（**非鉴权**）、408 请求体读超时、413 请求体过大、429 QPS、5xx/504 上游 → 均 fallback-eligible |
| 计费 | Search 与 Fetch 均 **$0.75 / 1000 次成功请求**；只有 HTTP 200 且返回结果（Search 非空 `results`、Fetch 有正文）才计费，失败/空结果/超时不收费；`count` 不影响单价 |
| 限速 | 账户级：Search **100 QPS**、Fetch **25 QPS**，两者独立；Fetch 另有按目标域名的 QPS 限制 |
| 模型参数 | Search API **没有 `model` 参数**，因此本实现不提供 `UWR_DEEPSEEK_MODEL`（加了也是空转，会误导运维） |
| 能力 | search + fetch |
| 文档来源 | https://api-docs.deepseek.com/guides/search （同一页含 Search 与 Fetch 的完整字段/错误/计费/限速表）、发布说明 https://api-docs.deepseek.com/news/news261017 |
| 第三方说法 | npm 项目声称走 Anthropic 兼容端点的服务端工具 `web_search_20250305`；DeepSeek 官方文档没有这个工具，本实现不采用 |
| Last verified | 2026-10-20（文档核对；未做真实调用） |

## Kimi / Moonshot（官方 Web Search + URL Fetch REST）

| 项 | 值 |
|---|---|
| 结论 | 用**独立 REST 工具接口**，不用已废弃的 `$web_search` 内置工具（官方：2026-10-20 下线；新接入一律走 REST） |
| Search Basic | `POST {base}/v1/tools/search`，`$0.002/次` |
| Search Pro | `POST {base}/v1/tools/search_pro`，`$0.003/次`；额外支持 `sites`（≤5 个，OR，不能含空格/括号）、`time_window`（`YYYY` / `YYYY-MM` / `YYYY-MM-DD`，`start` 不晚于 `end`），并返回 `chunks` |
| Fetch | `POST {base}/v1/tools/fetch`，body `{"url"}`（仅 http/https），返回 `{url, markdown, title}`，`$0.002/次` |
| Auth | `Authorization: Bearer <MOONSHOT_API_KEY>` + `Content-Type: application/json`；环境变量同时接受 `KIMI_API_KEY` |
| 入参字段 | `text_query`（**必填**，非空；注意不是 `query`）、`limit`（1-20，默认 5）、`timeout_seconds`（1-60，可选）、`include_content`（仅 Basic，默认 false）。本实现发 `text_query` + `limit`，Basic 另发 `include_content=false`；不转发 `timeout_seconds`，超时由 httpx 与链预算控制 |
| 返回字段 | `{"search_results": [{authority, date, icon, mime, site_name, snippet, text, title, url}]}`；Pro 每项另有 `chunks: [{text, score}]` |
| 本实现映射 | `snippet`（空则用首个 chunk 的 `text`；Basic 再用 `text`）→`snippet`、`date→published`、`site_name→site`、首个 chunk 的 `score→score`；fetch 的 `markdown` 按字符上限截断 |
| 错误 | 400 `invalid_request` / `invalid_url`、401（无 body）auth_failure、403（无 body）账号停用、403 `security_risk`（fetch 专属，**非鉴权**）、404 `markdown_not_found`（**非鉴权**）、408 `client_canceled`、429 `rate_limited`、500/502/504 → 均 fallback-eligible |
| 计费 | 仅 HTTP 200 且 `search_results` 非空（search）/ `markdown` 非空（fetch）计费；失败与空结果免费；三个端点各自计费 |
| Host | `https://api.moonshot.cn`（国内，默认）与 `https://api.moonshot.ai`（国际）都提供同样三个端点；**两边 key 不通用**。`UWR_KIMI_BASE_URL` 切换 |
| 档位 | `UWR_KIMI_SEARCH_TIER=basic`（默认）/`pro`；写错按 basic 处理 |
| 能力 | search + fetch |
| 文档来源 | https://platform.kimi.ai/docs/api/tools-search 、https://platform.kimi.ai/docs/api/tools-search-pro 、https://platform.kimi.ai/docs/api/tools-fetch 、计费 https://platform.kimi.ai/docs/pricing/websearch 、指南 https://platform.kimi.ai/docs/guide/use-web-search ；国内镜像 https://platform.moonshot.cn/docs/guide/use-web-search |
| Last verified | 2026-10-20（逐页核对 OpenAPI schema 与错误表；未做真实调用） |

## Zhipu BigModel（智谱 网络搜索 Web Search API）

| 项 | 值 |
|---|---|
| Endpoint | `POST https://open.bigmodel.cn/api/paas/v4/web_search` |
| Auth | `Authorization: Bearer <ZHIPU_API_KEY>`（亦接受 `BIGMODEL_API_KEY`） |
| 入参 | `search_query`（必填，建议 ≤70 字符，schema `maxLength=70`）、`search_engine`（必填）、`search_intent`（必填布尔，默认 false）、`count`（1-50，默认 10）、可选 `search_domain_filter` / `search_recency_filter`（`oneDay/oneWeek/oneMonth/oneYear/noLimit`）/ `content_size`（`medium`/`high`）/ `request_id` / `user_id` |
| 引擎 | `search_std`（智谱基础版）、`search_pro`（智谱高阶版）、`search_pro_sogou`（搜狗）、`search_pro_quark`（夸克）；`UWR_ZHIPU_ENGINE` 选择，写错回落 `search_std` |
| 返回 | `{id, created, request_id, search_intent: [{query, intent, keywords}], search_result: [{title, content, link, media, icon, refer, publish_date}]}` |
| 本实现映射 | `content→snippet`、`link→url`、`media→site`、`publish_date→published`；`search_intent=false`（agent 工具不需要意图识别那一跳）；查询按 70 字符截断 |
| 错误 | 单一信封 `{"error": {"code", "message"}}`：1701 搜索并发达上限、1702 无可用搜索引擎、1703 引擎未返回有效数据；本实现把 1701 报为并发限流、1703 报为 no results，其余按 code 报错；HTTP 401/403 → auth_failure |
| 计费 | `search_std` 0.01 元/次、`search_pro` 0.03 元/次、`search_pro_sogou` 0.05 元/次、`search_pro_quark` 0.05 元/次；免费额度官方未在文档页给出具体数值（控制台/活动页为准），本实现不假设有免费额度 |
| 能力 | 仅 search（正文读取是另外的 LLM/Agent 产品，没有对应的 REST fetch 接口） |
| 文档来源 | API 参考 https://docs.bigmodel.cn/api-reference/%E5%B7%A5%E5%85%B7-api/%E7%BD%91%E7%BB%9C%E6%90%9C%E7%B4%A2 （OpenAPI servers `https://open.bigmodel.cn/api/`）、产品与价格 https://docs.bigmodel.cn/cn/guide/tools/web-search 、价格页 https://bigmodel.cn/pricing |
| Last verified | 2026-10-20（OpenAPI schema + 价格表核对；未做真实调用） |

## Brave Search API

| 项 | 值 |
|---|---|
| Endpoint | `GET https://api.search.brave.com/res/v1/web/search?q=…&count=…`（官方同时支持 POST） |
| Auth | **`X-Subscription-Token: <BRAVE_API_KEY>` 头**（不是 Bearer；发 Authorization 只会 401） |
| 入参 | `q`（必填，≤600 字符 / 75 词）、`count`（1-20，默认 20）、`offset`（0-9）；另有 `country` / `search_lang` / `ui_lang` / `safesearch` / `freshness` / `result_filter` / `extra_snippets` 等，本实现不发送、走官方默认 |
| 返回 | `{type:"search", query:{original, altered, more_results_available, …}, web:{results:[{title, url, description, age, page_age, language, profile:{name,…}, meta_url:{hostname}, …}]}}` |
| 本实现映射 | `description→snippet`、`page_age`（无则 `age`）→`published`、`profile.name`（无则 `meta_url.hostname`）→`site` |
| 错误 | 401 token 无效 → auth_failure；422 参数不可处理；429 限频；均 fallback-eligible |
| 计费 | Search 计划 **$5.00 / 1000 次请求**，**每月自动赠送 $5 额度**（约 1000 次），容量 50 请求/秒 |
| 部署提示 | 单一全球端点，无中国区 host；国内直连延迟取决于网络路径 |
| 能力 | 仅 search（LLM Context / Answers 是另外的产品，未接入） |
| 文档来源 | API 参考 https://api-dashboard.search.brave.com/api-reference/web/search/get 、产品页 https://api-dashboard.search.brave.com/app/documentation/web-search/get-started 、官方 skill https://github.com/brave/brave-search-skills/blob/main/skills/web-search/SKILL.md 、价格 https://api-dashboard.search.brave.com/documentation/pricing |
| Last verified | 2026-10-20（参数表、响应字段、价格页核对；未做真实调用） |

## Jina Reader（r.jina.ai，fetch only）

| 项 | 值 |
|---|---|
| 为什么可用 keyless | 官方 OpenAPI（https://docs.jina.ai/openapi.json）里该路径 security 为 `[{"HTTPBearer": []}, {}]`——允许匿名；产品页限速表写明 **w/o API Key 20 RPM**、w/ Free API Key 500 RPM、w/ Paid 5000 RPM |
| Endpoint | `GET https://r.jina.ai/?url=<目标 URL>`（OpenAPI path `/` 的 `url` 参数；官方亦支持 `https://r.jina.ai/<url>`）。用 `?url=` 形式，避免目标 URL 自带的查询串被 Reader 当成自己的参数 |
| Auth | 默认**不发** Authorization；配了 `JINA_API_KEY` 则 `Authorization: Bearer <key>` |
| 请求头 | `Accept: application/json`（不加则返回纯 Markdown 文本）、`X-Timeout`（1-180 秒，由链剩余预算推导） |
| 返回 | `{code, status, data: {title, url, content, description, publishedTime, …}, meta}`；`data.content` 为 Markdown |
| 错误 | 400/401/402/403/409/413/422/429/451/500/503；**keyless 的 401 不算 auth_failure**（没 key 可查），带 key 的 401 才算；451 单独报"因法律原因不可用" |
| 计费 | 免费额度按 RPM 计：keyless 20 RPM、免费 key 500 RPM；付费档更高（官方表 5000 RPM） |
| 能力 | 仅 fetch；**不进任何默认链**（keyless 属性会让旧部署在无 key 时被它接管），只能显式点名 |
| 文档来源 | https://jina.ai/reader （端点 + 限速表）、https://docs.jina.ai/openapi.json （安全声明、`url` 参数、JSON 信封与 `Reader_FormattedPageDto`） |
| Last verified | 2026-10-20（产品页限速表 + OpenAPI schema 核对；未做真实调用） |

## 定价与免费额度（2026-10-20 核验）

| Provider | 目录价 | 免费额度 | 来源 |
|---|---|---|---|
| Yuanbao 极速版 | 14 元/千次 | 控制台"活动专区"发放，**额度数值需登录查看，未核实** | https://cloud.tencent.com/document/product/1806/121798 、 https://cloud.tencent.com/product/wsa |
| Yuanbao 标准版 | 20 元/千次；资源包 5 万次 950 元/年、100 万次 18,500 元/年、1000 万次 175,000 元/年 | 同上 | 同上 |
| Yuanbao 旗舰版 | 26 元/千次（仅企业用户可开通） | 同上 | 同上 |
| Bocha Web Search | 0.036 元/次（≈36 元/千次）；体验包 1000 次 3.6 元；标准包 36 元；1 万次 320 元；10 万次 3000 元；100 万次 28,000 元；1000 万次 250,000 元 | **免费试用 1000 次（0 元，3 个月）** | https://bocha-ai.feishu.cn/wiki/JYSbwzdPIiFnz4kDYPXcHSDrnZb |
| DeepSeek Search | $0.75 / 1000 次成功请求 | 无按量免费额度；2026-11-01 00:00（北京时间）前，经 DeepSeek Harness / Claude Code / Codex 的服务端工具调用免费——**直接调 API 不在该优惠内** | https://api-docs.deepseek.com/guides/search |
| DeepSeek Fetch | $0.75 / 1000 次成功请求（与 Search 分别计费，QPS 25） | 同上 | https://api-docs.deepseek.com/guides/search |
| Kimi Web Search Basic | $0.002/次（`/v1/tools/search`） | 无免费额度；失败或空结果不计费 | https://platform.kimi.ai/docs/pricing/websearch |
| Kimi Web Search Pro | $0.003/次（`/v1/tools/search_pro`） | 同上 | 同上 |
| Kimi URL Fetch | $0.002/次（`/v1/tools/fetch`） | 同上（404 `markdown_not_found` 不计费） | 同上 |
| Zhipu `search_std` | 0.01 元/次 | 文档页未给具体免费额度（控制台/活动页为准，**未核实**） | https://docs.bigmodel.cn/cn/guide/tools/web-search |
| Zhipu `search_pro` | 0.03 元/次 | 同上 | 同上 |
| Zhipu `search_pro_sogou` | 0.05 元/次 | 同上 | 同上 |
| Zhipu `search_pro_quark` | 0.05 元/次 | 同上 | 同上 |
| Brave Search | $5.00 / 1000 次请求（50 请求/秒） | **每月自动赠送 $5 额度**（约 1000 次请求） | https://api-dashboard.search.brave.com/documentation/pricing |
| Jina Reader | 按 RPM 分档：keyless 20 RPM、免费 key 500 RPM、付费 5000 RPM | keyless 可用（20 RPM）即免费额度 | https://jina.ai/reader |

注：Yuanbao 的服务调用失败不计费；Bocha 与 DeepSeek 的计费口径见各自官方页。上表中"未核实"项不得当作可用额度。
