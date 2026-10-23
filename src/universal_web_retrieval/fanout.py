"""Fan-out result merging: URL normalisation + Reciprocal Rank Fusion.

Fan-out asks several providers the same query and then has to turn N ranked
lists into ONE ranked list. Two steps:

``dedupe_key``  normalises a result URL so the same page fetched from different
                providers collapses into one row. Tracking parameters, ``www.``,
                fragments, parameter order and a trailing slash are all noise.
``merge_results`` scores the collapsed rows with RRF (``sum 1/(k + rank)``),
                breaks ties deterministically, and keeps the best-ranked
                occurrence's title/URL while taking the longest snippet.

The key is only ever used for matching — the output keeps a real URL.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

# Standard RRF smoothing constant: flattens the influence of rank 1 vs rank 10
# so one provider's top hit cannot outvote agreement between two providers.
RRF_K = 60

# Query parameters that never change which page is being linked to.
_TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src", "spm",
})
_TRACKING_PREFIXES = ("utm_",)


def _is_tracking(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def dedupe_key(raw_url: str) -> str:
    """Normalise a result URL into a stable merge key ('' when unusable)."""
    url = (raw_url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:  # e.g. unbalanced IPv6 brackets — fall back to the raw text
        return url.lower()

    scheme = parts.scheme.lower()
    host = (parts.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not scheme and not host:
        # Not an absolute URL: normalise the path-ish text only.
        return url.lower().rstrip("/")

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    params = sorted((name, value) for name, value in
                    parse_qsl(parts.query, keep_blank_values=True)
                    if not _is_tracking(name))
    key = f"{scheme}://{host}{path}"
    query = urlencode(params)
    return f"{key}?{query}" if query else key


def merge_results(provider_rows: Sequence[Tuple[str, Iterable[Dict[str, Any]]]],
                  limit: int) -> List[Dict[str, Any]]:
    """Merge per-provider result lists into one RRF-ranked list.

    ``provider_rows`` is ``[(provider_name, rows)]`` in chain order; ``rows``
    must already be in that provider's own rank order.
    """
    names = [name for name, _rows in provider_rows]
    entries: Dict[str, Dict[str, Any]] = {}

    for chain_index, (_name, rows) in enumerate(provider_rows):
        for rank, row in enumerate(rows or (), start=1):
            key = dedupe_key(str(row.get("url") or ""))
            if not key:
                continue
            entry = entries.get(key)
            if entry is None:
                entry = entries[key] = {"occurrences": []}
            entry["occurrences"].append(
                {"chain": chain_index, "rank": rank, "row": row})

    merged: List[Dict[str, Any]] = []
    for entry in entries.values():
        occurrences = entry["occurrences"]
        # One RRF term per provider: the best rank that provider gave this URL.
        best_rank_per_provider: Dict[int, int] = {}
        for occurrence in occurrences:
            chain = occurrence["chain"]
            previous = best_rank_per_provider.get(chain)
            if previous is None or occurrence["rank"] < previous:
                best_rank_per_provider[chain] = occurrence["rank"]

        score = sum(1.0 / (RRF_K + rank) for rank in best_rank_per_provider.values())
        # Chain order of the contributing providers == sorted chain indices.
        sources = [names[chain] for chain in sorted(best_rank_per_provider)]
        best = min(occurrences, key=lambda occ: (occ["rank"], occ["chain"]))

        snippet = ""
        for occurrence in occurrences:
            text = str(occurrence["row"].get("snippet") or "")
            if len(text) > len(snippet):
                snippet = text

        row: Dict[str, Any] = {
            "title": str(best["row"].get("title") or ""),
            "url": str(best["row"].get("url") or ""),
            "snippet": snippet,
        }
        for field in ("published", "site"):
            for occurrence in sorted(occurrences,
                                     key=lambda occ: (occ["chain"], occ["rank"])):
                value = occurrence["row"].get(field)
                if value not in (None, ""):
                    row[field] = value
                    break
        row["sources"] = sources
        merged.append({
            "row": row,
            "score": score,
            "sources": len(sources),
            "best_rank": best["rank"],
            "best_chain": best["chain"],
        })

    # RRF score desc, then agreement (more sources), best rank, chain order.
    merged.sort(key=_rank_key)
    return [item["row"] for item in merged[:max(0, limit)]]


def _rank_key(item: Dict[str, Any]) -> Tuple[float, int, int, int]:
    """Ordering contract: RRF score, then sources, best rank, chain order.

    All three tie-breaks exist because RRF scores collide routinely on small
    result sets (two providers, same ranks), and a merge must be deterministic.
    """
    return (-item["score"], -item["sources"], item["best_rank"], item["best_chain"])
