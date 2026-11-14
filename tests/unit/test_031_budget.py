"""0.3.1 — a multi-request provider honours ONE budget, honestly.

What was wrong: Yuanbao makes up to five HTTP requests per search (initialize,
initialized notification, tools/call, and a re-handshake + retry when the MCP
session expired). Every one of them got the *full* configured read timeout, so a
20 s budget could take 100 s. The router's per-attempt cap only decided when the
next provider starts; it could not stop a link already inside its own sequence.

What this module provides, and what it does not:

* ``TimeBudget`` counts down one wall-clock budget for a provider call and gives
  each sub-request ``min(configured, remaining)`` — that is a **per-socket**
  budget (connect/read), not a wall-clock cap, because httpx applies it per
  operation: a response that trickles one byte per read can outlive it.
* No sub-request starts once the budget is spent, so the sequence costs at most
  the sum of the budgets it actually had time for.
* Nothing here claims to *cancel* a request already in flight. A worker thread
  cannot be interrupted from outside; in fail-over the caller simply proceeds
  and the abandoned link finishes or times out on its own, and in fan-out the
  collector judges each record by its own finish time and discards late ones.

All timings here use an injected clock except one real-time test with a
deliberately tiny budget (0.3 s), which never touches the network.
"""
from __future__ import annotations

import time

import pytest

from universal_web_retrieval.budget import TimeBudget
from universal_web_retrieval.errors import ProviderError


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ---------------------------------------------------------------- primitives
def test_budget_counts_down():
    clock = FakeClock()
    b = TimeBudget(1.0, clock=clock)
    assert b.remaining() == pytest.approx(1.0)
    clock.advance(0.4)
    assert b.remaining() == pytest.approx(0.6)
    clock.advance(0.7)
    assert b.remaining() == 0.0        # never negative
    assert b.expired() is True


def test_budget_caps_each_request_by_the_remainder():
    clock = FakeClock()
    b = TimeBudget(30.0, clock=clock)
    assert b.timeout((5.0, 20.0)) == (5.0, 20.0)
    clock.advance(19.0)
    assert b.timeout((5.0, 20.0)) == (5.0, 11.0)      # connect capped too
    clock.advance(11.0)
    assert b.timeout((5.0, 20.0)) == (0.0, 0.0)


def test_budget_handles_scalar_timeouts():
    clock = FakeClock()
    b = TimeBudget(3.0, clock=clock)
    clock.advance(1.0)
    assert b.timeout(15.0) == 2.0


def test_budget_refuses_a_request_it_cannot_afford():
    clock = FakeClock()
    b = TimeBudget(1.0, clock=clock)
    b.require("initialize")
    clock.advance(0.95)
    with pytest.raises(ProviderError) as exc:
        b.require("tools/call")
    assert "deadline" in str(exc.value).lower()
    assert exc.value.fallback_eligible is True


def test_budget_reports_elapsed():
    clock = FakeClock()
    b = TimeBudget(5.0, clock=clock)
    clock.advance(2.0)
    assert b.elapsed() == pytest.approx(2.0)


# ---------------------------------------------------------------- yuanbao
def _yuanbao(monkeypatch, *, budget):
    """Provider whose every HTTP call costs the budget it was granted."""
    from universal_web_retrieval.providers.yuanbao import YuanbaoProvider
    monkeypatch.setenv("WSA_API_KEY", "k")
    provider = YuanbaoProvider()
    seen = []

    class Resp:
        status_code = 200
        headers = {"content-type": "application/json", "mcp-session-id": "s1"}
        text = "{}"

        def json(self):
            return {"jsonrpc": "2.0", "id": 1,
                    "result": {"content": [{"type": "text",
                                            "text": '{"Response": {"Pages": []}}'}],
                               "isError": False}}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen.append(timeout)
        # Simulate a server that uses the whole read budget it was given.
        read = timeout[1] if isinstance(timeout, tuple) else timeout
        time.sleep(min(read, 0.25))
        return Resp()

    monkeypatch.setattr("universal_web_retrieval.providers.yuanbao.httpx.post",
                        fake_post)
    return provider, seen


def test_yuanbao_multi_request_sequence_is_bounded(monkeypatch):
    provider, seen = _yuanbao(monkeypatch, budget=0.5)
    started = time.monotonic()
    outcome = None
    try:
        provider.search("q", 5, timeout_override=0.5)
    except ProviderError as exc:      # honest: it may run out of budget
        outcome = exc
    elapsed = time.monotonic() - started
    assert elapsed < 1.2, f"budget not honoured: {elapsed:.2f}s over {len(seen)} calls"
    # Every sub-request was capped by what was left, not by the 20 s default.
    assert seen, "no request was attempted"
    for timeout in seen:
        read = timeout[1] if isinstance(timeout, tuple) else timeout
        assert read <= 0.5
    assert outcome is None or "deadline" in str(outcome).lower()


def test_yuanbao_does_not_start_a_request_with_an_empty_budget(monkeypatch):
    provider, seen = _yuanbao(monkeypatch, budget=0.3)
    with pytest.raises(ProviderError):
        provider.search("q", 5, timeout_override=0.0)
    assert seen == []        # nothing was attempted at all
