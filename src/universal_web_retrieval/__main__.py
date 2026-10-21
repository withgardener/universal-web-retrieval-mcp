"""python -m universal_web_retrieval — same entry point as the `uwr` console script.

Delegating to the CLI keeps `--version` (and any future flag) working; with no flags it starts the
stdio MCP server exactly as before.
"""
from .cli import main

main()
