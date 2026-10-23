"""Provider router: capability -> ordered, configurable provider chain.

Two search modes
----------------
``failover`` (default, unchanged since 0.1.x) walks the chain one provider at a
time and returns the first success. ``fanout`` (opt-in, ``UWR_SEARCH_MODE=fanout``
or ``mode="fanout"`` on one call) queries up to ``UWR_SEARCH_FANOUT`` providers
concurrently, waits ``UWR_FANOUT_GRACE`` seconds for stragglers once the first
one answers, and merges what came back with Reciprocal Rank Fusion
(``fanout.py``). Fan-out trades N× provider quota per search for quality;
failover keeps the per-call cost at one provider. ``web_fetch`` is always
failover.

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

Keyed-only providers (Yuanbao, Bocha, DeepSeek, Kimi, Zhipu, Brave) with no key
are skipped as ``skipped:not_configured`` — they never reach the network, so a
missing key can never 401 the chain. AnySearch/Tavily keep their official keyless
behaviour, Jina Reader is genuinely usable keyless (20 RPM) and DDGS has no key
concept, so those are always attempted. Because Jina works without a key it is
deliberately absent from both default chains — it runs only when named.

``deadline`` is an explicit absolute monotonic timestamp (or None). Batch callers
pass ONE shared deadline across all URLs so the overall batch is bounded; single
calls get a fresh deadline per call.
"""
from __future__ import annotations

import logging
import time
import threading
import weakref
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures.thread import _worker
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import config
from ..errors import (AuthMode, Capability, InvalidInputError, Provider, ProviderError,
                      SearchResult, FetchResult)
from ..fanout import merge_results
from . import registry as _registry

log = logging.getLogger("universal_web_retrieval.router")

# (capability, source, dropped) signatures already warned about — one log per chain.
_WARNED: set = set()

FANOUT_PROVIDER_NAME = "fanout"


class _DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor whose abandoned workers cannot hold interpreter exit."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, work_queue=self._work_queue):
            work_queue.put(None)

        if len(self._threads) < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self,
                                      len(self._threads))
            thread = threading.Thread(
                name=thread_name, target=_worker,
                args=(weakref.ref(self, weakref_cb), self._work_queue,
                      self._initializer, self._initargs), daemon=True)
            thread.start()
            self._threads.add(thread)
            # Do not register in concurrent.futures' interpreter-exit join map:
            # daemon workers are deliberately abandoned if they outlive the call.


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _mode_name(mode: Any) -> str:
    """AuthMode.value for real enums, ``str`` for anything a test injected."""
    return getattr(mode, "value", None) or str(mode)


def _search_mode(mode: Optional[str]) -> str:
    """Resolve the mode for one search: per-call argument, then UWR_SEARCH_MODE.

    A per-call value that is not a known mode is an input error (like an
    unusable ``providers`` override) rather than a silent fallback.
    """
    if mode is None:
        return config.search_mode()
    value = str(mode).strip().lower()
    if value not in config.SEARCH_MODES:
        raise InvalidInputError(
            f"mode must be one of {', '.join(config.SEARCH_MODES)}")
    return value


