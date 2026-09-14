import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import unittest
from unittest.mock import patch, MagicMock

from universal_web_retrieval.errors import (
    Capability, InvalidInputError, ProviderError, AuthMode)
from universal_web_retrieval.providers.router import ProviderRouter


def _mk_provider(name, mode, search=None, fetch=None, search_exc=None, fetch_exc=None):
    p = MagicMock()
    p.NAME = name
    p.mode.return_value = mode
    if search_exc:
        p.search.side_effect = search_exc
    else:
        p.search.return_value = search
    if fetch_exc:
        p.fetch.side_effect = fetch_exc
    else:
        p.fetch.return_value = fetch
    return p


def _sr(name, mode):
    from universal_web_retrieval.errors import SearchResult
    return SearchResult(results=[{"title": "t", "url": "u", "snippet": "s"}], provider=name, mode=mode)

def _fr(name, mode):
    from universal_web_retrieval.errors import FetchResult
    return FetchResult(url="https://x", content="c", provider=name, mode=mode)


class TestRouter(unittest.TestCase):
    def _router_with(self, *providers):
        r = ProviderRouter()
        r._providers = {Capability.SEARCH: list(providers), Capability.FETCH: list(providers)}
        return r

    def test_first_provider_success(self):
        p1 = _mk_provider("a", AuthMode.KEYED, search=_sr("a", AuthMode.KEYED))
        p2 = _mk_provider("b", AuthMode.KEYED)
        r = self._router_with(p1, p2)
        res = r.search("q", 5)
        self.assertEqual(res.provider, "a")
        p2.search.assert_not_called()

    def test_fallback_on_provider_error(self):
        p1 = _mk_provider("a", AuthMode.KEYED, search_exc=ProviderError("429 rate limited"))
        p2 = _mk_provider("b", AuthMode.KEYLESS, search=_sr("b", AuthMode.KEYLESS))
        r = self._router_with(p1, p2)
        res = r.search("q", 5)
        self.assertEqual(res.provider, "b")

    def test_auth_failure_falls_back_and_is_not_silently_keyless(self):
        # key present but invalid -> keyed auth failure -> NEXT provider (not same-provider keyless)
        p1 = _mk_provider("a", AuthMode.KEYED, search_exc=ProviderError("HTTP 401: bad key", auth_failure=True))
        p2 = _mk_provider("b", AuthMode.KEYLESS, search=_sr("b", AuthMode.KEYLESS))
        r = self._router_with(p1, p2)
        res = r.search("q", 5)
        self.assertEqual(res.provider, "b")
        # provider a called exactly once (no same-provider keyless retry)
        self.assertEqual(p1.search.call_count, 1)

    def test_all_fail(self):
        p1 = _mk_provider("a", AuthMode.KEYED, search_exc=ProviderError("429"))
        p2 = _mk_provider("b", AuthMode.KEYLESS, search_exc=ProviderError("timeout"))
        r = self._router_with(p1, p2)
        with self.assertRaises(ProviderError) as cm:
            r.search("q", 5)
        self.assertIn("all providers failed", str(cm.exception))
        self.assertIn("a", str(cm.exception))
        self.assertIn("b", str(cm.exception))

    def test_invalid_input_no_fallback(self):
        r = self._router_with(_mk_provider("a", AuthMode.KEYED))
        with self.assertRaises(InvalidInputError):
            r.search("", 5)
        with self.assertRaises(InvalidInputError):
            r.search("   ", 5)
        with self.assertRaises(InvalidInputError):
            r.fetch("ftp://example.com")
        with self.assertRaises(InvalidInputError):
            r.fetch("")

    def test_fetch_chain(self):
        p1 = _mk_provider("a", AuthMode.KEYED, fetch_exc=ProviderError("HTTP 500"))
        p2 = _mk_provider("b", AuthMode.KEYLESS, fetch=_fr("b", AuthMode.KEYLESS))
        r = self._router_with(p1, p2)
        res = r.fetch("https://example.com")
        self.assertEqual(res.provider, "b")
        self.assertEqual(res.content, "c")


if __name__ == "__main__":
    unittest.main()
