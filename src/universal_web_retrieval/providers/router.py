"""Provider router: capability -> ordered, configurable provider chain.

Chain resolution order
----------------------
1. Per-call ``providers`` (MCP tool argument) — overrides the order for one call.
2. ``UWR_SEARCH_CHAIN`` / ``UWR_FETCH_CHAIN`` — comma-separated names, order is
   the fallback order.
3. Built-in defaults (`anysearch,tavily,ddgs`), which reproduce v0.1.x exactly.

Names that are unknown, lack the capability, or appear in
``UWR_DISABLED_PROVIDERS`` are dropped with ONE warning per distinct chain (the
warning is not repeated on every request). A chain that ends up empty falls back
to the defaults; an explicitly empty/invalid *per-call* override raises
``InvalidInputError`` instead, so "use bocha only" can never silently run the
whole default chain.

Keyed-only providers (Yuanbao, Bocha, DeepSeek) with no key are skipped as
``skipped:not_configured`` — they never reach the network, so a missing key can
never 401 the chain. AnySearch/Tavily keep their official keyless behaviour and
DDGS has no key concept, so all three are always attempted.

``deadline`` is an explicit absolute monotonic timestamp (or None). Batch callers
pass ONE shared deadline across all URLs so the overall batch is bounded; single
calls get a fresh deadline per call.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import config
from ..errors import (Capability, InvalidInputError, Provider, ProviderError,
                      SearchResult, FetchResult)
from . import registry as _registry

log = logging.getLogger("universal_web_retrieval.router")

# (capability, source, dropped) signatures already warned about — one log per chain.
_WARNED: set = set()


class ProviderRouter:
    """Ordered chains per capability. A link is skipped on fallback-eligible
    errors; auth failures are logged loudly (the user's key is misconfigured);
    non-eligible errors abort the chain immediately."""

    def __init__(self, providers: Optional[Dict[str, Provider]] = None) -> None:
        self._registry: Dict[str, Provider] = (dict(providers) if providers is not None
                                               else _registry.build_registry())
        # Optional capability -> list injection point (tests / embedding). When a
        # capability is present here it wins over env chains and the registry.
        self._providers: Dict[Capability, List[Provider]] = {}

    # ---- chain resolution ---------------------------------------------
    @staticmethod
    def _default_spec(capability: Capability) -> Sequence[str]:
        return (config.DEFAULT_SEARCH_CHAIN if capability == Capability.SEARCH
                else config.DEFAULT_FETCH_CHAIN)

    @staticmethod
    def _env_spec(capability: Capability) -> List[str]:
        return (config.search_chain() if capability == Capability.SEARCH
                else config.fetch_chain())

    def _resolve(self, capability: Capability, names: Sequence[str],
                 *, source: str) -> Tuple[List[Provider], List[str]]:
        """Map names to providers, dropping+reporting anything unusable."""
        disabled = config.disabled_providers()
        kept: List[Provider] = []
        dropped: List[str] = []
        seen = set()
        for raw in names:
            name = str(raw).strip().lower()
            if not name:
                continue
            provider = self._registry.get(name)
            if provider is None:
                dropped.append(f"{name} (unknown provider)")
            elif not provider.supports(capability):
                dropped.append(f"{name} (no {capability.value} capability)")
            elif name in disabled:
                dropped.append(f"{name} (disabled by UWR_DISABLED_PROVIDERS)")
            elif name in seen:
                continue
            else:
                seen.add(name)
                kept.append(provider)
        if dropped:
            signature = (capability.value, source, tuple(dropped))
            if signature not in _WARNED:
                _WARNED.add(signature)
                log.warning("chain[%s] ignoring %s", capability.value, ", ".join(dropped))
        return kept, dropped

    def chain(self, capability: Capability, providers: Optional[Sequence[str]] = None) -> List[Provider]:
        """Resolve the ordered provider list for one capability."""
        if providers:
            kept, dropped = self._resolve(capability, providers, source="call")
            if not kept:
                raise InvalidInputError(
                    f"none of the requested providers can serve {capability.value}: "
                    f"{', '.join(dropped)} — available: "
                    f"{', '.join(_registry.providers_for(capability))}")
            return kept
        injected = self._providers.get(capability)
        if injected:
            return list(injected)
        spec = self._env_spec(capability)
        source = "env"
        if not spec:
            spec = list(self._default_spec(capability))
            source = "default"
        kept, _dropped = self._resolve(capability, spec, source=source)
        if not kept:
            # Config amounted to nothing usable: fall back to the built-in chain.
            kept, _ = self._resolve(capability, list(self._default_spec(capability)),
                                    source="fallback-default")
        return kept

    def resolve_names(self, capability: Capability,
                      providers: Optional[Sequence[str]] = None) -> List[str]:
        return [p.NAME for p in self.chain(capability, providers)]

    # ---- capabilities --------------------------------------------------
    def search(self, query: str, limit: int, *, deadline: Optional[float] = None,
               providers: Optional[Sequence[str]] = None) -> SearchResult:
        if not query or not query.strip():
            raise InvalidInputError("query must be a non-empty string")
        return self._walk(Capability.SEARCH, query=query, limit=limit,
                          deadline=deadline, providers=providers)

    def fetch(self, url: str, *, deadline: Optional[float] = None,
              providers: Optional[Sequence[str]] = None) -> FetchResult:
        url = (url or "").strip()
        if not url:
            raise InvalidInputError("url must be a non-empty string")
        if not url.lower().startswith(("http://", "https://")):
            raise InvalidInputError(f"unsupported URL scheme: {url[:50]}")
        return self._walk(Capability.FETCH, url=url, deadline=deadline,
                          providers=providers)

    # ---- execution -----------------------------------------------------
    def _walk(self, capability: Capability, *, deadline: Optional[float],
              providers: Optional[Sequence[str]] = None, **kwargs) -> Any:
        chain = self.chain(capability, providers)   # may raise InvalidInputError
        if deadline is None:
            deadline = time.monotonic() + config.chain_deadline()
        attempts: List[Dict[str, str]] = []
        errors: List[str] = []

        def _skip(name: str, reason: str) -> None:
            attempts.append({"provider": name, "outcome": f"skipped:{reason}"})

        for provider in chain:
            name = provider.NAME
            # Keyed-only providers without a key are skipped before any network
            # call (the `is True` keeps third-party/injected providers working).
            if getattr(provider, "KEYED_ONLY", False) is True and not provider.api_key():
                _skip(name, "not_configured")
                errors.append(f"{name}: skipped (not_configured)")
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _skip(name, "deadline")
                errors.append(f"chain deadline ({config.chain_deadline():.0f}s) "
                              f"reached before {name}")
                break
            # Hard upper bound: each attempt gets min(provider timeout, remaining),
            # so one slow provider can never push the walk past the deadline.
            try:
                if capability == Capability.SEARCH:
                    result = provider.search(kwargs["query"], kwargs["limit"],
                                             timeout_override=remaining)
                else:
                    result = provider.fetch(kwargs["url"], timeout_override=remaining)
                result.attempts = attempts + [{"provider": name, "outcome": "ok"}]
                return result
            except ProviderError as exc:
                attempts.append({"provider": name, "outcome": f"error:{str(exc)[:150]}"})
                errors.append(f"{name}({provider.mode().value}): {str(exc)[:150]}")
                if exc.auth_failure:
                    # Loud, non-suppressible: the user's key is misconfigured.
                    log.error("AUTH FAILURE on %s (mode=%s): %s — check the configured API key",
                              name, provider.mode().value, str(exc)[:200])
                if not exc.fallback_eligible:
                    break
            except Exception as exc:  # noqa: BLE001 — unknown errors are still chain-eligible
                attempts.append({"provider": name, "outcome": f"error:{str(exc)[:150]}"})
                errors.append(f"{name}: {str(exc)[:150]}")
        raise _with_attempts("all providers failed: " + " | ".join(errors), attempts)


def _with_attempts(message: str, attempts: List[Dict[str, str]]) -> ProviderError:
    """Attach the attempt trail to a chain-level failure (surfaced by the tools)."""
    err = ProviderError(message)
    err.attempts = attempts
    return err
