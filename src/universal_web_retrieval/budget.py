"""One wall-clock budget, shared by every HTTP request a provider makes.

``timeout_override`` from the router is the *remaining chain budget*. Most
providers spend it on a single request, so passing it straight to httpx is
enough. Providers that need several requests (Yuanbao's MCP handshake, its
``notifications/initialized`` POST, ``tools/call`` and a possible re-handshake +
retry) used to give each one the full configured timeout, so a 20 s budget could
take five times that.

``TimeBudget`` is the fix: the provider creates one budget for the call and asks
it for every sub-request's timeout.

What this does **not** do
-------------------------
* It is not a wall-clock cap on a single response. httpx applies a timeout per
  operation (connect / read / write / pool), so a server that dribbles one byte
  per read can outlast it. ``budget.timeout()`` returns a *per-socket* budget.
* It does not cancel anything. A request already in flight cannot be
  interrupted by another thread; the worst case for one sub-request is still its
  own timeout. What the budget guarantees is that **no new request starts** once
  the budget is spent, so a sequence costs at most the sum of the budgets it had
  time for.
* It does not make the router's deadline a hard kill. Fail-over stops *waiting*
  at the deadline and fan-out discards late records by their finish time; the
  abandoned provider thread finishes on its own schedule. That is why fan-out
  judges records by ``finished_at`` rather than by arrival order.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Union

from .errors import ProviderError

TimeoutSpec = Union[float, tuple, None]

# A request needs at least this much budget to be worth starting: a handshake
# that has 40 ms left will fail anyway, and failing locally is faster and more
# honest than a guaranteed network timeout.
MIN_REQUEST_SECONDS = 0.25


class TimeBudget:
    """A countdown shared by the requests of one provider call."""

    def __init__(self, seconds: Optional[float], *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._started = clock()
        try:
            total = float(seconds) if seconds is not None else 0.0
        except (TypeError, ValueError):
            total = 0.0
        self._total = max(0.0, total)

    # ---- state ---------------------------------------------------------
    @property
    def total(self) -> float:
        return self._total

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started)

    def remaining(self) -> float:
        return max(0.0, self._total - self.elapsed())

    def expired(self) -> bool:
        return self.remaining() <= 0.0

    # ---- usage ---------------------------------------------------------
    def timeout(self, configured: TimeoutSpec) -> TimeoutSpec:
        """``configured`` capped by what is left of the budget.

        Tuples are capped element-wise (httpx ``(connect, read, …)``), which is
        what makes the connect phase share the same budget as the read phase.
        """
        left = self.remaining()
        if configured is None:
            return left
        if isinstance(configured, tuple):
            return tuple(min(float(value), left) for value in configured)
        return min(float(configured), left)

    def require(self, what: str) -> None:
        """Raise unless a sub-request ``what`` can still be attempted."""
        left = self.remaining()
        if left < MIN_REQUEST_SECONDS:
            raise ProviderError(
                f"provider deadline exhausted before {what} "
                f"({left:.2f}s of {self._total:.2f}s left)")
