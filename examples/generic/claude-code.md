# Claude Code 接入

```bash
claude mcp add universal-web-retrieval \
  --env PYTHONPATH=/path/to/universal-web-retrieval-mcp/src \
  --env TAVILY_API_KEY=tvly-... \
  -- python -m universal_web_retrieval
```

验证：`claude mcp list` 应显示 universal-web-retrieval ✓ connected，
会话内 `/mcp` 可见 web_search / web_fetch 两个工具。
