"""Credential scrubbing with an explicit trust boundary.

Until 0.3.1 ``server._json()`` ran one global ``[?&](token|key|sig|…)="``
regex over the **entire** serialised payload. Errors were scrubbed correctly,
but successful results were scrubbed too: any signed URL inside fetched Markdown
or a search row (``?sig=…``, ``?token=…``, ``?key=…``) came back rewritten to
``<redacted>`` — a link that no longer works, silently.

The rule now:

``scrub_diagnostic(text)`` — for text *we* or a provider wrote about a request:
error strings, ``attempts`` outcomes, log lines. Removes configured key values,
``NAME=value`` key assignments, bearer tokens and credential-bearing query
parameters (the parameter name stays, which is what makes the message useful).

``scrub_exact_secrets(text)`` — the narrow pass that is also applied to
successful payloads: it removes the *configured* credential values and nothing
else. A page may legitimately contain ``?token=`` (a signed image URL) or the
word "Bearer" in a code sample; a page containing our own API key is a leak by
definition and is redacted in place.

``scrub_payload(payload)`` — structural walk for a tool payload: diagnostic
fields go through ``scrub_diagnostic``, everything else is only subject to the
exact-secret pass (done by the caller after serialisation).

Trust boundary: this server's payload goes to the calling agent only and is
never written to a log. Third-party signed URLs inside page content are
returned as-is; they belong to the page's publisher, not to this server.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Mapping

REDACTED = "[REDACTED]"
URL_REDACTED = "<redacted>"

# Environment variables that hold provider credentials. Kept in one place so a
# new provider cannot be added without the scrubber learning about it (there is
# a test that walks the registry and asserts every key env is covered).
PROVIDER_KEY_ENVS = (
    "ANYSEARCH_API_KEY", "TAVILY_API_KEY", "YUANBAO_API_KEY", "WSA_API_KEY",
    "BOCHA_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY",
    "ZHIPU_API_KEY", "BIGMODEL_API_KEY", "BRAVE_API_KEY", "JINA_API_KEY",
)

# ``?token=...`` / ``&sig=...`` inside a URL. Only ever applied to diagnostics:
# a success payload's URLs must survive byte-for-byte or the link is broken.
_URL_CREDENTIAL_RE = re.compile(
    r"(?i)([?&](?:token|api_key|apikey|key|secret|password|sig|signature|"
    r"access_token|auth|session|sessionid|code)=)([^&\s]+)"
)

# A bearer credential actually being sent. Needs 12+ token characters so prose
# ("Bearer token") and short placeholders are left alone.
_BEARER_RE = re.compile(
    r"(?i)\b(bearer\s+)([A-Za-z0-9\-._~+/]{12,}={0,2})"
)

# ``BOCHA_API_KEY=abc`` in a message, or ``ZHIPU_API_KEY: abc``.
_KEY_ASSIGN_RE = re.compile(
    r"(?i)\b(" + "|".join(PROVIDER_KEY_ENVS) + r")(\s*[=:]\s*)([^\s,;\"']+)"
)

# Payload keys whose values are diagnostics about a request.
DIAGNOSTIC_KEYS = frozenset({"error", "errors", "message", "detail", "reason",
                             "warning", "hint"})


def _configured_secrets() -> List[str]:
    """Configured credential values, longest first (so nesting cannot leak)."""
    values = {os.environ.get(name, "") for name in PROVIDER_KEY_ENVS}
    return sorted((v for v in values if v), key=len, reverse=True)


def scrub_exact_secrets(text: str) -> str:
    """Remove configured credential *values* from ``text``; nothing else."""
    out = text or ""
    for secret in _configured_secrets():
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(secret)
                             + r"(?![A-Za-z0-9_])")
        out = pattern.sub(REDACTED, out)
    return out


def scrub_diagnostic(text: str) -> str:
    """Full scrub for text that may echo a request URL, header or key."""
    out = scrub_exact_secrets(str(text or ""))
    out = _KEY_ASSIGN_RE.sub(r"\1\2" + REDACTED, out)
    out = _BEARER_RE.sub(r"\1" + REDACTED, out)
    return _URL_CREDENTIAL_RE.sub(r"\1" + URL_REDACTED, out)


def _scrub_attempt(attempt: Any) -> Any:
    if not isinstance(attempt, Mapping):
        return attempt
    return {key: (scrub_diagnostic(value) if isinstance(value, str) else value)
            for key, value in attempt.items()}


def scrub_payload(payload: Any) -> Any:
    """Return ``payload`` with diagnostic fields scrubbed, structure intact.

    Content, titles, URLs and result rows are left byte-for-byte alone; only
    ``error``/``message``/… strings and the per-provider ``attempts`` trail are
    rewritten. Exact-secret removal for the rest happens after serialisation so
    it cannot be defeated by escaping.
    """
    if isinstance(payload, Mapping):
        out: Dict[Any, Any] = {}
        for key, value in payload.items():
            if isinstance(key, str) and key in DIAGNOSTIC_KEYS and isinstance(value, str):
                out[key] = scrub_diagnostic(value)
            elif key == "attempts" and isinstance(value, (list, tuple)):
                out[key] = [_scrub_attempt(item) for item in value]
            else:
                out[key] = scrub_payload(value)
        return out
    if isinstance(payload, (list, tuple)):
        return [scrub_payload(item) for item in payload]
    return payload
