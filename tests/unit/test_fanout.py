"""Fan-out search mode (v0.3.0): config, concurrency, grace/deadline, RRF merge.

Everything here is hermetic: providers are stubs, no network is touched.
"""
from __future__ import annotations

import json
import logging
import threading
import time

import pytest

from universal_web_retrieval import config, server
from universal_web_retrieval.errors import (AuthMode, Capability, FetchResult,
                                            InvalidInputError, Provider,
                                            ProviderError, SearchResult)
from universal_web_retrieval.fanout import _rank_key, dedupe_key, merge_results
from universal_web_retrieval.providers import router as router_module
from universal_web_retrieval.providers.router import ProviderRouter


# ---------------------------------------------------------------- helpers
class StubProvider(Provider):
    """Real Provider (not a Mock) so KEYED_ONLY/mode checks behave like production."""

    NAME = "stub"
    CAPABILITIES = frozenset({Capability.SEARCH, Capability.FETCH})
    KEYED_ONLY = False
    KIND = "search"
    TIMEOUT_CONFIG = staticmethod(lambda: 5.0)

    def __init__(self, name: str, *, rows=None, delay: float = 0.0, error=None,
                 key: str = "", keyed_only: bool = False):
        self.NAME = name
        self.KEYED_ONLY = keyed_only
        self._rows = rows
        self._delay = delay
        self._error = error
        self._key = key
        self.calls = 0
        self.limits = []
        self.timeouts = []
        self.threads = []

    def api_key(self) -> str:
        return self._key

    def search(self, query, limit, timeout_override=None):
        self.calls += 1
        self.limits.append(limit)
        self.timeouts.append(timeout_override)
        current = threading.current_thread()
        self.threads.append((current.name, current.daemon))
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        rows = self._rows
        if rows is None:
            rows = [{"title": f"{self.NAME} hit", "url": f"https://{self.NAME}.example/x",
                     "snippet": f"snippet from {self.NAME}"}]
        return SearchResult(results=[dict(r) for r in rows], provider=self.NAME,
                            mode=self.mode())

    def fetch(self, url, timeout_override=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return FetchResult(url=url, content="c", provider=self.NAME, mode=self.mode())


def router_with(*providers):
    """Inject an ordered chain (the same hook the existing unit tests use)."""
    r = ProviderRouter(providers={p.NAME: p for p in providers})
    r._providers = {Capability.SEARCH: list(providers), Capability.FETCH: list(providers)}
    return r


class SkewedClock:
    """``time.monotonic`` stand-in whose offset a provider can jump on purpose.

    Lets a test say "this provider's record says it finished 10 s later" without
    sleeping for 10 s and without depending on real scheduling.
    """

    def __init__(self) -> None:
        self.skew = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.skew


def row(url, title="t", snippet="s", **extra):
    out = {"title": title, "url": url, "snippet": snippet}
    out.update(extra)
    return out


# ---------------------------------------------------------------- default mode
def test_default_mode_is_failover_and_unchanged():
    a = StubProvider("a")
    b = StubProvider("b")
    r = router_with(a, b)
    result = r.search("q", 5)
    assert result.provider == "a"
    assert b.calls == 0
    payload = result.to_dict()
    assert payload["attempts"] == [{"provider": "a", "outcome": "ok"}]
    assert "sources" not in payload["results"][0]
    assert set(payload) == {"success", "provider", "mode", "latency_ms", "attempts",
                            "results"}


def test_failover_payload_is_byte_identical_to_pre_fanout_golden():
    """0.3.0 must not change one byte of a failover response.

    The golden dict is built exactly the way ``SearchResult.to_dict`` built it in
    0.2.3: fixed key order, extras only when non-empty, no ``sources``.
    """
    a = StubProvider("a", rows=[
        {"title": "T", "url": "https://example.com/x", "snippet": "S",
         "published": "2026-01-02", "site": "Example", "score": 0.5},
        {"title": "U", "url": "https://example.com/y", "snippet": "V",
         "published": "", "site": None, "score": None},
    ])
    result = router_with(a, StubProvider("b")).search("q", 5)
    result.latency_ms = 12                        # fixed, so the payload is comparable
    golden = {
        "success": True, "provider": "a", "mode": "keyless", "latency_ms": 12,
        "attempts": [{"provider": "a", "outcome": "ok"}],
        "results": [
            {"title": "T", "url": "https://example.com/x", "snippet": "S",
             "published": "2026-01-02", "site": "Example", "score": 0.5},
            {"title": "U", "url": "https://example.com/y", "snippet": "V"},
        ],
    }
    payload = result.to_dict()
    assert payload == golden
    # Key order matters too: JSON consumers see the serialised bytes.
    assert json.dumps(payload) == json.dumps(golden)


def test_config_defaults():
    assert config.search_mode() == "failover"
    assert config.fanout_providers() == 3
    assert config.fanout_grace() == 5.0


def test_env_mode_switches_to_fanout(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    a, b = StubProvider("a"), StubProvider("b")
    r = router_with(a, b)
    result = r.search("q", 5)
    assert result.provider == "fanout"
    assert a.calls == b.calls == 1


def test_per_call_mode_overrides_env(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    a, b = StubProvider("a"), StubProvider("b")
    r = router_with(a, b)
    assert r.search("q", 5, mode="failover").provider == "a"
    assert b.calls == 0

    monkeypatch.delenv("UWR_SEARCH_MODE")
    a2, b2 = StubProvider("a2"), StubProvider("b2")
    r2 = router_with(a2, b2)
    assert r2.search("q", 5, mode="FANOUT").provider == "fanout"
    assert a2.calls == b2.calls == 1


def test_invalid_per_call_mode_is_an_input_error():
    r = router_with(StubProvider("a"))
    with pytest.raises(InvalidInputError) as cm:
        r.search("q", 5, mode="sideways")
    assert "mode must be one of" in str(cm.value)


def test_unknown_env_mode_warns_once_then_fails_over(monkeypatch, caplog):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fannout")   # typo
    a, b = StubProvider("a"), StubProvider("b")
    r = router_with(a, b)
    with caplog.at_level(logging.WARNING, logger="universal_web_retrieval.config"):
        assert r.search("q", 5).provider == "a"
        assert r.search("q", 5).provider == "a"
    warnings = [rec for rec in caplog.records if "fannout" in rec.getMessage()]
    assert len(warnings) == 1, [rec.getMessage() for rec in caplog.records]
    assert b.calls == 0


# ---------------------------------------------------------------- N / eligibility
def test_n_clamps_to_chain_length(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_SEARCH_FANOUT", "10")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    providers = [StubProvider(f"p{i}") for i in range(3)]
    r = router_with(*providers)
    assert r.search("q", 5).provider == "fanout"
    assert [p.calls for p in providers] == [1, 1, 1]


def test_n_limits_providers_queried(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_SEARCH_FANOUT", "2")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    providers = [StubProvider(f"p{i}") for i in range(4)]
    r = router_with(*providers)
    result = r.search("q", 5)
    assert [p.calls for p in providers] == [1, 1, 0, 0]
    assert [a for a in result.attempts] == [
        {"provider": "p0", "outcome": "ok", "results": 1,
         "latency_ms": result.attempts[0]["latency_ms"]},
        {"provider": "p1", "outcome": "ok", "results": 1,
         "latency_ms": result.attempts[1]["latency_ms"]},
        {"provider": "p2", "outcome": "skipped:fanout"},
        {"provider": "p3", "outcome": "skipped:fanout"},
    ]


def test_not_configured_skips_but_does_not_consume_a_slot(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_SEARCH_FANOUT", "2")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    missing = StubProvider("missing", keyed_only=True, key="")
    a, b = StubProvider("a"), StubProvider("b")
    r = router_with(missing, a, b)
    result = r.search("q", 5)
    assert missing.calls == 0
    assert a.calls == b.calls == 1
    assert result.attempts[0] == {"provider": "missing",
                                  "outcome": "skipped:not_configured"}
    assert [x["provider"] for x in result.attempts] == ["missing", "a", "b"]


def test_keyed_only_with_key_does_consume_a_slot(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_SEARCH_FANOUT", "1")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    keyed = StubProvider("keyed", keyed_only=True, key="k")
    later = StubProvider("later")
    r = router_with(keyed, later)
    assert r.search("q", 5).provider == "fanout"
    assert keyed.calls == 1 and later.calls == 0


def test_only_not_configured_providers_still_fails_with_attempts(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    missing1 = StubProvider("m1", keyed_only=True, key="")
    missing2 = StubProvider("m2", keyed_only=True, key="")
    r = router_with(missing1, missing2)
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5)
    assert "all providers failed" in str(cm.value)
    assert [a["outcome"] for a in cm.value.attempts] == [
        "skipped:not_configured", "skipped:not_configured"]


# ---------------------------------------------------------------- concurrency
def test_three_providers_run_concurrently(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    monkeypatch.setenv("UWR_SEARCH_FANOUT", "3")
    providers = [StubProvider(f"p{i}", delay=0.4) for i in range(3)]
    r = router_with(*providers)
    started = time.monotonic()
    result = r.search("q", 5)
    elapsed = time.monotonic() - started
    assert result.provider == "fanout"
    assert all(p.calls == 1 for p in providers)
    # Sequential would be 1.2 s; parallel is ~0.4 s. The 1.0 s bound leaves >0.5 s
    # of slack for slow CI scheduling while still failing a sequential run.
    assert elapsed < 1.0, f"fan-out was sequential: {elapsed:.2f}s for 3×0.4s"


def test_each_provider_gets_remaining_deadline_as_timeout(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0")
    a, b = StubProvider("a"), StubProvider("b")
    r = router_with(a, b)
    r.search("q", 5, deadline=time.monotonic() + 10)
    for provider in (a, b):
        assert provider.timeouts[0] is not None
        assert 0 < provider.timeouts[0] <= 10


# ---------------------------------------------------------------- grace
def test_grace_cutoff_marks_stragglers_skipped(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0.15")
    fast = StubProvider("fast", delay=0.05)
    slow = StubProvider("slow", delay=1.0)
    r = router_with(fast, slow)
    started = time.monotonic()
    result = r.search("q", 5)
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"grace period was not honoured: {elapsed:.2f}s"
    assert result.attempts == [
        {"provider": "fast", "outcome": "ok", "results": 1,
         "latency_ms": result.attempts[0]["latency_ms"]},
        {"provider": "slow", "outcome": "skipped:grace"},
    ]
    assert all(r["sources"] == ["fast"] for r in result.results)


def test_grace_zero_waits_for_everyone(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0")
    fast = StubProvider("fast", delay=0.02)
    slow = StubProvider("slow", delay=0.3)
    r = router_with(fast, slow)
    started = time.monotonic()
    result = r.search("q", 5)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.25
    assert [a["outcome"] for a in result.attempts] == ["ok", "ok"]
    assert {source for r_ in result.results for source in r_["sources"]} == {"fast", "slow"}


def test_a_record_finishing_after_the_window_is_not_merged(monkeypatch):
    """The cut-off is a finish time, not "had the collector looked yet".

    A record that reaches the queue just after ``first_ok_at + grace`` can ride
    along with an in-window record into the next drain. It must still be
    ``skipped:grace``: the window closed before it finished.
    """
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0.2")
    clock = SkewedClock()
    monkeypatch.setattr(router_module, "time", clock)

    class LateProvider(StubProvider):
        def search(self, query, limit, timeout_override=None):
            result = super().search(query, limit, timeout_override)
            clock.skew += 10.0     # its record claims to finish 10 s from now
            return result

    prompt = StubProvider("prompt", delay=0.005)
    late = LateProvider("late", delay=0.05)
    result = router_with(prompt, late).search("q", 5)
    assert [a["outcome"] for a in result.attempts] == ["ok", "skipped:grace"]
    assert [r_["sources"] for r_ in result.results] == [["prompt"]]


def test_late_error_keeps_its_diagnostic_in_attempts(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "0.2")
    clock = SkewedClock()
    monkeypatch.setattr(router_module, "time", clock)

    class LateErrorProvider(StubProvider):
        def search(self, query, limit, timeout_override=None):
            time.sleep(0.05)
            clock.skew += 10.0
            raise ProviderError("late diagnostic")

    prompt = StubProvider("prompt", delay=0.005)
    late_error = LateErrorProvider("late_error")
    result = router_with(prompt, late_error).search("q", 5)
    assert [attempt["outcome"] for attempt in result.attempts] == [
        "ok", "error:late diagnostic"]
    assert [hit["sources"] for hit in result.results] == [["prompt"]]


# ---------------------------------------------------------------- deadline
def test_deadline_already_passed_skips_without_calling(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    a, b = StubProvider("a"), StubProvider("b")
    r = router_with(a, b)
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5, deadline=time.monotonic() - 1)
    assert a.calls == b.calls == 0
    assert [x["outcome"] for x in cm.value.attempts] == ["skipped:deadline",
                                                         "skipped:deadline"]


def test_deadline_cuts_the_whole_fanout(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "5")
    a = StubProvider("a", delay=1.0)
    b = StubProvider("b", delay=1.0)
    r = router_with(a, b)
    started = time.monotonic()
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5, deadline=time.monotonic() + 0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 0.9, f"deadline not enforced: {elapsed:.2f}s"
    assert [x["outcome"] for x in cm.value.attempts] == ["skipped:deadline",
                                                         "skipped:deadline"]
    assert "all providers failed" in str(cm.value)


# ---------------------------------------------------------------- failures
def test_one_provider_failing_does_not_lose_the_others(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    bad = StubProvider("bad", error=ProviderError("HTTP 429 rate limited"))
    good = StubProvider("good")
    r = router_with(bad, good)
    result = r.search("q", 5)
    assert result.provider == "fanout"
    assert result.attempts[0]["provider"] == "bad"
    assert result.attempts[0]["outcome"].startswith("error:")
    assert result.attempts[1]["outcome"] == "ok"
    assert result.results[0]["url"] == "https://good.example/x"


def test_all_fail_raises_with_attempts(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    a = StubProvider("a", error=ProviderError("boom"))
    b = StubProvider("b", error=ProviderError("bang"))
    r = router_with(a, b)
    with pytest.raises(ProviderError) as cm:
        r.search("q", 5)
    assert "all providers failed" in str(cm.value)
    assert "boom" in str(cm.value) and "bang" in str(cm.value)
    assert [x["provider"] for x in cm.value.attempts] == ["a", "b"]
    assert all(x["outcome"].startswith("error:") for x in cm.value.attempts)


def test_an_empty_provider_contributes_nothing_but_does_not_fail(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    empty = StubProvider("empty", rows=[])
    good = StubProvider("good")
    r = router_with(empty, good)
    result = r.search("q", 5)
    assert result.attempts[0] == {"provider": "empty", "outcome": "ok", "results": 0,
                                  "latency_ms": result.attempts[0]["latency_ms"]}
    assert [r_["sources"] for r_ in result.results] == [["good"]]


def test_all_empty_answers_are_a_success_with_no_rows(monkeypatch):
    """Same rule as failover: at least one OK answer — not one hit — is success."""
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    first = StubProvider("first", rows=[])
    second = StubProvider("second", rows=[])
    r = router_with(first, second)
    result = r.search("q", 5)
    assert result.provider == "fanout"
    assert result.results == []
    assert result.mode is AuthMode.KEYLESS
    assert result.attempts == [
        {"provider": "first", "outcome": "ok", "results": 0,
         "latency_ms": result.attempts[0]["latency_ms"]},
        {"provider": "second", "outcome": "ok", "results": 0,
         "latency_ms": result.attempts[1]["latency_ms"]},
    ]
    payload = result.to_dict()
    assert payload["success"] is True
    assert payload["results"] == []
    # What failover does with the same query, for comparison.
    failover = router_with(StubProvider("solo", rows=[])).search("q", 5, mode="failover")
    assert failover.results == [] and failover.provider == "solo"


def test_all_empty_answers_still_report_mixed_modes(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    r = router_with(StubProvider("keyed", rows=[], key="k"),
                    StubProvider("keyless", rows=[]))
    result = r.search("q", 5)
    assert result.results == []
    assert result.mode is AuthMode.MIXED


def test_an_empty_answer_survives_the_other_providers_failing(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    r = router_with(StubProvider("bad", error=ProviderError("boom")),
                    StubProvider("empty", rows=[]))
    result = r.search("q", 5)
    assert result.results == []
    assert result.attempts[0]["outcome"].startswith("error:")
    assert result.attempts[1]["outcome"] == "ok"


def test_non_fallback_eligible_error_does_not_abort_the_fanout(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    fatal = StubProvider("fatal", error=ProviderError("bad request",
                                                      fallback_eligible=False))
    good = StubProvider("good")
    r = router_with(fatal, good)
    result = r.search("q", 5)
    assert fatal.calls == 1 and good.calls == 1
    assert result.attempts[0]["outcome"].startswith("error:bad request")
    assert result.results[0]["sources"] == ["good"]


def test_auth_failure_is_still_loud(monkeypatch, caplog):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    bad = StubProvider("bad", error=ProviderError("HTTP 401: bad key",
                                                  auth_failure=True))
    good = StubProvider("good")
    r = router_with(bad, good)
    with caplog.at_level(logging.ERROR, logger="universal_web_retrieval.router"):
        r.search("q", 5)
    assert any("AUTH FAILURE on bad" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------- dedupe
@pytest.mark.parametrize("variant", [
    "https://www.example.com/docs/?utm_source=news&b=2&a=1#intro",
    "https://EXAMPLE.com/docs?a=1&b=2",
    "https://example.com/docs/?b=2&a=1&gclid=xyz",
    "https://example.com/docs?a=1&b=2&ref=hn&spm=abc",
])
def test_dedupe_key_collapses_noise(variant):
    assert dedupe_key(variant) == dedupe_key("https://example.com/docs?a=1&b=2")


def test_dedupe_key_keeps_meaningful_differences():
    assert dedupe_key("https://example.com/a") != dedupe_key("https://example.com/b")
    assert dedupe_key("https://example.com/a?id=1") != dedupe_key("https://example.com/a?id=2")
    assert dedupe_key("https://example.com:8443/a") != dedupe_key("https://example.com/a")
    # Only the default port for the URL's own scheme is noise.
    assert dedupe_key("https://example.com:80/a") != dedupe_key("https://example.com/a")
    assert dedupe_key("https://example.com/") == "//example.com/"
    assert dedupe_key("") == ""


@pytest.mark.parametrize(("left", "right"), [
    ("http://example.com/a", "https://example.com/a"),
    ("http://www.example.com/a/", "https://example.com/a"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("http://example.com:80/a", "http://example.com/a"),
    ("ftp://example.com/a", "https://example.com/a"),
    ("//example.com/a", "https://example.com/a"),
    ("example.com/a", "https://example.com/a"),
    ("example.com?x=1", "https://example.com/?x=1"),
    ("HTTPS://X:443/a", "https://x/a"),
])
def test_dedupe_key_ignores_scheme_and_default_ports(left, right):
    assert dedupe_key(left) == dedupe_key(right)


def test_merge_collapses_http_and_https_into_one_row():
    merged = merge_results([
        ("a", [row("http://example.com/x", title="plain", snippet="short")]),
        ("b", [row("https://example.com/x", title="tls", snippet="a longer snippet")]),
    ], limit=5)
    assert len(merged) == 1
    assert merged[0]["sources"] == ["a", "b"]
    # The output URL is still the best-ranked occurrence's original one.
    assert merged[0]["url"] == "http://example.com/x"
    assert merged[0]["title"] == "plain"
    assert merged[0]["snippet"] == "a longer snippet"


@pytest.mark.parametrize(("left", "right"), [
    ("https://bücher.example/page", "https://xn--bcher-kva.example/page"),
    ("https://www.example.com:8443/page", "https://example.com:8443/page"),
])
def test_dedupe_key_normalizes_idn_and_www_with_port(left, right):
    assert dedupe_key(left) == dedupe_key(right)


def test_dedupe_key_preserves_distinct_ports_and_bad_authorities():
    assert dedupe_key("https://example.com:8443/x") != dedupe_key("https://example.com:9443/x")
    assert dedupe_key("https://www.example.com:8443/x") == dedupe_key("https://example.com:8443/x")
    # Invalid port syntax must not collapse to the default-port URL.
    assert dedupe_key("https://example.com:bad/x") != dedupe_key("https://example.com/x")


def test_missing_urls_are_not_merged_into_one_row():
    merged = merge_results([
        ("a", [{"title": "no URL one", "snippet": "x"},
               {"title": "no URL two", "snippet": "y"}]),
    ], limit=5)
    assert merged == []


def test_malformed_rows_from_one_provider_do_not_sink_the_merge():
    bad_rows: list = [None, "junk", row("https://example.com/ok")]
    partly_bad: list = [{"url": None}, row("https://example.com/ok", title="b title")]
    merged = merge_results([("a", bad_rows), ("b", partly_bad)], limit=5)
    assert [r_["url"] for r_ in merged] == ["https://example.com/ok"]
    assert merged[0]["sources"] == ["a", "b"]


def test_duplicate_urls_within_one_provider_count_best_rank_once():
    merged = merge_results([
        ("a", [row("https://example.com/x", title="best", snippet="longest"),
               row("https://www.example.com/x#fragment", title="duplicate", snippet="s")]),
    ], limit=5)
    assert len(merged) == 1
    assert merged[0]["sources"] == ["a"]
    assert merged[0]["title"] == "best"
    assert merged[0]["snippet"] == "longest"


def test_fanout_runs_each_provider_on_its_own_daemon_thread(monkeypatch):
    """Abandoned stragglers must be daemons: they can outlive the collector."""
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    providers = [StubProvider("a"), StubProvider("b")]
    result = router_with(*providers).search("q", 5)
    assert result.provider == "fanout"
    for provider in providers:
        assert provider.threads, provider.NAME
        name, daemon = provider.threads[0]
        assert daemon is True
        assert name == f"uwr-fanout-{provider.NAME}"


def test_fanout_uses_only_public_concurrency_apis():
    """No private ``concurrent.futures`` internals: they vary between versions."""
    import ast
    import inspect
    from universal_web_retrieval.providers import router

    tree = ast.parse(inspect.getsource(router))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name.startswith("concurrent") for name in imported), imported
    assert "_adjust_thread_count" not in inspect.getsource(router)


def test_merge_dedupes_across_url_variants(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    a = StubProvider("a", rows=[row("https://www.example.com/docs/?utm_source=news",
                                    title="A title", snippet="short")])
    b = StubProvider("b", rows=[row("https://example.com/docs#frag",
                                    title="B title", snippet="a much longer snippet")])
    r = router_with(a, b)
    result = r.search("q", 5)
    assert len(result.results) == 1
    only = result.results[0]
    assert only["sources"] == ["a", "b"]
    # Original URL + title of the best-ranked (rank 1, chain-first) occurrence.
    assert only["url"] == "https://www.example.com/docs/?utm_source=news"
    assert only["title"] == "A title"
    # Longest snippet wins regardless of which occurrence it came from.
    assert only["snippet"] == "a much longer snippet"


def test_snippet_title_and_extra_field_merge():
    merged = merge_results([
        ("a", [row("https://example.com/x", title="first", snippet="",
                   published="", site="SiteA")]),
        ("b", [row("https://example.com/x", title="second", snippet="longer text",
                   published="2026-01-01", site="SiteB")]),
    ], limit=5)
    assert len(merged) == 1
    assert merged[0]["title"] == "first"
    assert merged[0]["snippet"] == "longer text"
    assert merged[0]["published"] == "2026-01-01"   # first non-empty, chain order
    assert merged[0]["site"] == "SiteA"


def test_sources_follow_chain_order_not_rank_order():
    merged = merge_results([
        ("a", [row("https://example.com/y"), row("https://example.com/x")]),
        ("b", [row("https://example.com/x")]),
        ("c", [row("https://example.com/x")]),
    ], limit=5)
    by_url = {r_["url"]: r_ for r_ in merged}
    assert by_url["https://example.com/x"]["sources"] == ["a", "b", "c"]


# ---------------------------------------------------------------- ranking
def test_rrf_multi_source_boost_beats_single_source():
    merged = merge_results([
        ("a", [row("https://example.com/x", title="x"), row("https://example.com/y")]),
        ("b", [row("https://example.com/y"), row("https://example.com/z")]),
    ], limit=5)
    assert [r_["url"] for r_ in merged] == ["https://example.com/y",
                                            "https://example.com/x",
                                            "https://example.com/z"]
    assert merged[0]["sources"] == ["a", "b"]


def test_rrf_scores_use_one_term_per_provider():
    # Duplicate hits inside ONE provider must not look like two providers:
    # x is listed twice by "a", so it scores 1/(60+2) only — one term, at its
    # best rank. A naive per-occurrence sum would put x first instead of third.
    merged = merge_results([
        ("a", [row("https://example.com/p"), row("https://example.com/x"),
               row("https://www.example.com/x/")]),
        ("b", [row("https://example.com/q")]),
    ], limit=5)
    assert [r_["url"] for r_ in merged] == ["https://example.com/p",
                                            "https://example.com/q",
                                            "https://example.com/x"]
    assert merged[2]["sources"] == ["a"]


def test_tie_break_three_is_chain_order():
    merged = merge_results([
        ("a", [row("https://example.com/x", title="from a")]),
        ("b", [row("https://example.com/y", title="from b")]),
    ], limit=5)
    assert [r_["title"] for r_ in merged] == ["from a", "from b"]


def test_tie_break_key_prefers_sources_then_best_rank():
    base = {"score": 1.0 / 61, "best_rank": 1, "best_chain": 0}
    more_sources = dict(base, sources=2)
    fewer_sources = dict(base, sources=1)
    assert _rank_key(more_sources) < _rank_key(fewer_sources)

    same_score = dict(base, sources=2)
    better_rank = dict(same_score, best_rank=1, best_chain=2)
    worse_rank = dict(same_score, best_rank=3, best_chain=0)
    assert _rank_key(better_rank) < _rank_key(worse_rank)


def test_limit_truncates_and_no_overfetch(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    a = StubProvider("a", rows=[row(f"https://a.example/{i}") for i in range(10)])
    b = StubProvider("b", rows=[row(f"https://b.example/{i}") for i in range(10)])
    r = router_with(a, b)
    result = r.search("q", 3)
    assert len(result.results) == 3
    assert a.limits == [3] and b.limits == [3]


# ---------------------------------------------------------------- result shape
def test_mode_is_shared_auth_mode_or_mixed(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")

    both_keyless = router_with(StubProvider("a"), StubProvider("b"))
    assert both_keyless.search("q", 5).mode is AuthMode.KEYLESS

    keyed_a = StubProvider("a", key="k")
    keyed_b = StubProvider("b", key="k")
    assert router_with(keyed_a, keyed_b).search("q", 5).mode is AuthMode.KEYED

    mixed = router_with(StubProvider("a", key="k"), StubProvider("b"))
    assert mixed.search("q", 5).mode is AuthMode.MIXED
    payload = mixed.search("q", 5).to_dict()
    assert payload["mode"] == "mixed"
    assert payload["provider"] == "fanout"


def test_attempts_carry_results_and_latency(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    a = StubProvider("a", rows=[row("https://a.example/1"), row("https://a.example/2")])
    r = router_with(a)
    entry = r.search("q", 5).attempts[0]
    assert entry["provider"] == "a"
    assert entry["outcome"] == "ok"
    assert entry["results"] == 2
    assert isinstance(entry["latency_ms"], int)


def test_server_result_shape_includes_sources(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    shared = row("https://shared.example/page", title="shared")
    r = router_with(StubProvider("a", rows=[shared, row("https://a.example/only")]),
                    StubProvider("b", rows=[shared, row("https://b.example/only")]))
    monkeypatch.setattr(server, "router", r)
    out = json.loads(server.web_search_tool("q", 5))
    assert out["provider"] == "fanout"
    assert out["mode"] in {"keyed", "keyless", "mixed"}
    by_url = {r_["url"]: r_ for r_ in out["results"]}
    assert by_url["https://shared.example/page"]["sources"] == ["a", "b"]
    assert by_url["https://shared.example/page"]["title"] == "shared"
    assert set(by_url["https://a.example/only"]) == {"title", "url", "snippet", "sources"}
    # The shared hit outranks both single-source hits (agreement wins).
    assert out["results"][0]["url"] == "https://shared.example/page"


def test_server_invalid_mode_is_an_input_error(monkeypatch):
    monkeypatch.setattr(server, "router", router_with(StubProvider("a")))
    out = json.loads(server.web_search_tool("q", 5, mode="bogus"))
    assert out["success"] is False
    assert out["error"].startswith("invalid input:")
    assert "attempts" not in out


def test_server_per_call_mode_switch(monkeypatch):
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1")
    r = router_with(StubProvider("a"), StubProvider("b"))
    monkeypatch.setattr(server, "router", r)
    fanout = json.loads(server.web_search_tool("q", 5, mode="fanout"))
    assert fanout["provider"] == "fanout"
    failover = json.loads(server.web_search_tool("q", 5))
    assert failover["provider"] == "a"


# ---------------------------------------------------------------- list_providers
def test_list_providers_reports_search_mode_and_fanout(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    monkeypatch.setenv("UWR_SEARCH_FANOUT", "2")
    monkeypatch.setenv("UWR_FANOUT_GRACE", "1.5")
    payload = json.loads(server.list_providers_tool())
    assert payload["search_mode"] == "fanout"
    assert payload["fanout"] == {"n": 2, "grace_s": 1.5}
    assert payload["chains"]["search"]   # existing fields still present


def test_list_providers_defaults(monkeypatch):
    payload = json.loads(server.list_providers_tool())
    assert payload["search_mode"] == "failover"
    assert payload["fanout"] == {"n": 3, "grace_s": 5.0}


# ---------------------------------------------------------------- fetch unaffected
def test_web_fetch_is_always_failover(monkeypatch):
    monkeypatch.setenv("UWR_SEARCH_MODE", "fanout")
    a = StubProvider("a")
    b = StubProvider("b")
    r = router_with(a, b)
    result = r.fetch("https://example.com")
    assert result.provider == "a"
    assert b.calls == 0

    monkeypatch.setattr(server, "router", r)
    out = json.loads(server.web_fetch_tool(url="https://example.com"))
    assert out[0]["provider"] == "a"
    assert "sources" not in out[0]
    with pytest.raises(TypeError):
        r.fetch("https://example.com", mode="fanout")
