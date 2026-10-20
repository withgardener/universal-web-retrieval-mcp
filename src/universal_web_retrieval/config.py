"""Environment-driven configuration with sensible defaults.

API keys are read from os.environ AT CALL TIME (not import time) so that
consumers can inject keys via MCP server `env` config, and so that invalid-key
behavior can be tested by mutating the environment.

Provider chains are also resolved at call time (see ``router.py``), so an
operator can change ``UWR_SEARCH_CHAIN`` in the MCP server env — after the
server process restarts — without any code change.
"""
from __future__ import annotations

import os
from typing import List

# Default chains. v0.1.x hard-coded exactly these; they stay the default so an
# existing deployment behaves identically after upgrading to 0.2.x.
DEFAULT_SEARCH_CHAIN: tuple = ("anysearch", "tavily", "ddgs")
DEFAULT_FETCH_CHAIN: tuple = ("anysearch", "tavily", "ddgs")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def anysearch_api_key() -> str:
    return (os.environ.get("ANYSEARCH_API_KEY") or "").strip()


def tavily_api_key() -> str:
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


def yuanbao_api_key() -> str:
    """Tencent Cloud Yuanbao Search (元宝搜索, product code ``wsa``) service key.

    The MCP marketplace page names it ``WSA_API_KEY``; accept both names so an
    operator can reuse whichever label their console/apiary uses.
    """
    return ((os.environ.get("YUANBAO_API_KEY") or os.environ.get("WSA_API_KEY") or "")
            .strip())


def bocha_api_key() -> str:
    return (os.environ.get("BOCHA_API_KEY") or "").strip()


def deepseek_api_key() -> str:
    return (os.environ.get("DEEPSEEK_API_KEY") or "").strip()


