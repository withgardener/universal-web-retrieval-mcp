"""Provider registry — the single place that knows every provider this build ships.

``list_providers``, the env-driven chains and the per-call ``providers`` override
all resolve names through here, so adding a provider is a one-line change.
"""
from __future__ import annotations

import logging
from typing import Dict, List

from ..errors import Capability, Provider
from .anysearch import AnySearchProvider
from .bocha import BochaProvider
from .ddgs import DDGSProvider
from .deepseek import DeepSeekProvider
from .tavily import TavilyProvider
from .yuanbao import YuanbaoProvider

log = logging.getLogger("universal_web_retrieval.registry")

# Instances are module-level singletons: Yuanbao caches its MCP session id on
# the instance, and provider objects are stateless otherwise.
_REGISTRY: Dict[str, Provider] = {
    p.NAME: p for p in (
        AnySearchProvider(),
        TavilyProvider(),
        DDGSProvider(),
        YuanbaoProvider(),
        BochaProvider(),
        DeepSeekProvider(),
    )
}

_BUILD_ORDER: tuple = ("anysearch", "tavily", "ddgs", "yuanbao", "bocha", "deepseek")


def registry() -> Dict[str, Provider]:
    """Name -> provider instance (shared singletons)."""
    return _REGISTRY


def build_registry() -> Dict[str, Provider]:
    """Return a fresh registry copy (same singleton instances)."""
    return dict(_REGISTRY)


def provider_names() -> List[str]:
    return [n for n in _BUILD_ORDER if n in _REGISTRY]


def providers_for(capability: Capability) -> List[str]:
    return [n for n in provider_names() if _REGISTRY[n].supports(capability)]
