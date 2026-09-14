"""Provider router: capability -> ordered provider chain with error classification.

``deadline`` is an explicit absolute monotonic timestamp (or None). Batch callers
pass ONE shared deadline across all URLs so the overall batch is bounded; single
calls get a fresh deadline per call.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .. import config
from ..errors import Capability, InvalidInputError, Provider, ProviderError, SearchResult, FetchResult
from .anysearch import AnySearchProvider
from .ddgs import DDGSProvider
from .tavily import TavilyProvider


class ProviderRouter:
    """Ordered chains per capability. A link is skipped on fallback-eligible
    errors; auth failures are logged loudly (the user's key is misconfigured);
    non-eligible errors abort the chain immediately."""

    def __init__(self) -> None:
        self._providers: Dict[Capability, List[Provider]] = {
            Capability.SEARCH: [AnySearchProvider(), TavilyProvider(), DDGSProvider()],
            Capability.FETCH: [AnySearchProvider(), TavilyProvider(), DDGSProvider()],
        }

    def chain(self, capability: Capability) -> List[Provider]:
        return list(self._providers[capability])

    def search(self, query: str, limit: int, *, deadline: Optional[float] = None) -> SearchResult:
        if not query or not query.strip():
            raise InvalidInputError("query must be a non-empty string")
        return self._walk(Capability.SEARCH, query=query, limit=limit, deadline=deadline)

    def fetch(self, url: str, *, deadline: Optional[float] = None) -> FetchResult:
        url = (url or "").strip()
        if not url:
            raise InvalidInputError("url must be a non-empty string")
        if not url.lower().startswith(("http://", "https://")):
            raise InvalidInputError(f"unsupported URL scheme: {url[:50]}")
        return self._walk(Capability.FETCH, url=url, deadline=deadline)

    def _walk(self, capability: Capability, *, deadline: Optional[float], **kwargs) -> Any:
        if deadline is None:
            deadline = time.monotonic() + config.chain_deadline()
        errors: List[str] = []
        for provider in self.chain(capability):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                errors.append(f"chain deadline ({config.chain_deadline():.0f}s) reached before {provider.NAME}")
                break
            # Hard upper bound: each attempt gets min(provider timeout, remaining),
            # so one slow provider can never push the walk past the deadline.
            kwargs["timeout_override"] = remaining
            try:
                if capability == Capability.SEARCH:
                    return provider.search(kwargs["query"], kwargs["limit"],
                                           timeout_override=remaining)
                return provider.fetch(kwargs["url"], timeout_override=remaining)
            except ProviderError as exc:
                errors.append(f"{provider.NAME}({provider.mode().value}): {str(exc)[:150]}")
                if exc.auth_failure:
                    # Loud, non-suppressible: the user's key is misconfigured.
                    import logging
                    logging.getLogger("universal_web_retrieval").error(
                        "AUTH FAILURE on %s (mode=%s): %s — check the configured API key",
                        provider.NAME, provider.mode().value, str(exc)[:200])
                if not exc.fallback_eligible:
                    break
            except Exception as exc:  # noqa: BLE001 — unknown errors are still chain-eligible
                errors.append(f"{provider.NAME}: {str(exc)[:150]}")
        raise ProviderError("all providers failed: " + " | ".join(errors))
