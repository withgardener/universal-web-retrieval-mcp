"""Hermetic test environment.

Provider keys and ``UWR_*``/``WEB_RETRIEVAL_*`` knobs are cleared for every test
so results never depend on whatever the host shell happens to export (the
production Hermes env does export ``ANYSEARCH_API_KEY``, which used to flip
keyed/keyless asserts, and ``WEB_RETRIEVAL_BLOCK_PRIVATE`` would otherwise turn
the opt-in private-target check on for the whole suite).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest  # noqa: E402

_CLEARED_KEYS = (
    "ANYSEARCH_API_KEY",
    "TAVILY_API_KEY",
    "YUANBAO_API_KEY",
    "WSA_API_KEY",
    "BOCHA_API_KEY",
    "DEEPSEEK_API_KEY",
    "MOONSHOT_API_KEY",
    "KIMI_API_KEY",
    "ZHIPU_API_KEY",
    "BIGMODEL_API_KEY",
    "BRAVE_API_KEY",
    "JINA_API_KEY",
)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("UWR_", "WEB_RETRIEVAL_")) or name in _CLEARED_KEYS:
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch):
    """No test may depend on the host resolver.

    The opt-in private-target check (0.3.1, ``WEB_RETRIEVAL_BLOCK_PRIVATE``)
    resolves the target host to decide whether it is public, so an unpatched
    resolver would make those tests depend on the host's DNS. Only
    ``netguard._resolve`` is stubbed — patching ``socket.getaddrinfo`` itself
    would also redirect httpx and turn a stubbed test into a real connection
    attempt. Tests that need a specific answer patch ``netguard._resolve``
    themselves, which overrides this default.
    """
    from universal_web_retrieval import netguard

    def fake(host, port):
        return ["93.184.216.34"]

    monkeypatch.setattr(netguard, "_resolve", fake)
    yield
