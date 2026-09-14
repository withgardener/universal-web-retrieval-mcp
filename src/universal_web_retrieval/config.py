"""Environment-driven configuration with sensible defaults.

API keys are read from os.environ AT CALL TIME (not import time) so that
consumers can inject keys via MCP server `env` config, and so that invalid-key
behavior can be tested by mutating the environment.
"""
from __future__ import annotations

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def anysearch_api_key() -> str:
    return (os.environ.get("ANYSEARCH_API_KEY") or "").strip()


def tavily_api_key() -> str:
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


# Per-provider timeouts (seconds): (connect, read). Overridable via env.
def anysearch_timeout() -> tuple:
    return (float(os.environ.get("WEB_RETRIEVAL_ANYSEARCH_CONNECT_TIMEOUT", "5") or 5),
            float(os.environ.get("WEB_RETRIEVAL_ANYSEARCH_TIMEOUT", "20") or 20))

def tavily_timeout() -> tuple:
    return (float(os.environ.get("WEB_RETRIEVAL_TAVILY_CONNECT_TIMEOUT", "5") or 5),
            float(os.environ.get("WEB_RETRIEVAL_TAVILY_TIMEOUT", "20") or 20))

def ddgs_timeout() -> float:
    return float(os.environ.get("WEB_RETRIEVAL_DDGS_TIMEOUT", "15") or 15)

# Overall chain deadline (seconds) so a full fallback walk cannot run unbounded.
def chain_deadline() -> float:
    return float(os.environ.get("WEB_RETRIEVAL_TIMEOUT", "45") or 45)

def max_results_cap() -> int:
    return _int_env("WEB_RETRIEVAL_MAX_RESULTS", 20)

def extract_char_limit() -> int:
    return _int_env("WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT", 15_000)

def log_level() -> str:
    return os.environ.get("WEB_RETRIEVAL_LOG_LEVEL", "WARNING").upper()
