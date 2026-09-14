"""`uwr` CLI — run the MCP server (stdio) or print version."""
from __future__ import annotations

import argparse
import sys

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="uwr",
        description="Universal Web Retrieval MCP — resilient web search/fetch with provider failover.")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args()
    if args.version:
        print(f"uwr {__version__}")
        return
    from .server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
