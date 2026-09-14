import os, sys, time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from universal_web_retrieval.errors import (
    classify_http_error, ProviderError, AuthMode, Capability)
from universal_web_retrieval.providers.router import ProviderRouter


class TestAuthContextClassification(unittest.TestCase):
    def test_keyed_401_is_auth_failure(self):
        e = classify_http_error(401, "bad key", authenticated=True)
        self.assertTrue(e.auth_failure)
        self.assertTrue(e.fallback_eligible)

    def test_keyed_403_is_auth_failure(self):
        e = classify_http_error(403, "forbidden", authenticated=True)
        self.assertTrue(e.auth_failure)

    def test_keyless_401_is_not_auth_failure(self):
        e = classify_http_error(401, "denied", authenticated=False)
        self.assertFalse(e.auth_failure)
        self.assertTrue(e.fallback_eligible)

    def test_keyless_403_is_not_auth_failure(self):
        e = classify_http_error(403, "denied", authenticated=False)
        self.assertFalse(e.auth_failure)


class TestHardDeadline(unittest.TestCase):
    def _router_with_slow(self, delay):
        p = MagicMock()
        p.NAME = "slow"
        p.mode.return_value = AuthMode.KEYLESS
        def _slow_search(query, limit, timeout_override=None):
            # Simulate a provider that would sleep `delay` if not bounded:
            # assert the router passed a timeout_override <= remaining.
            self.assertIsNotNone(timeout_override)
            self.assertLessEqual(timeout_override, 2.0)  # chain deadline is 2s in test
            time.sleep(min(delay, timeout_override))
            raise ProviderError(f"HTTP 429 (slept {min(delay, timeout_override):.1f}s)")
        p.search.side_effect = _slow_search
        r = ProviderRouter()
        r._providers = {Capability.SEARCH: [p], Capability.FETCH: [p]}
        return r

    def test_deadline_bounds_walk(self):
        with patch('universal_web_retrieval.config.chain_deadline', return_value=2.0):
            r = self._router_with_slow(delay=30)
            start = time.monotonic()
            with self.assertRaises(ProviderError):
                r.search("q", 5)
            elapsed = time.monotonic() - start
            # The slow provider must be cut off near the 2s deadline, not sleep 30s.
            self.assertLess(elapsed, 4.0, f"walk took {elapsed:.1f}s — deadline not enforced")
            self.assertGreater(elapsed, 1.0)

    def test_zero_remaining_skips_provider(self):
        p = MagicMock()
        p.NAME = "never"
        p.mode.return_value = AuthMode.KEYLESS
        r = ProviderRouter()
        r._providers = {Capability.SEARCH: [p], Capability.FETCH: [p]}
        with patch('universal_web_retrieval.config.chain_deadline', return_value=-1.0):
            with self.assertRaises(ProviderError) as cm:
                r.search("q", 5)
            self.assertIn("deadline", str(cm.exception))
        p.search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
