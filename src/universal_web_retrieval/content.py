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
    ``ProviderError`` instead, so the chain *downgrades* the wall to the next
    provider.

    This is a heuristic, and it errs towards returning content. **Length alone
    is never a signal**, and neither is a word: a page is called blocked only
    when, inside a short body (``UWR_BLOCK_DETECTION_MAX_CHARS``, default 4000):

    * at least two distinct **technical fingerprints** appear — strings only a
      challenge implementation emits (``cf_chl_opt``, "checking your browser",
      "enable javascript and cookies to continue", ``_incapsula_resource``…); or
    * the **title is a challenge title** ("Just a moment...", "Access Denied",
      "Attention Required! | Cloudflare"…) *and* the body carries at least one
      marker, technical or prose. The title must be the whole title or the
      challenge phrase followed by the site's own short label — a subtitle
      ("Access Denied: a practical guide to 403 responses") is prose, not a wall.

    A single marker is never enough, so a help page that explains CAPTCHA, a
    glossary that lists reCAPTCHA/hCaptcha/Turnstile, an FAQ quoting "Just a
    moment...", or a doc showing one ``cf_chl_opt`` sample all pass through
    untouched (they were killed by the earlier title-word rule). False
    negatives remain possible — a novel interstitial, a JS-only page that
    returns an empty shell, a brand-like subtitle ("Access Denied | Mozilla
    Support") — and are preferred: a false negative costs one useless result,
    a false positive silently hides a real page.

    ``UWR_BLOCK_DETECTION=off`` disables both the check and nothing else; it
    does not change truncation reporting.
"""
from __future__ import annotations

import logging
import re
from typing import Tuple
from urllib.parse import urlsplit

from . import config
from .errors import ProviderError

log = logging.getLogger("universal_web_retrieval.content")

# Titles that only ever belong to a challenge/denial page. Matched against the
# normalised title as an exact value or as a prefix followed by a separator
# ("Just a moment...", "Access Denied", "Attention Required! | Cloudflare"), so
# a help page that merely contains the word — "CAPTCHA help" — cannot match.
CHALLENGE_TITLES: Tuple[str, ...] = (
    "just a moment",
    "attention required",
    "access denied",
    "security check",
    "are you a robot",
    "verify you are human",
    "ddos-guard",
    "one more step",
    "403 forbidden",
    "please wait",
    "checking your browser",
    "sucuri website firewall",
)

# Strings only a challenge implementation emits. Two distinct ones in one short
# body identify a wall on their own; one is a code sample or a doc mention.
TECHNICAL_MARKERS: Tuple[str, ...] = (
    "checking your browser",
    "checking if the site connection is secure",
    "enable javascript and cookies to continue",
    "cf_chl_opt",
    "cf-chl-",
    "cf-error-details",
    "cf-browser-verification",
    "attention required! | cloudflare",
    "_incapsula_resource",
    "incapsula incident id",
    "request unsuccessful. incapsula",
    "ddos-guard",
    "sucuri website firewall",
)

# Prose that appears on real walls *and* in pages that discuss walls. Never
# enough on its own: it needs a challenge title alongside it.
HINT_MARKERS: Tuple[str, ...] = (
    "verify you are human",
    "are you a robot",
    "please complete the security check",
    "unusual traffic from your computer network",
    "our systems have detected unusual traffic",
    "just a moment...",
    "turnstile",
    "captcha",
    "ray id",
    "reference #",
    "access denied",
)

# Kept for callers that introspect the marker tables; the full set is the union.
BODY_MARKERS: Tuple[str, ...] = TECHNICAL_MARKERS + HINT_MARKERS

# Punctuation that may follow a challenge title before the site's own suffix:
# "Access Denied - example.com", "Just a moment...", "Attention Required! | …".
_TITLE_SEPARATORS = ("...", " |", " -", " —", " –", " (", "!", ":", ".")


# Words that belong to a subtitle and never to a site's own label. "Access
# Denied: a practical guide to 403 responses" is a help page; "Access Denied -
# example.com" is a wall. A suffix containing any of these is a phrase.
_SUBTITLE_WORDS = frozenset({
    "a", "an", "the", "to", "of", "for", "and", "or", "how", "why", "what",
    "when", "where", "your", "you", "is", "are", "in", "on", "with", "from",
})

# A site label is short; anything longer is prose that happens to follow a colon.
_SUFFIX_MAX_WORDS = 4


def _is_site_label(suffix: str) -> bool:
    """True when a title suffix reads as the site's own label, not a subtitle."""
    words = suffix.split()
    if not words or len(words) > _SUFFIX_MAX_WORDS:
        return False
    return not any(word.strip(",;:") in _SUBTITLE_WORDS for word in words)


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


def _norm_title(title: str) -> str:
    """Lower-cased, single-spaced title with typographic ellipses normalised."""
    text = (title or "").lower().replace("…", "...")
    return re.sub(r"\s+", " ", text).strip()


def challenge_title(title: str) -> str:
    """Return the challenge title ``title`` matches, or ``''``.

    A match is the whole title ("Access Denied") or the title followed by a
    separator and the site's own label ("Access Denied - example.com"). A help
    page that merely contains the words — "CAPTCHA help" — is not a match, and
    neither is a title whose suffix is a subtitle: "Access Denied: a practical
    guide to 403 responses" is prose after a colon, not a page that is denying
    access.
    """
    normalised = _norm_title(title)
    if not normalised:
        return ""
    for marker in CHALLENGE_TITLES:
        if normalised == marker:
            return marker
        if normalised.startswith(marker):
            rest = normalised[len(marker):]
            if not any(rest.startswith(sep) for sep in _TITLE_SEPARATORS):
                continue
            suffix = rest.lstrip(" .:!|—-–(").strip()
            # "Just a moment..." normalises to "just a moment..." — no suffix.
            if suffix == "" or _is_site_label(suffix):
                return marker
    return ""


def challenge_reason(title: str, text: str, *,
                     max_chars: int | None = None) -> str:
    """Return why ``text`` looks like a challenge page, or ``''`` when it does not.

    Two rules only (see the module docstring): two distinct technical
    fingerprints, or a challenge title plus one marker. A single word — however
    often a page about CAPTCHAs repeats it — never blocks.
    """
    body = text or ""
    if not body.strip():
        return ""
    cap = config.block_detection_max_chars() if max_chars is None else int(max_chars)
    if cap > 0 and len(body) >= cap:
        return ""
    low = body.lower()
    technical = [marker for marker in TECHNICAL_MARKERS if marker in low]
    if len(technical) >= 2:
        return (f"{len(technical)} challenge fingerprints "
                f"({technical[0]!r}, {technical[1]!r}, …)")
    title_hit = challenge_title(title)
    if title_hit:
        hints = [marker for marker in HINT_MARKERS if marker in low]
        marker = (technical or hints)
        if marker:
            return f"title {title_hit!r} + marker {marker[0]!r}"
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
