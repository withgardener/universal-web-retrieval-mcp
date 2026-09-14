"""Offline provider parser tests — no network. Prevents parser bugs from being
silently hidden by fallback."""
import os, sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from universal_web_retrieval.errors import ProviderError, AuthMode
from universal_web_retrieval.providers.anysearch import AnySearchProvider
from universal_web_retrieval.providers.tavily import TavilyProvider
from universal_web_retrieval.providers.ddgs import DDGSProvider


class TestAnySearchParsing(unittest.TestCase):
    def setUp(self):
        self.p = AnySearchProvider()

    def _mock_response(self, payload, status=200):
        m = MagicMock()
        m.status_code = status
        m.json.return_value = payload
        return m

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_code_zero_success(self, mock_post):
        mock_post.return_value = self._mock_response({
            "code": 0, "data": {"results": [
                {"title": "T", "url": "https://x", "snippet": "s"},
                {"title": "T2", "url": "https://y", "content": "c"}]}})
        r = self.p.search("q", 5)
        self.assertEqual(len(r.results), 2)
        self.assertEqual(r.results[0]["title"], "T")
        self.assertEqual(r.results[0]["snippet"], "s")  # snippet preferred

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_code_nonzero(self, mock_post):
        mock_post.return_value = self._mock_response({"code": -1, "message": "Invalid API key."})
        with self.assertRaises(ProviderError):
            self.p.search("q", 5)

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_empty_results(self, mock_post):
        mock_post.return_value = self._mock_response({"code": 0, "data": {"results": []}})
        with self.assertRaises(ProviderError):
            self.p.search("q", 5)

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_malformed_payload(self, mock_post):
        mock_post.return_value = self._mock_response({"unexpected": "shape"})
        with self.assertRaises((ProviderError, KeyError, TypeError)):
            self.p.search("q", 5)

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_keyed_http_failure_is_auth(self, mock_post):
        with patch.object(self.p, 'api_key', return_value='as_sk_test'):
            mock_post.return_value = self._mock_response({"error": "denied"}, status=401)
            with self.assertRaises(ProviderError) as cm:
                self.p.search("q", 5)
            self.assertTrue(cm.exception.auth_failure)

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_keyless_http_failure_not_auth(self, mock_post):
        mock_post.return_value = self._mock_response("nope", status=403)
        with self.assertRaises(ProviderError) as cm:
            self.p.search("q", 5)
        self.assertFalse(cm.exception.auth_failure)

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = self._mock_response({
            "code": 0, "data": {"url": "https://x", "title": "T", "content": "# body"}})
        r = self.p.fetch("https://x")
        self.assertEqual(r.content, "# body")
        self.assertEqual(r.title, "T")

    @patch('universal_web_retrieval.providers.anysearch.httpx.post')
    def test_extract_empty_content(self, mock_post):
        mock_post.return_value = self._mock_response({"code": 0, "data": {"url": "https://x", "content": ""}})
        with self.assertRaises(ProviderError):
            self.p.fetch("https://x")


class TestTavilyParsing(unittest.TestCase):
    def setUp(self):
        self.p = TavilyProvider()

    def _mock_response(self, payload, status=200):
        m = MagicMock()
        m.status_code = status
        m.json.return_value = payload
        return m

    @patch('universal_web_retrieval.providers.tavily.httpx.post')
    def test_search_success(self, mock_post):
        mock_post.return_value = self._mock_response({"results": [
            {"title": "T", "url": "https://x", "content": "c"}]})
        r = self.p.search("q", 5)
        self.assertEqual(len(r.results), 1)

    @patch('universal_web_retrieval.providers.tavily.httpx.post')
    def test_extract_success(self, mock_post):
        mock_post.return_value = self._mock_response({"results": [
            {"url": "https://x", "title": "T", "raw_content": "body"}]})
        r = self.p.fetch("https://x")
        self.assertEqual(r.content, "body")

    @patch('universal_web_retrieval.providers.tavily.httpx.post')
    def test_extract_failed_results(self, mock_post):
        mock_post.return_value = self._mock_response({
            "results": [], "failed_results": [{"url": "https://x", "error": "boom"}]})
        with self.assertRaises(ProviderError) as cm:
            self.p.fetch("https://x")
        self.assertIn("boom", str(cm.exception))

    @patch('universal_web_retrieval.providers.tavily.httpx.post')
    def test_extract_empty_response(self, mock_post):
        mock_post.return_value = self._mock_response({})
        with self.assertRaises(ProviderError):
            self.p.fetch("https://x")

    @patch('universal_web_retrieval.providers.tavily.httpx.post')
    def test_malformed_response(self, mock_post):
        mock_post.return_value = self._mock_response({"weird": 1})
        with self.assertRaises((ProviderError, KeyError, TypeError, AttributeError)):
            self.p.search("q", 5)

    @patch('universal_web_retrieval.providers.tavily.httpx.post')
    def test_keyless_403_not_auth_failure(self, mock_post):
        mock_post.return_value = self._mock_response("nope", status=403)
        with self.assertRaises(ProviderError) as cm:
            self.p.search("q", 5)
        self.assertFalse(cm.exception.auth_failure)


class TestDDGSNormalization(unittest.TestCase):
    def setUp(self):
        self.p = DDGSProvider()

    def _fake_client(self, text_rows=None, extract_result=None, text_exc=None, extract_exc=None):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        if text_exc:
            client.text.side_effect = text_exc
        else:
            client.text.return_value = text_rows or []
        if extract_exc:
            client.extract.side_effect = extract_exc
        else:
            client.extract.return_value = extract_result or {}
        return client

    @patch('universal_web_retrieval.providers.ddgs.DDGSProvider._client')
    def test_text_normalization(self, mock_client):
        mock_client.return_value = self._fake_client(text_rows=[
            {"title": "T", "href": "https://x", "body": "b"},
            {"title": "T2", "url": "https://y", "body": "b2"}])  # href OR url both handled
        r = self.p.search("q", 5)
        self.assertEqual(len(r.results), 2)
        self.assertEqual(r.results[1]["url"], "https://y")

    @patch('universal_web_retrieval.providers.ddgs.DDGSProvider._client')
    def test_text_empty(self, mock_client):
        mock_client.return_value = self._fake_client(text_rows=[])
        with self.assertRaises(ProviderError):
            self.p.search("q", 5)

    @patch('universal_web_retrieval.providers.ddgs.DDGSProvider._client')
    def test_text_package_exception(self, mock_client):
        mock_client.return_value = self._fake_client(text_exc=RuntimeError("ddgs boom"))
        with self.assertRaises(ProviderError):
            self.p.search("q", 5)

    @patch('universal_web_retrieval.providers.ddgs.DDGSProvider._client')
    def test_extract_normalization(self, mock_client):
        mock_client.return_value = self._fake_client(extract_result={
            "url": "https://x", "content": "# md"})
        r = self.p.fetch("https://x")
        self.assertEqual(r.content, "# md")
        self.assertEqual(r.content_type, "text_markdown")

    @patch('universal_web_retrieval.providers.ddgs.DDGSProvider._client')
    def test_extract_empty(self, mock_client):
        mock_client.return_value = self._fake_client(extract_result={"url": "https://x", "content": ""})
        with self.assertRaises(ProviderError):
            self.p.fetch("https://x")

    @patch('universal_web_retrieval.providers.ddgs.DDGSProvider._client')
    def test_extract_package_exception(self, mock_client):
        mock_client.return_value = self._fake_client(extract_exc=RuntimeError("boom"))
        with self.assertRaises(ProviderError):
            self.p.fetch("https://x")


if __name__ == "__main__":
    unittest.main()
