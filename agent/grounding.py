"""Deterministic entity grounding for the agent layer.

Before a tool call is executed, every entity the planner proposed
(merchant / category / product / price bound) is cross-checked against
FacetedCatalog's deterministic resolvers run on the *normalized user
text*. A proposed entity that is not corroborated by the user's own
words is dropped and recorded in the trace as
"unconfirmed entity dropped: <value>".

This is a pure safety net against planner hallucination — it uses no LLM
call and no heuristics beyond the existing keyword/alias resolvers.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from core.faceted import normalize_arabic

# ------------------------------------------------------------------ #
# Traceable numbers in the user message
# ------------------------------------------------------------------ #

_ARABIC_DIGITS = dict(zip("٠١٢٣٤٥٦٧٨٩", "0123456789"))

_NUMBER_RE = re.compile(r"\d[\d,.]*")


def numbers_in_text(text: str | None) -> set[float]:
    """Every numeric value literally present in the user message, with
    Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩) normalized to ASCII."""
    nums: set[float] = set()
    q = text or ""
    if not q.strip():
        return nums
    latin = q.translate(str.maketrans(_ARABIC_DIGITS))
    try:
        int(latin)  # fast-path: whole message is a number
        nums.add(float(int(latin)))
        return nums
    except ValueError:
        pass
    for token in _NUMBER_RE.findall(latin):
        try:
            nums.add(float(token.replace(",", "")))
        except ValueError:
            continue
    return nums


def bound_traceable(bound: float | None, numbers: Iterable[float], tol: float = 1e-6) -> bool:
    """True when a proposed price bound equals a number in the message."""
    if bound is None:
        return False
    return any(abs(bound - n) <= tol for n in numbers)


# ------------------------------------------------------------------ #
# Corroboration of proposed entities
# ------------------------------------------------------------------ #

def grounded_merchants(user_query: str, faceted) -> set[str]:
    """Set of canonical merchant names the message itself mentions."""
    q = (user_query or "").strip()
    if not q:
        return set()
    return {m for m in (faceted.resolve_merchants(q) or []) if m in getattr(faceted, "merchants", ())}


def grounded_category(user_query: str, faceted) -> str | None:
    """Canonical category key the message itself mentions."""
    q = (user_query or "").strip()
    return faceted.resolve_category(q) if q else None


def grounded_product(user_query: str, faceted) -> str | None:
    """Canonical product key the message itself mentions."""
    q = (user_query or "").strip()
    return faceted.resolve_product(q) if q else None


def _corroborated_merchant(proposed: object, grounded: set[str], faceted) -> bool:
    value = str(proposed).strip()
    if not value:
        return True  # empty merchant arg means "no filter" -> nothing to ground
    if value in grounded:
        return True
    # Proposed value may be a raw alias/brand; resolve to its canonical
    # form and compare against the corroborated canonical set.
    canonical = faceted.resolve_merchants(value)
    if canonical:
        return canonical[0] in grounded
    return False


def _corroborated_category(proposed: object, grounded: str | None, faceted) -> bool:
    value = str(proposed).strip()
    if not value:
        return True
    canonical = faceted.resolve_category(value)
    return (canonical or value) == grounded


def _corroborated_product(proposed: object, grounded: str | None, faceted) -> bool:
    value = str(proposed).strip()
    if not value:
        return True
    canonical = faceted.resolve_product(value)
    return (canonical or value) == grounded


# ------------------------------------------------------------------ #
# Grounding pass over a search_offers argument dict
# ------------------------------------------------------------------ #

def _merge_corroborated(cleaned: dict, key: str, value) -> str | None:
    """Add a corroborated entity the planner omitted, without overwriting an
    explicit already-grounded value. Returns a merge note when applied.

    Only called with a value the user's own words corroborated (`grounded_*`),
    so this is the "keep corroborated entities" half of the contract -- it
    never trusts an unconfirmed planner claim. Merchant is intentionally out of
    scope: a bare merchant mention in a comparison/exclusion turn is not a
    filter request, so only category/product (the corroborated facet keys the
    planner most often drops) are restored."""
    if key not in ("category", "product"):
        return None
    if cleaned.get(key) not in (None, "", []):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned[key] = value
    return f"corroborated entity merged: {key}={cleaned[key]}"


def ground_search_args(args: dict, user_query: str | None, faceted) -> dict:
    """Return (cleaned_args, meta) where `cleaned_args` is the argument dict
    with every unconfirmed filter removed AND every entity the user's own
    words corroborate merged back in, and `meta` records what happened:
    "unconfirmed entity dropped: <value>" / "corroborated entity merged: ...".

    `args` is treated as untrusted planner output. Data the planner invented
    (not corroborated by the user text / catalog resolvers) is never passed to
    the tool; corroborated data the planner omitted is restored, because the
    tool call's filters must reflect what the user actually asked for.
    """
    dropped: list[str] = []
    merged: list[str] = []
    cleaned = dict(args or {})

    q = user_query or ""
    g_merchants = grounded_merchants(q, faceted)
    g_category = grounded_category(q, faceted)
    g_product = grounded_product(q, faceted)
    numbers = numbers_in_text(q)

    # -- merchant -----------------------------------------------------
    if cleaned.get("merchant") not in (None, "", []):
        proposed = cleaned["merchant"]
        if isinstance(proposed, (list, tuple)):
            kept = []
            for item in proposed:
                if _corroborated_merchant(item, g_merchants, faceted):
                    kept.append(item)
                else:
                    dropped.append(f"unconfirmed entity dropped: {item}")
            cleaned["merchant"] = kept[0] if kept else None
        else:
            if _corroborated_merchant(proposed, g_merchants, faceted):
                cleaned["merchant"] = proposed
            else:
                dropped.append(f"unconfirmed entity dropped: {proposed}")
                cleaned["merchant"] = None
        if not cleaned["merchant"]:
            cleaned.pop("merchant", None)

    # -- category ------------------------------------------------------
    if cleaned.get("category") not in (None, ""):
        proposed = cleaned["category"]
        if _corroborated_category(proposed, g_category, faceted):
            cleaned["category"] = proposed
        else:
            dropped.append(f"unconfirmed entity dropped: {proposed}")
            cleaned.pop("category", None)

    # -- product -------------------------------------------------------
    if cleaned.get("product") not in (None, ""):
        proposed = cleaned["product"]
        if _corroborated_product(proposed, g_product, faceted):
            cleaned["product"] = proposed
        else:
            dropped.append(f"unconfirmed entity dropped: {proposed}")
            cleaned.pop("product", None)

    # -- merge corroborated entities the planner omitted ---------------
    # The corroborated entity dict is computed above; if the planner failed
    # to carry one into the args (e.g. product=pizza for "عايز بيتزا من
    # 100-150 جنيه"), the tool call would silently degrade to a price-only
    # search. Restore it here so the executed filters match the user's words.
    # An explicit, already-grounded value is never overwritten.
    for key, value in (("category", g_category), ("product", g_product)):
        note = _merge_corroborated(cleaned, key, value)
        if note:
            merged.append(note)

    # -- price bounds preserve only numbers the user actually wrote -----
    price = cleaned.get("price_range")
    if isinstance(price, (list, tuple)) and len(price) == 2:
        lo, hi = price
        lo = float(lo) if lo is not None else None
        hi = float(hi) if hi is not None else None
        keep_lo = bound_traceable(lo, numbers)
        keep_hi = bound_traceable(hi, numbers)
        if not keep_lo and not keep_hi:
            dropped.append("unconfirmed entity dropped: price_range")
            cleaned.pop("price_range", None)
        else:
            cleaned["price_range"] = [lo if keep_lo else None,
                                      hi if keep_hi else None]
            if not keep_lo:
                dropped.append("unconfirmed entity dropped: price look-upper bound")
            if not keep_hi:
                dropped.append("unconfirmed entity dropped: price higher bound")

    # -- semantic query: never let a planner-authored anchor override -----
    # the user's own words. The free-text `query` is a deterministic anchor
    # for semantic fallback; a planner rewrite can smuggle an unconfirmed
    # brand past the entity checks above (Zara for "cheaper KFC offers").
    # A rewrite is kept only when it introduces no NEW content relative to
    # the user message (strict normalized substring); anything else is
    # replaced by the user's own text and traced.
    pq = cleaned.get("query")
    if isinstance(pq, str) and pq.strip() and q:
        norm_pq = normalize_arabic(pq).casefold().strip()
        norm_q = normalize_arabic(q).casefold().strip()
        if norm_pq and norm_pq not in norm_q:
            dropped.append(f"unconfirmed entity dropped: query={pq}")
            cleaned["query"] = q

    return cleaned, {"dropped": dropped,
                     "merged": merged,
                     "numbers_in_query": sorted(numbers),
                     "grounded": {"merchants": sorted(g_merchants),
                                  "category": g_category,
                                  "product": g_product}}