def kimi_api_key() -> str:
    """Moonshot / Kimi platform key.

    Official docs and samples use ``MOONSHOT_API_KEY``; the Kimi console also
    labels it "Kimi API key", so ``KIMI_API_KEY`` is accepted as an alias.
    """
    return ((os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY") or "")
            .strip())


def zhipu_api_key() -> str:
    """Zhipu BigModel (智谱开放平台) key — ``ZHIPU_API_KEY`` / ``BIGMODEL_API_KEY``."""
    return ((os.environ.get("ZHIPU_API_KEY") or os.environ.get("BIGMODEL_API_KEY") or "")
            .strip())


def brave_api_key() -> str:
    return (os.environ.get("BRAVE_API_KEY") or "").strip()


def jina_api_key() -> str:
    """Optional: Jina Reader works without a key (lower rate limit)."""
    return (os.environ.get("JINA_API_KEY") or "").strip()


# Per-provider timeouts (seconds): (connect, read). Overridable via env.
def anysearch_timeout() -> tuple:
    return (float(os.environ.get("WEB_RETRIEVAL_ANYSEARCH_CONNECT_TIMEOUT", "5") or 5),
            float(os.environ.get("WEB_RETRIEVAL_ANYSEARCH_TIMEOUT", "20") or 20))

def tavily_timeout() -> tuple:
    return (float(os.environ.get("WEB_RETRIEVAL_TAVILY_CONNECT_TIMEOUT", "5") or 5),
            float(os.environ.get("WEB_RETRIEVAL_TAVILY_TIMEOUT", "20") or 20))

def ddgs_timeout() -> float:
    return float(os.environ.get("WEB_RETRIEVAL_DDGS_TIMEOUT", "15") or 15)

def yuanbao_timeout() -> tuple:
    # Yuanbao is China-hosted; a cold MCP handshake + search is slower than a
    # bare REST call, so the read budget matches the other HTTP providers.
    return (_float_env("UWR_YUANBAO_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_YUANBAO_TIMEOUT", 20.0))

def bocha_timeout() -> tuple:
    return (_float_env("UWR_BOCHA_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_BOCHA_TIMEOUT", 20.0))

def deepseek_timeout() -> tuple:
    return (_float_env("UWR_DEEPSEEK_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_DEEPSEEK_TIMEOUT", 20.0))

def kimi_timeout() -> tuple:
    return (_float_env("UWR_KIMI_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_KIMI_TIMEOUT", 20.0))

def zhipu_timeout() -> tuple:
    return (_float_env("UWR_ZHIPU_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_ZHIPU_TIMEOUT", 20.0))

def brave_timeout() -> tuple:
    return (_float_env("UWR_BRAVE_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_BRAVE_TIMEOUT", 20.0))

def jina_timeout() -> tuple:
    # Reader drives a headless browser, so its read budget is wider than the
    # plain JSON API providers.
    return (_float_env("UWR_JINA_CONNECT_TIMEOUT", 5.0),
            _float_env("UWR_JINA_TIMEOUT", 30.0))


def yuanbao_mcp_url() -> str:
    return (os.environ.get("UWR_YUANBAO_MCP_URL")
            or "https://api.wsa.cloud.tencent.com/Mcp").strip()


def bocha_base_url() -> str:
    # Both api.bochaai.com (platform homepage / official MCP repo) and
    # api.bocha.cn (current docs quick-start) are first-party hosts; the
    # historical one is the default and the other is one env var away.
    return (os.environ.get("UWR_BOCHA_BASE_URL") or "https://api.bochaai.com").strip().rstrip("/")


def deepseek_base_url() -> str:
    return (os.environ.get("UWR_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")


def kimi_base_url() -> str:
    """Moonshot API host.

    ``https://api.moonshot.cn`` (China, the default) and ``https://api.moonshot.ai``
    (international) are both first-party hosts serving the same
    ``/v1/tools/search``, ``/v1/tools/search_pro`` and ``/v1/tools/fetch``
    endpoints; a key from one platform does not work on the other.
    """
    return (os.environ.get("UWR_KIMI_BASE_URL") or "https://api.moonshot.cn").strip().rstrip("/")


KIMI_TIERS: tuple = ("basic", "pro")


def kimi_search_tier() -> str:
    """``UWR_KIMI_SEARCH_TIER``: ``basic`` (default) or ``pro``.

    ``basic`` posts to ``/v1/tools/search`` ($0.002/call); ``pro`` posts to
    ``/v1/tools/search_pro`` ($0.003/call). Anything unrecognised falls back to
    ``basic`` so a typo cannot silently triple nothing — it just picks the
    documented default.
    """
    value = (os.environ.get("UWR_KIMI_SEARCH_TIER") or "").strip().lower()
    return value if value in KIMI_TIERS else "basic"


ZHIPU_ENGINES: tuple = ("search_std", "search_pro", "search_pro_sogou", "search_pro_quark")


def zhipu_engine() -> str:
    """``UWR_ZHIPU_ENGINE`` — one of ``search_std`` (default), ``search_pro``,
    ``search_pro_sogou``, ``search_pro_quark``. Unknown values fall back to the
    documented default rather than sending a value the API would reject."""
    value = (os.environ.get("UWR_ZHIPU_ENGINE") or "").strip().lower()
    return value if value in ZHIPU_ENGINES else "search_std"


def zhipu_base_url() -> str:
    return (os.environ.get("UWR_ZHIPU_BASE_URL") or "https://open.bigmodel.cn").strip().rstrip("/")


def brave_base_url() -> str:
    return (os.environ.get("UWR_BRAVE_BASE_URL") or "https://api.search.brave.com").strip().rstrip("/")


def jina_base_url() -> str:
    """Jina Reader host — keyless, so it is never part of a default chain."""
    return (os.environ.get("UWR_JINA_BASE_URL") or "https://r.jina.ai").strip().rstrip("/")


# Overall chain deadline (seconds) so a full fallback walk cannot run unbounded.
def chain_deadline() -> float:
    return float(os.environ.get("WEB_RETRIEVAL_TIMEOUT", "45") or 45)

def max_results_cap() -> int:
    return _int_env("WEB_RETRIEVAL_MAX_RESULTS", 20)

def extract_char_limit() -> int:
    return _int_env("WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT", 15_000)

def log_level() -> str:
    return os.environ.get("WEB_RETRIEVAL_LOG_LEVEL", "WARNING").upper()


def _csv_env(name: str) -> List[str]:
    """Parse a comma-separated env var into lower-case, de-duplicated names.

    Order is preserved verbatim — the chain order IS the fallback order. An
    unset/blank value yields an empty list (the caller falls back to defaults);
    garbage tokens are returned as-is so the registry can report them.
    """
    raw = os.environ.get(name) or ""
    out: List[str] = []
    for token in raw.replace(";", ",").split(","):
        name_ = token.strip().strip("'\"").lower()
        if name_ and name_ not in out:
            out.append(name_)
    return out


def search_chain() -> List[str]:
    """``UWR_SEARCH_CHAIN`` — e.g. ``anysearch,tavily,yuanbao,bocha,ddgs``."""
    return _csv_env("UWR_SEARCH_CHAIN")


def fetch_chain() -> List[str]:
    """``UWR_FETCH_CHAIN`` — only search+fetch providers belong here."""
    return _csv_env("UWR_FETCH_CHAIN")


def disabled_providers() -> frozenset:
    """``UWR_DISABLED_PROVIDERS`` — names switched off without editing chains."""
    return frozenset(_csv_env("UWR_DISABLED_PROVIDERS"))
