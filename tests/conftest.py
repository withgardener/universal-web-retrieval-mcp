"""Hermetic test environment.

Provider keys and ``UWR_*`` knobs are cleared for every test so results never
depend on whatever the host shell happens to export (the production Hermes env
does export ``ANYSEARCH_API_KEY``, which used to flip keyed/keyless asserts).
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
        if name.startswith("UWR_") or name in _CLEARED_KEYS:
            monkeypatch.delenv(name, raising=False)
    yield
