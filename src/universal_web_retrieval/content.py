"""Fetch-content post-processing shared by every provider.

Two jobs, both about the payload telling the truth about itself.

``truncate``
    Content is cut at ``WEB_RETRIEVAL_EXTRACT_CHAR_LIMIT`` characters. Before
    0.3.1 the cut was silent, so a caller could not tell a 300-character article
    from a 200 000-character one that lost 99% of its body. ``truncate`` returns
    the cut text plus ``(truncated, original_length)``, which ``FetchResult``
    then reports.

``enforce_not_blocked``
    A provider that receives a CDN interstitial ("Just a moment...", "Checking
    your browser", an Akamai "Access Denied" page) would otherwise hand that
    wall back as if it were the article. It raises a fallback-eligible
    ``ProviderError`` instead, so the chain tries the next provider.

    This is a heuristic, and it is deliberately conservative. **Length alone is
    never a signal** — short pages are extremely common ("Not found", a redirect
    stub, a one-line answer). A page is called blocked only when, inside a short
    body (``UWR_BLOCK_DETECTION_MAX_CHARS``, default 4000):

    * the title is a known challenge title AND the body carries at least one
      challenge marker, or
    * the body carries at least three distinct challenge markers.

    A long article that discusses captchas, or a short help page that mentions
    one, therefore passes through untouched. False negatives remain possible
    (a novel challenge design, a JS-only page that returns an empty shell); a
    false negative costs a useless result, a false positive silently hides a
    real page, so the thresholds favour the former.

    ``UWR_BLOCK_DETECTION=off`` disables both the check and nothing else; it
    does not change truncation reporting.
"""
from __future__ import annotations

import logging
from typing import Tuple
from urllib.parse import urlsplit

from . import config
from .errors import ProviderError

log = logging.getLogger("universal_web_retrieval.content")

# Titles that only ever belong to a challenge/denial page.
TITLE_MARKERS: Tuple[str, ...] = (
    "just a moment",
    "attention required",
    "access denied",
    "security check",
    "are you a robot",
    "verify you are human",
    "ddos-guard",
    "one more step",
    "403 forbidden",
    "captcha",
    "please wait",
)

# Body phrases produced by CDN / WAF interstitials.
BODY_MARKERS: Tuple[str, ...] = (
    "checking your browser",
    "enable javascript and cookies to continue",
    "verify you are human",
    "please complete the security check",
    "unusual traffic from your computer network",
    "cf_chl_opt",
    "cf-chl",
    "cf-error-details",
    "ray id",
    "reference #",
    "request unsuccessful",
    "incapsula",
    "ddos protection by",
    "g-recaptcha",
    "hcaptcha",
    "turnstile",
    "captcha",
)


def truncate(text: str, limit: int | None = None) -> Tuple[str, bool, int]:
    """Cut ``text`` to the extract limit, reporting whether it was cut.

    Returns ``(content, truncated, original_length)`` where ``original_length``
    is the character count *before* the cut (so ``len(content) ==
    original_length`` when nothing was cut). ``limit <= 0`` disables truncation
    rather than returning an empty string.
    """
    raw = "" if text is None else str(text)
    cap = config.extract_char_limit() if limit is None else int(limit)
    if cap <= 0 or len(raw) <= cap:
        return raw, False, len(raw)
    return raw[:cap], True, len(raw)


def challenge_reason(title: str, text: str, *,
                     max_chars: int | None = None) -> str:
    """Return why ``text`` looks like a challenge page, or ``''`` when it does not.

    See the module docstring for the combination rule; length is only an upper
    bound (a long body is a real page), never a trigger.
    """
    body = text or ""
    if not body.strip():
        return ""
    cap = config.block_detection_max_chars() if max_chars is None else int(max_chars)
    if cap > 0 and len(body) >= cap:
        return ""
    low = body.lower()
    title_low = (title or "").lower()
    title_hit = next((marker for marker in TITLE_MARKERS if marker in title_low), "")
    body_hits = [marker for marker in BODY_MARKERS if marker in low]
    if title_hit and body_hits:
        return f"title {title_hit!r} + {len(body_hits)} body marker(s)"
    if len(body_hits) >= 3:
        return f"{len(body_hits)} body markers ({body_hits[0]!r}, …)"
    return ""


def _host_of(url: str) -> str:
    """Host only — never the full URL, whose query may carry credentials."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return ""
    return parts.hostname or ""


def enforce_not_blocked(title: str, text: str, *, provider: str,
                        url: str = "") -> None:
    """Raise a fallback-eligible error when ``text`` is a challenge page.

    The message names the provider, the reason and the host — never the full
    URL, because a fetch target can carry signed query parameters.
    """
    if not config.block_detection_enabled():
        return
    reason = challenge_reason(title, text)
    if not reason:
        return
    host = _host_of(url)
    where = f" for {host}" if host else ""
    raise ProviderError(f"{provider}: blocked page{where}: {reason}")