def _fanout_call(provider: Provider, query: str, limit: int,
                 budget: float) -> Dict[str, Any]:
    """Run ONE provider search inside a worker thread. Never raises.

    Each call gets its own ``timeout_override`` and touches no router state, so
    concurrent providers cannot interfere with one another. Providers that keep
    per-instance state (Yuanbao's MCP session id / JSON-RPC counter) guard it
    with their own lock and stay correct under concurrent calls.
    """
    started = time.monotonic()
    try:
        result = provider.search(query, limit, timeout_override=budget)
    except ProviderError as exc:
        return {"outcome": "error", "error": str(exc),
                "auth_failure": bool(getattr(exc, "auth_failure", False)),
                "mode": provider.mode(), "latency_ms": _ms(started),
                "finished_at": time.monotonic()}
    except Exception as exc:  # noqa: BLE001 — one provider's surprise is not the chain's
        return {"outcome": "error", "error": str(exc), "auth_failure": False,
                "mode": provider.mode(), "latency_ms": _ms(started),
                "finished_at": time.monotonic()}
    hits = list(getattr(result, "results", None) or [])
    return {"outcome": "ok", "result": result, "results": len(hits),
            "mode": getattr(result, "mode", None) or provider.mode(),
            "latency_ms": _ms(started), "finished_at": time.monotonic()}



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
        if providers is not None:
            kept, dropped = self._resolve(capability, providers, source="call")
            if not kept:
                requested = ", ".join(dropped) if dropped else "<empty list>"
                raise InvalidInputError(
                    f"none of the requested providers can serve {capability.value}: "
                    f"{requested} — available: "
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
               providers: Optional[Sequence[str]] = None,
               mode: Optional[str] = None) -> SearchResult:
        if not query or not query.strip():
            raise InvalidInputError("query must be a non-empty string")
        if _search_mode(mode) == "fanout":
            return self._fanout(query, limit, deadline=deadline, providers=providers)
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
            if remaining < 0.1:
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

    # ---- fan-out -------------------------------------------------------
    def _fanout(self, query: str, limit: int, *, deadline: Optional[float],
                providers: Optional[Sequence[str]]) -> SearchResult:
        """Query up to N eligible providers concurrently, merge with RRF.

        Bound by ONE deadline: every worker gets ``remaining`` as its timeout
        override, the collector stops waiting ``grace`` seconds after the first
        non-empty answer (``grace == 0`` waits for everyone), and a worker still
        running thereafter is recorded as ``skipped:grace`` / ``skipped:deadline``
        while its eventual result is discarded.
        """
        chain = self.chain(Capability.SEARCH, providers)   # may raise InvalidInputError
        if deadline is None:
            deadline = time.monotonic() + config.chain_deadline()
        started = time.monotonic()
        grace = config.fanout_grace()
        slots = config.fanout_providers()

        # Plan in chain order. not_configured providers are recorded in attempts
        # but never consume a slot, so they cannot hide a usable provider behind
        # the N cutoff.
        layout: List[Tuple[str, Provider]] = []
        planned: List[Provider] = []
        for provider in chain:
            if getattr(provider, "KEYED_ONLY", False) is True and not provider.api_key():
                layout.append(("skipped", provider))
                continue
            if len(planned) >= slots:
                layout.append(("beyond", provider))
                continue
            layout.append(("run", provider))
            planned.append(provider)

        records: Dict[str, Dict[str, Any]] = {}

        def _attempts() -> List[Dict[str, Any]]:
            """Per-provider trail, one entry each, in chain order."""
            out: List[Dict[str, Any]] = []
            for kind, provider in layout:
                name = provider.NAME
                if kind == "skipped":
                    out.append({"provider": name, "outcome": "skipped:not_configured"})
                    continue
                if kind == "beyond":
                    out.append({"provider": name, "outcome": "skipped:fanout"})
                    continue
                record = records.get(name)
                if record is None:
                    out.append({"provider": name, "outcome": "skipped:deadline"})
                    continue
                if record["outcome"] == "ok":
                    out.append({"provider": name, "outcome": "ok",
                                "results": record["results"],
                                "latency_ms": record["latency_ms"]})
                elif record["outcome"].startswith("skipped:"):
                    out.append({"provider": name, "outcome": record["outcome"]})
                else:
                    out.append({"provider": name,
                                "outcome": f"error:{record['error'][:150]}"})
            return out

        def _failure_message() -> str:
            errors: List[str] = []
            for kind, provider in layout:
                name = provider.NAME
                if kind == "skipped":
                    errors.append(f"{name}: skipped (not_configured)")
                    continue
                if kind == "beyond":
                    errors.append(f"{name}: skipped (fanout limit)")
                    continue
                record = records.get(name)
                if record is None:
                    continue
                if record["outcome"] == "ok":
                    if not record["results"]:
                        errors.append(f"{name}: no results")
                elif record["outcome"] == "skipped:grace":
                    errors.append(f"{name}: skipped (grace elapsed before it answered)")
                elif record["outcome"] == "skipped:deadline":
                    errors.append(f"chain deadline ({config.chain_deadline():.0f}s) "
                                  f"reached before {name}")
                else:
                    errors.append(f"{name}({_mode_name(record['mode'])}): "
                                  f"{record['error'][:150]}")
            return "all providers failed: " + " | ".join(errors)

        # Nothing can run: no eligible provider, or the deadline already passed.
        if not planned:
            raise _with_attempts(_failure_message(), _attempts())
        if deadline - time.monotonic() < 0.1:
            for provider in planned:
                records[provider.NAME] = {"outcome": "skipped:deadline"}
            raise _with_attempts(_failure_message(), _attempts())

        futures: Dict[Any, Provider] = {}
        first_ok_at: Optional[float] = None
        stopped_by: Optional[str] = None
        executor = _DaemonThreadPoolExecutor(max_workers=max(1, len(planned)),
                                             thread_name_prefix="uwr-fanout")
        try:
            for provider in planned:
                budget = max(0.05, deadline - time.monotonic())
                futures[executor.submit(_fanout_call, provider, query, limit, budget)] = provider

            pending = set(futures)
            while pending:
                now = time.monotonic()
                if first_ok_at is None or grace <= 0:
                    budget = deadline - now           # nothing to merge yet (or grace=0)
                else:
                    budget = min(deadline, first_ok_at + grace) - now
                if budget <= 0:
                    stopped_by = ("grace" if first_ok_at is not None and grace > 0
                                  and first_ok_at + grace <= deadline else "deadline")
                    break
                done, pending = wait(pending, timeout=budget,
                                     return_when=FIRST_COMPLETED)
                for future in done:
                    provider = futures[future]
                    try:
                        record = future.result()      # worker wrapper normally never raises
                    except BaseException as exc:      # preserve the trail for worker failures
                        record = {"outcome": "error", "error": str(exc),
                                  "auth_failure": False, "mode": provider.mode(),
                                  "latency_ms": 0}
                    records[provider.NAME] = record
                    if record["outcome"] == "ok" and record["results"]:
                        finished = record["finished_at"]
                        if first_ok_at is None or finished < first_ok_at:
                            first_ok_at = finished
                if not done:
                    break
        finally:
            # Never block on a straggler: it may finish in the background, and
            # whatever it returns is discarded.
            executor.shutdown(wait=False, cancel_futures=True)

        for provider in planned:
            if provider.NAME not in records:
                records[provider.NAME] = {
                    "outcome": (f"skipped:{stopped_by or ('grace' if first_ok_at is not None else 'deadline')}")}

        for provider in planned:
            record = records.get(provider.NAME) or {}
            if record.get("auth_failure"):
                # Loud, non-suppressible: the user's key is misconfigured.
                log.error("AUTH FAILURE on %s (mode=%s): %s — check the configured API key",
                          provider.NAME, _mode_name(record.get("mode")),
                          str(record.get("error", ""))[:200])

        contributors = [(provider.NAME, records[provider.NAME]["result"].results,
                         records[provider.NAME]["mode"])
                        for provider in planned
                        if records.get(provider.NAME, {}).get("outcome") == "ok"
                        and records[provider.NAME]["results"]]
        if not contributors:
            raise _with_attempts(_failure_message(), _attempts())

        rows = merge_results([(name, hits) for name, hits, _mode in contributors], limit)

        # One auth mode if the answering providers agree, otherwise "mixed".
        mode_names = sorted({_mode_name(mode) for _name, _hits, mode in contributors})
        if len(mode_names) > 1:
            result_mode = AuthMode.MIXED
        else:
            first_mode = contributors[0][2]
            result_mode = first_mode if isinstance(first_mode, AuthMode) else AuthMode(first_mode)

        return SearchResult(results=rows, provider=FANOUT_PROVIDER_NAME, mode=result_mode,
                            latency_ms=_ms(started), attempts=_attempts())


def _with_attempts(message: str, attempts: List[Dict[str, str]]) -> ProviderError:
    """Attach the attempt trail to a chain-level failure (surfaced by the tools)."""
    err = ProviderError(message)
    err.attempts = attempts
    return err
