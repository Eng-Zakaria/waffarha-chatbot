"""Shared live/not-live freshness helpers (Phase 1, fix/routing-and-freshness).

One place for the "is this offer live?" decision, reused by retrieve(),
the faceted pools, the catalog answers and the agent tools. The reference
clock is the existing REFERENCE_DATE env var (ISO yyyy-mm-dd, single source
of truth already used by agent/tools/catalog_tools); it falls back to real
today. The audit harness pins it via --now for deterministic expiry math.

An offer is live when its effective expiry (metadata "expiry") is absent/
unparseable or on/after the reference date. FAQ docs (no expiry) are always
live. offer_status is advisory only: this index holds uniform-active offers,
so date is the discriminating signal.
"""
import datetime
import os


def today():
    """Injectable reference date: REFERENCE_DATE env or real today."""
    raw = os.environ.get("REFERENCE_DATE")
    if raw:
        try:
            return datetime.date.fromisoformat(raw.strip()[:10])
        except ValueError:
            pass
    return datetime.date.today()


def parse_expiry(value):
    """Parse an expiry metadata value to a date, or None when absent/garbled."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def is_live(metadata, now=None):
    """True when the doc may surface in organic (non-anchored) results."""
    if not isinstance(metadata, dict):
        return True
    if metadata.get("source") == "faq":
        return True
    now = now or today()
    exp = parse_expiry(metadata.get("expiry"))
    if exp is None:
        return True
    return exp >= now


def split_live(entries, now=None):
    """Partition offer-entry dicts (each with a metadata mapping) into
    (live, expired). Entries without usable expiry count as live."""
    now = now or today()
    live, expired = [], []
    for e in entries or []:
        meta = e.get("metadata", {}) if isinstance(e, dict) else {}
        (live if is_live(meta, now) else expired).append(e)
    return live, expired


def qual_score(candidate):
    """Phase 4 qualification score for one retrieve() candidate dict.

    With SCORE_GATES_EMBEDDING_ONLY (default): the raw embedding similarity,
    or None for BM25-only candidates (no dense measurement exists -- gates
    must skip them, never judge them). Legacy (flag off): the bonus-inflated
    combined_score, reproducing the old behavior exactly.
    """
    from core import config as _config
    if bool(getattr(_config, "SCORE_GATES_EMBEDDING_ONLY", True)):
        return candidate.get("embedding_score")
    return candidate.get("combined_score", 0.0)
