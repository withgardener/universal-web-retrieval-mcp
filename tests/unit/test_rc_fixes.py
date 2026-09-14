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


class TestBatchOverallDeadline(unittest.TestCase):
    """web_fetch batch: ONE overall deadline across all URLs (not per-URL reset)."""

    def test_batch_deadline_shared(self):
        import time as _t
        p = MagicMock()
        p.NAME = "a"
        p.mode.return_value = AuthMode.KEYLESS
        calls = []
        def _fetch(url, timeout_override=None):
            calls.append((url, timeout_override))
            _t.sleep(0.3)  # each URL eats 0.3s of the shared budget
            raise ProviderError("HTTP 429")
        p.fetch.side_effect = _fetch
        # patch the SERVER's module-level router singleton (what web_fetch_tool uses)
        from universal_web_retrieval import server
        r = ProviderRouter()
        r._providers = {Capability.SEARCH: [p], Capability.FETCH: [p]}
        with patch.object(server, 'router', r), \
             patch('universal_web_retrieval.config.chain_deadline', return_value=1.0):
            import json
            out = json.loads(server.web_fetch_tool(urls=[f"https://x/{i}" for i in range(5)]))
        self.assertEqual(len(out), 5)
        # Later URLs must carry deadline-exceeded errors without network attempts
        deadline_hits = [o for o in out if "batch deadline" in o.get("error", "")]
        self.assertTrue(deadline_hits, "expected at least one URL to hit the batch deadline")
        # No URL's timeout_override may exceed the batch deadline
        for url, to in calls:
            self.assertLessEqual(to, 1.0)

    def test_over_five_urls_rejected(self):
        from universal_web_retrieval.server import web_fetch_tool
        import json
        out = json.loads(web_fetch_tool(urls=[f"https://x/{i}" for i in range(6)]))
        self.assertFalse(out.get("success", True))
        self.assertIn("max 5", out["error"])

    def test_batch_dedup_stable(self):
        from universal_web_retrieval.server import web_fetch_tool
        import json
        # duplicate URLs should be deduped (2 unique instead of 4 calls)
        out = json.loads(web_fetch_tool(urls=["https://a", "https://b", "https://a", "https://b"]))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["url"], "https://a")  # first-seen order preserved

    def test_url_secret_redaction(self):
        from universal_web_retrieval.server import _redact_secrets
        text = "failed: https://api.example.com/x?token=abc123&other=keep"
        red = _redact_secrets(text)
        self.assertNotIn("abc123", red)
        self.assertIn("<redacted>", red)
        self.assertIn("other=keep", red)  # non-credential params untouched
