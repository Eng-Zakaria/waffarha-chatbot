"""
Deterministic reference resolution for the agent (Stage 3).

`resolve_reference()` decides whether `query` points back at offers that were
ACTUALLY SHOWN earlier in this session, and if so which stored entries it
means. Every returned target is an element of the `recent_offers` list the
caller passes in -- memory.SessionMemory.recent() / the engine's
`recent_offers_full` -- i.e. the same stored state the old cascade's
`_resolve_followup_targets` used. A reference is NEVER resolved from freeform
reading of the conversation: the planner may propose a follow-up, but it only
holds when the user's own words deterministically point at a stored offer.

This module deliberately has NO LLM calls and does NOT import core.rag_engine
(which pulls ~7s of heavy deps at import). The detection signals below --
comparison words, other-offer phrases, price/anaphora regexes, ordinals,
cross-reference regexes, price-range parser -- are copied VERBATIM from
core/rag_engine.py so behavior cannot drift from the deterministic rule
fast-paths that shipped in earlier stages. Only core.faceted (normalize_arabic)
and core.config (MERCHANT_ALIASES) are imported, matching agent/grounding.py.

In the old cascade the ambiguous cases were handed to an LLM follow-up
summarizer. Here they resolve to NEW_TOPIC instead: the query is treated as
self-contained and lets the normal catalog/FAQ tools answer it. That is the
"no trust the planner's continuity claim" rule -- evidence must come from
stored state, not from a model's interpretation of raw history.
"""
from __future__ import annotations

import re

from core.faceted import normalize_arabic as _normalize_arabic

try:
    from core.config import MERCHANT_ALIASES as _MERCHANT_ALIASES
except Exception:  # noqa: BLE001 -- config may not be importable in isolation
    _MERCHANT_ALIASES = {}

# ------------------------------------------------------------------ #
# Signals copied verbatim from core/rag_engine.py (see module docstring)
# ------------------------------------------------------------------ #
_CURRENCY_PATTERN = r"(?:\s*(?:egp|le|l\.e|ج\.م|جنيه|جنية))?"
_RANGE_RE = re.compile(
    rf"(?:between|from|range)\s*(\d+){_CURRENCY_PATTERN}\s*(?:to|and|-|وحتي|لـ|ل|الي|و)\s*(\d+){_CURRENCY_PATTERN}"
    rf"|بين\s*(\d+){_CURRENCY_PATTERN}\s*و\s*(\d+){_CURRENCY_PATTERN}"
    rf"|من\s*(\d+){_CURRENCY_PATTERN}\s*(?:لـ|ل|الي|حتي|و|لحد)\s*(\d+){_CURRENCY_PATTERN}",
    re.IGNORECASE,
)
_MAX_RE = re.compile(
    rf"(?:under|below|up to|less than)\s*(\d+){_CURRENCY_PATTERN}|(?:حتي|اقل من|تحت)\s*(\d+){_CURRENCY_PATTERN}",
    re.IGNORECASE,
)
_MIN_RE = re.compile(
    rf"(?:over|above|more than)\s*(\d+){_CURRENCY_PATTERN}|(?:فوق|اكثر من|اعلي من)\s*(\d+){_CURRENCY_PATTERN}",
    re.IGNORECASE,
)

_COMPARISON_WORDS = {
    "compare", "vs", "versus", "both of", "difference between", "better deal",
    "which is better", "which one",
    "قارن", "الفرق بين", "ايهما", "أيهما", "الاثنين", "أفضل من", "افضل من",
}

_OTHER_OFFER_PHRASES = {
    "what else", "anything else", "other than", "different from",
    "غير", "غير ده", "غير دي", "غيرذا", "بخلاف", "سوى",
    "تاني غير", "غير تاني", "غيره", "غير ها",
    "gher", "ghyr", "dika gher", "dihom gher", "ay tany gher",
}

_OTHER_OFFER_TAIL = (
    "عروض تانية", "عروض تاني", "عرض تاني", "عروض كمان",
    "كمان عروض", "باقي العروض", "عروض غير", "غير العروض",
    "else", "others", "more like", "anything like",
)

_SAME_OFFER_ANAPHORA_RES = [
    re.compile(r"(ده|ديه|دي|هاده)\s*(بكام|بكامه|السعر|سعره|سعر|بتاعه|خلص|كلف)"),
    re.compile(r"(بكام|بكامه)(?=.*?(الخصم|الأصلي|قبل|الحقيقي))"),
    re.compile(r"(قبل\s+الخصم|السعر\s+الأصلي|السعر\s+الحقيقي|الأصلي\s+قبل|الأصلي\s+بتاعه)"),
    re.compile(r"(التاني|الثاني|اللي\s+فات|اللي\s+قبل|اللي\s+طلع)\s*(بكام|بكامه|كان\s+بكام|السعر)"),
    re.compile(r"\bbikaam|bkaam|bekam\b", re.IGNORECASE),
]

_OFFER_REFERENCE_RES = [
    re.compile(r"(ده|ديه|دي|دّة|دة|هذا|هذه|هادا|هادي|ذا|دول|ذول)\b"),
    re.compile(r"(كده|كدة|كده|كدى|ذيك|كذا)"),
    re.compile(r"(العرض|العروض|العروضين|الاتنين|الاثنين|اللي فات|اللي قبله|اللي قبلي|اللي شفناه|اللي شفنا|اللي طلع|الموضوع ده)"),
    re.compile(r"(التاني|الثاني|التانية|الثانية|التالت|الثالث|الأول|الاول|التالته|بعدهم|قبلهم)"),
    re.compile(r"(بكام|بكم|بكامه|بكمه)"),
    re.compile(r"(أرخص|اغلى|أغلى|أعلى|أقل|اقل|أفضل|افضل|أحسن|احسن|بديل|بديله)(?=.*(منه|منها|منهم|منهن|من ده|من دي|من كده|من دول))"),
    re.compile(r"(غير دي|غيرها|غيرهم|غير ده|غير كده)"),
    re.compile(r"(عروض تانية|عرض تاني|كمان عروض|برده|برضه|الباقي|الباقين|باقي العروض|باقي)"),
    re.compile(r"(سعره|سعرها|خصمها|السعر ده|سعر ده|سعر العرض)"),
    re.compile(r"(قارن|الفرق|اقارن|مقارنة|شوف الفرق|فرق بين|ولخص)"),
    re.compile(r"\b(this|that|these|those|the second|the first|the other|another|cheaper|more expensive|compare|the previous|the ones|them|it)\b", re.IGNORECASE),
    re.compile(r"\b(bkaam|bkam|bkamh|tany|tanya|tani|dah|deh|doh|elly fat|awel|taneya)\b", re.IGNORECASE),
]

_SELF_CONTAINED_EXCEPTION_RES = [
    re.compile(r"(لحد تاني|لشخص تاني|حد تاني|لواحد تاني|لحد غير|بتبعت لحد|بترسل لحد|الحد تاني)"),
]

_ORDINAL_WORDS = {
    "first": 0, "1st": 0, "الاول": 0, "الأول": 0,
    "second": 1, "2nd": 1, "التاني": 1, "الثاني": 1,
    "third": 2, "3rd": 2, "التالت": 2, "الثالث": 2,
    "fourth": 3, "4th": 3, "الرابع": 3,
    "last": -1, "الاخير": -1, "الأخير": -1,
    # Stage-4 additive: definite feminine Arabic ordinals ("التانية بكام")
    # and the DEFINITE Franco/Egyptian-Arabizi article+ordinal spellings
    # ("el tany be kam"). Bare "تاني"/"tany" is deliberately NOT here: in
    # everyday Egyptian it means "another", not "the second".
    "التانية": 1, "الثانية": 1,
    "التالتة": 2, "الثالثة": 2,
    "الرابعة": 3,
    "الاخيرة": -1, "الأخيرة": -1,
    "eltany": 1, "el tany": 1, "altany": 1, "al tany": 1,
    "eltani": 1, "el tani": 1, "altani": 1, "al tani": 1,
    "eltanya": 1, "el tanya": 1,
    "elta tany": 1, "el ta tany": 1,
}

_PRICE_FILTER_WORDS = {
    "في", "فيه", "من", "او", "أو", "لو", "ان", "إن", "ده", "دي", "هل", "بس",
    "كل", "كام", "بكام", "حابب", "عايز", "عاوز", "ايه", "إيه", "اعلي", "اعلى",
    "أعلى", "اقل", "أقل", "فوق", "تحت", "حتى", "حتي", "اكثر", "أكثر", "جنيه",
    "جنية", "و", "ل", "لـ", "الي", "إلى", "الى", "قد", "طب",
    "the", "a", "an", "of", "in", "on", "at", "for", "to", "is", "are", "any",
    "under", "over", "above", "below", "between", "from", "less", "more",
    "than", "egp",
}

# Copied verbatim from core/rag_engine.py (lines 1420-1491): the whole-word
# anaphora set, the follow-up signal phrases, and the bare price question
# words. These back `_looks_like_followup_text`, which the engine used to
# decide a query was REALLY pointing back at shown content before letting the
# ambiguous (non merchant/ordinal) resolution paths run.
_ANAPHORA_WORDS = {
    "this", "that", "it", "ده", "دي", "دة", "هذا", "هذه", "ذلك", "دول", "هو", "هي",
    # Franco/Arabizi anaphora
    "do", "diki", "dika", "dako", "dakom", "dihom", "dih", "dalk",
}

# Stage 4 additive (kept OUT of the verbatim set above): whole-word Franco
# pointers for "ده/دي/ذا" ("3ayez 7aga zay dah"). Word-boundary matched only,
# so "da"/"deh" cannot hit inside another word.
_FRANCO_ANAPHORA = {"da", "dah", "deh", "dh", "dol", "dolhom"}

_FOLLOWUP_SIGNAL_PHRASES = {
    "before the discount", "original price", "old price", "how much was",
    "how much is", "how much is it", "how much does it cost",
    "is it still", "does it still", "still available", "still valid",
    "how do i redeem", "redeem this", "redeem it",
    "قبل الخصم", "السعر الاصلي", "السعر الأصلي", "كان بكام",
    "لسه موجود", "لسه شغال", "استخدمه ازاي", "استخدمه إزاي",
    "similar offers", "similar offer", "something similar", "anything similar",
    "same type", "something else like",
    "عروض مشابهة", "حاجة مشابهة", "حاجة زي كده", "زي كده", "زي ده", "نفس النوع",
    "how much",
    "other offers", "more offers", "any other", "do they have more",
    "عروض تانية", "عروض غير", "عندكم عروض", "في عروض تانية",
    "عندهم عروض", "عندهم عروض تانية", "عندهم عروض تانية؟",
    "عندهم حاجات تانية", "عندهم حاجة تانية",
    "عندك عروض", "عندك عروض تانية",
    "عندك حاجات تانية",
    "ايه تاني", "ايه غير", "ايهغير", "تاني شغل",
    "عندو عروض", "عندم عروض",
    "فيه عروض", "في عروض",
    "ahy tany", "ahy tany gher", "ahy gher", "eh tany", "eh gher",
    "3ndhom arou3 tdnya", "3ndhom tdnya", "3ndhom arou3",
    "3ndkom arou3", "3ndak arou3", "3ndo tdnya",
    "fi 3roud", "fi 3roud tdnya", "3roud tanya", "3rouh tdnya",
    "bt'awed arou3", "bt3awed tdnya", "3ayez arou3 tdnya",
    "3ndy", "3andok", "3andkom", "3ndek",
}

_BARE_PRICE_QUESTION_WORDS = {"بكام", "كام", "bkam", "bk3m", "qdam", "f kam"}

# ------------------------------------------------------------------ #
# Deterministic signal helpers (same logic as core/rag_engine)
# ------------------------------------------------------------------ #
def extract_price_range(query: str):
    q = _normalize_arabic(query)
    m = _RANGE_RE.search(q)
    if m:
        nums = [int(g) for g in m.groups() if g and g.isdigit()]
        if len(nums) >= 2:
            return (min(nums[:2]), max(nums[:2]))
    m = _MAX_RE.search(q)
    if m:
        val = next(int(g) for g in m.groups() if g and g.isdigit())
        return (0, val)
    m = _MIN_RE.search(q)
    if m:
        val = next(int(g) for g in m.groups() if g and g.isdigit())
        return (val, float("inf"))
    return None


def _looks_like_comparison(query: str) -> bool:
    return any(w in (query or "").lower() for w in _COMPARISON_WORDS)


def _looks_like_other_offer(query: str) -> bool:
    q = (query or "").lower()
    for p in _OTHER_OFFER_PHRASES:
        if p in q:
            return True
    return any(w in q for w in _OTHER_OFFER_TAIL)


def _same_offer_price_followup(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    return any(rx.search(q) for rx in _SAME_OFFER_ANAPHORA_RES)


def _offers_likely_referenced(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if any(rx.search(q) for rx in _SELF_CONTAINED_EXCEPTION_RES):
        return False
    return any(rx.search(q) for rx in _OFFER_REFERENCE_RES)


def _looks_like_price_only_query(query: str) -> bool:
    if extract_price_range(query) is None:
        return False
    words = re.split(r"[\s؟?!.,،]+", (query or "").lower())
    content = [w for w in words if w and not w.isdigit() and w not in _PRICE_FILTER_WORDS]
    return len(content) == 0


_CHEAPER_WORDS = {
    "ارخص", "أرخص", "رخيص", "رخيصة",
    "اقل سعر", "اقل", "أقل سعر",
    "cheaper", "less expensive", "lower price",
}
_EXPENSIVE_WORDS = {
    "اغلى", "أغلى", "اعلي سعر", "أعلى سعر",
    "اكثر سعر", "أكثر سعر",
    "more expensive", "higher price", "expensive",
}


def _price_direction(query: str) -> str | None:
    """'below' when the user asks for something CHEAPER than the anchor,
    'above' for something more expensive. Only meaningful once the caller has
    established the query REALLY references a shown offer (step 6 gate)."""
    q = (query or "").strip().lower()
    if any(w in q for w in _CHEAPER_WORDS):
        return "below"
    if any(w in q for w in _EXPENSIVE_WORDS):
        return "above"
    return None


# Stage 4: "similar to what you showed" wording. WITHOUT a price direction this
# must resolve to same-merchant ALTERNATIVE offers (the user asked for more of
# the same KIND), never a collapse back onto the already-shown card. Explicit
# cross-merchant asks ("من تاجر تاني") stay out of the reference layer -- they
# are fresh-merchant questions for the planner + grounding, not references.
_SIMILAR_PHRASES = {
    "similar offer", "similar offers", "similar to this", "something similar",
    "anything similar", "same type", "same kind", "like this", "like these",
    "like it", "more like",
    "مشابه", "مشابهة", "مشابهه", "شبه", "شبيه", "شبهه", "شبهها",
    "زي ده", "زي كده", "زي دة", "زيه", "زيها", "مثله", "مثلها",
    "نفس النوع", "نفس الشكل", "حاجة زي", "حاجه زي",
    "عروض مشابهة", "عروض مشابه", "حاجة مشابهة", "حاجه مشابهة",
    "zay dah", "zay deh", "zai dah", "zai deh", "zai", "zay",
    "nfs el no3", "nfs el nawe3",
}


def _similar_text(query: str) -> bool:
    """True when the user's OWN wording asks for alternatives OF THE SAME KIND
    as a shown offer (a "شبهه / نفس النوع / similar" ask). Only consulted after
    the step-6 followup+reference gate already fired, so a bare 'like' can
    never leak in -- there must also be a whole-word anaphora / signal phrase."""
    q = (query or "").strip().lower()
    return any(p in q for p in _SIMILAR_PHRASES)


# Stage 4: pronoun-suffixed similar pointers ("زيها / شبهه / like this one")
# are an anaphora in their own right, so a bare "عايز حاجة زيها بس أغلى" (no
# ده/كده in the message) can still resolve against stored state. Word-boundary
# matched so "تشبهه" / "...شبهها" inside a longer word never triggers.
_SIMILAR_PRONOUN_RE = re.compile(
    r"\b(زيها|زيه|زيو|مزيه|شبهها|شبهو|شبها|شبهه|مثله|مثلها)\b"
    r"|\b(zayaha|zayha|zaiha|zeha|zayo|zayah|nfsaha)\b"
    r"|\b(like it|like him|like her|like this one)\b",
    re.IGNORECASE,
)


def _similar_pronoun_pointer(query: str) -> bool:
    q = (query or "").strip()
    return bool(q) and _SIMILAR_PRONOUN_RE.search(q) is not None


# Stage 4: explicit CROSS-merchant asks. The reference layer deliberately does
# NOT force the same merchant here -- it steps aside (NEW_TOPIC) so the planner
# and grounding decide. (A hard "not merchant X" negation is not modeled;
# that's a documented limitation, but we never silently force a merchant.)
_CROSS_MERCHANT_MARKERS = (
    "من مكان تاني", "من مكان غير", "من تاجر تاني", "من محلات تانية",
    "تاجر تاني", "محل تاني", "محلات تانية", "مكان تاني", "مكان مختلف",
    "شركة تانية", "براند تاني", "من براند تاني", "من محل تاني",
    "غير ال", "غير الشركة", "مش من نفس الشركة",
    "another merchant", "another store", "another brand", "different merchant",
    "other store", "somewhere else", "a different place",
)


def _cross_merchant_explicit(query: str) -> bool:
    q = (query or "").strip().lower()
    return any(p in q for p in _CROSS_MERCHANT_MARKERS)


def _to_float(value):
    try:
        return float(str(value or "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _looks_like_followup_text(query: str) -> bool:
    """True when the query's OWN wording suggests it refers back to something
    shown earlier: a whole-word anaphora pronoun, a bare-price question, one
    of the referring signal phrases, or a price-only filter. Kept identical to
    core.rag_engine._looks_like_followup_text so the ambiguous resolution
    paths only fire when the user is genuinely pointing backwards -- NOT on a
    suffix coincidence like "دة" inside "جديدة"."""
    q = (query or "").lower()
    words = re.split(r"[\s؟?!.,،]+", q)
    has_anaphora = any(w in _ANAPHORA_WORDS for w in words if w)
    has_franco_anaphora = any(w in _FRANCO_ANAPHORA for w in words if w)
    has_bare_price_question = any(w in _BARE_PRICE_QUESTION_WORDS for w in words if w)
    has_signal_phrase = any(p in q for p in _FOLLOWUP_SIGNAL_PHRASES)
    return (has_anaphora or has_franco_anaphora or has_bare_price_question
            or has_signal_phrase or _looks_like_price_only_query(query))


def _extract_ordinals(query: str) -> list:
    q = (query or "").lower()
    found = []
    for word, idx in _ORDINAL_WORDS.items():
        if word in q and idx not in found:
            found.append(idx)
    return found


def _mentioned_merchants(query: str, merchants: list) -> list:
    """True-name mention detection, copied from rag_engine._mentioned_merchants:
    literal word-boundary matches plus config.MERCHANT_ALIASES. `merchants`
    should be sorted longest-first (longer names match before shorter ones
    that happen to be substrings of them)."""
    q = (query or "").lower()
    found = []
    alias_map = {a.lower(): c for a, c in dict(_MERCHANT_ALIASES or {}).items()}
    for m in merchants:
        m_clean = str(m or "").strip()
        if len(m_clean) < 3:
            continue
        m_lower = m_clean.lower()
        pattern = rf"(?:\b|^)(?:و)?{re.escape(m_lower)}(?:\b|$)"
        if re.search(pattern, q):
            found.append(m_clean)
    for alias, canonical in alias_map.items():
        pattern = rf"(?:\b|^)(?:و)?{re.escape(alias)}(?:\b|$)"
        if re.search(pattern, q) and canonical not in found:
            found.append(canonical)
    return found


def _dedup_strs(values: list) -> list:
    seen = set()
    out = []
    for v in (values or []):
        key = str(v or "").strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _metadata(entry: dict) -> dict:
    return entry.get("metadata", {}) if isinstance(entry, dict) else {}


def _entry_id(entry: dict) -> str:
    mid = _metadata(entry)
    return str(mid.get("id") or "") or (
        f"{mid.get('source') or 'doc'}:{mid.get('question') or mid.get('title') or ''}")


def _false_none(reason_code: str, recent_offers: list, **extra) -> dict:
    out = {
        "kind": "none",
        "verdict": "NEW_TOPIC",
        "intent": "",
        "targets": [],
        "target_ids": [],
        "anchor_merchant": None,
        "reason_code": reason_code,
    }
    out.update(extra)
    return out


def resolve_reference(query: str, recent_offers: list,
                      faceted=None) -> dict:
    """Deterministically resolve `query` against the stored last-shown offers.

    Params:
        query: the sanitized user text for THIS turn.
        recent_offers: the actual stored offers shown earlier this session,
            each {"metadata": {...}}, most-recent-first (the engine's
            `recent_offers_full` / memory.SessionMemory.recent()).
        faceted: optional FacetedCatalog; used ONLY to corroborate merchant
            names, never as "memory".

    Returns a dict:
        kind: "none" | "same_offer" | "other_offer" | "compare" | "ordinal"
        verdict: "NEW_TOPIC" | "SAME_OFFER" | "OTHER_OFFER_SAME_SOURCE"
        intent: "same" | "other_offer" | "compare" | "ordinal" | ""
        targets: entries copied straight from `recent_offers`
        target_ids: their metadata ids
        anchor_merchant: merchant name to keep searching when kind=other_offer
        reason_code: which deterministic rule fired

    "none" + NEW_TOPIC means: no stored reference is corroborated -- the query
    is self-contained and normal catalog/FAQ handling should answer it. No LLM
    is ever consulted here.
    """
    q = (query or "").strip()
    if not q or not recent_offers:
        return _false_none("no_state", recent_offers)

    merchants_sorted = ()
    faceted_merchants = set()
    if faceted is not None:
        faceted_merchants = set(getattr(faceted, "merchants", ()) or ())
        merchants_sorted = tuple(sorted(
            (str(m).strip() for m in faceted_merchants if str(m).strip()),
            key=lambda s: (-len(s), s)))

    stored_merchants = {
        str(m).strip() for m in
        (_metadata(o).get("merchant") for o in recent_offers)
        if str(m or "").strip()
    }
    merchants_sorted = merchants_sorted or tuple(
        sorted(stored_merchants, key=lambda s: (-len(s), s)))

    # ---- 0. explicit cross-merchant ask (Stage 4) -----------------------
    # "شبهه بس من مكان تاني / another store": the user is NOT asking for
    # more of the SAME merchant. The reference layer steps aside (NEW_TOPIC)
    # so the planner + grounding decide the merchant -- never a silent force.
    if _cross_merchant_explicit(q):
        return _false_none("cross_merchant_explicit", recent_offers)

    # ---- 1. explicit merchant naming (same as old cascade fast path) ----
    mentioned = _dedup_strs(
        _mentioned_merchants(q, list(merchants_sorted)))
    if faceted is not None:
        try:
            mentioned = _dedup_strs(mentioned + [
                m for m in faceted.resolve_merchants(q)
                if m in (faceted_merchants or stored_merchants)
            ])
        except Exception:  # noqa: BLE001, S110 -- resolver must never crash the agent
            pass
    if mentioned:
        mset = set(mentioned)
        targets = [o for o in recent_offers
                   if str(_metadata(o).get("merchant") or "").strip() in mset]
        if targets and _looks_like_comparison(q):
            # "قارن بين X و Y" where a named merchant is NOT in the
            # stored set is a fresh comparison, not a reference.
            if any(m not in stored_merchants for m in mentioned) or len(targets) < 2:
                return _false_none("compare_fresh_scope", recent_offers)
            return {
                "kind": "compare", "verdict": "SAME_OFFER",
                "intent": "compare", "targets": targets,
                "target_ids": [_entry_id(t) for t in targets],
                "anchor_merchant": None,
                "reason_code": "merchant_compare",
            }
        if targets and _looks_like_other_offer(q):
            anchor = str(_metadata(targets[0]).get("merchant") or "").strip()
            return {
                "kind": "other_offer", "verdict": "OTHER_OFFER_SAME_SOURCE",
                "intent": "other_offer", "targets": targets,
                "target_ids": [_entry_id(t) for t in targets],
                "anchor_merchant": anchor or None,
                "reason_code": "merchant_other",
            }
        if targets:
            return {
                "kind": "same_offer", "verdict": "SAME_OFFER",
                "intent": "same", "targets": targets,
                "target_ids": [_entry_id(t) for t in targets],
                "anchor_merchant": None,
                "reason_code": "merchant",
            }
        # A merchant is named but nothing stored matches it: it may be a fresh
        # merchant ("offers from Zara") -- self-contained, not a reference.
        return _false_none("merchant_not_stored", recent_offers)

    # ---- 2. ordinal pointer ("التاني" / "the first one" / "last") ----
    ordinals = _extract_ordinals(q)
    if ordinals:
        targets = []
        for idx in ordinals:
            try:
                targets.append(recent_offers[idx])
            except IndexError:
                continue
        if targets:
            return {
                "kind": "ordinal", "verdict": "SAME_OFFER",
                "intent": "ordinal", "targets": targets,
                "target_ids": [_entry_id(t) for t in targets],
                "anchor_merchant": None,
                "reason_code": "ordinal",
            }
        return _false_none("ordinal_no_target", recent_offers)

    # ---- 3. bare price filter / same-offer price anaphora ----
    # ("فيه اعلي من 200 جنيه؟", "ده بكام قبل الخصم؟") -- implicitly scoped to
    # what was just shown.
    if _same_offer_price_followup(q) or _looks_like_price_only_query(q):
        return {
            "kind": "same_offer", "verdict": "SAME_OFFER",
            "intent": "same", "targets": [recent_offers[0]],
            "target_ids": [_entry_id(recent_offers[0])],
            "anchor_merchant": None,
            "reason_code": "price_pointer",
        }

    # ---- 4. comparison wording with no merchant/ordinal anchor ----
    # ("شوف الفرق بين العروضين" right after two offers were shown).
    if _looks_like_comparison(q):
        if len(recent_offers) >= 2:
            return {
                "kind": "compare", "verdict": "SAME_OFFER",
                "intent": "compare", "targets": recent_offers[:2],
                "target_ids": [_entry_id(o) for o in recent_offers[:2]],
                "anchor_merchant": None,
                "reason_code": "compare_implied",
            }
        return _false_none("compare_no_partner", recent_offers)

    # ---- 5. other-offer wording without a merchant ("عايز عروض تانية") ----
    if _looks_like_other_offer(q):
        anchor = str(_metadata(recent_offers[0]).get("merchant") or "").strip()
        return {
            "kind": "other_offer", "verdict": "OTHER_OFFER_SAME_SOURCE",
            "intent": "other_offer",
            "targets": list(recent_offers),
            "target_ids": [_entry_id(o) for o in recent_offers],
            "anchor_merchant": anchor or None,
            "reason_code": "other_implied",
        }

    # ---- 6. generic cross-reference pointer ("ده بكام", "العروض دي") ----
    # Gated by _looks_like_followup_text: the substring regexes alone over-trigger
    # on ordinary words ("دة" inside "جديدة"), so we only treat the query as a
    # cross-reference when the user ALSO used a whole-word anaphora / bare-price
    # / signal-phrase. The old cascade sent exactly these ambiguous-but-flagged
    # cases to an LLM; here they resolve deterministically or drop to catalog.
    if (_looks_like_followup_text(q) or _similar_pronoun_pointer(q)) \
        and (_offers_likely_referenced(q) or _similar_pronoun_pointer(q)):
        anchor = _metadata(recent_offers[0])
        direction = _price_direction(q)
        if direction:
            # "العرض ده غالي، هاتلي حاجة شبهه بس أرخص": the reference is real
            # AND the user wants an ALTERNATIVE relative to the shown offer's
            # own price. The engine answers with a search anchored on the
            # STORED merchant + the STORED anchor price as the bound -- never
            # a collapse to a single get_offer card.
            return {
                "kind": "same_offer", "verdict": "SAME_OFFER",
                "intent": "same", "targets": [recent_offers[0]],
                "target_ids": [_entry_id(recent_offers[0])],
                "anchor_merchant": str(anchor.get("merchant") or "").strip() or None,
                "anchor_price": _to_float(anchor.get("price")),
                "price_direction": direction,
                "reason_code": "cross_ref_price",
            }
        if _similar_text(q):
            # "عايز حاجة زي ده / شبهه / نفس النوع" (no price hook): the user
            # wants MORE of the SAME KIND from the same merchant, not the same
            # card again. Same-merchant scope (v1, deliberate): a cross-merchant
            # ask names a different merchant and flows through grounding instead.
            anchor_name = str(anchor.get("merchant") or "").strip() or None
            return {
                "kind": "other_offer", "verdict": "OTHER_OFFER_SAME_SOURCE",
                "intent": "other_offer", "targets": list(recent_offers),
                "target_ids": [_entry_id(o) for o in recent_offers],
                "anchor_merchant": anchor_name,
                "reason_code": "cross_ref_similar",
            }
        return {
            "kind": "same_offer", "verdict": "SAME_OFFER",
            "intent": "same", "targets": [recent_offers[0]],
            "target_ids": [_entry_id(recent_offers[0])],
            "anchor_merchant": None,
            "reason_code": "cross_ref",
        }

    # ---- 7. nothing points at stored state: self-contained, fresh topic ----
    return _false_none("self_contained", recent_offers)


# ------------------------------------------------------------------ #
# Planner-claim corroboration
# ------------------------------------------------------------------ #
def corroborate_references(plan_references: list, query: str,
                           resolved: dict) -> tuple:
    """Cross-check the planner's DECLARED reference list against the user's
    own words AND the deterministic resolution.

    A planner may claim a reference ("هالخصم", "that coupon", an ordinal) to
    justify anchoring onto older context. That claim is only evidence when the
    user actually said the words (query text) and the deterministic resolution
    agrees the turn is a reference. Anything else is dropped and reported, so
    a model continuity story can never steer the turn.

    Returns (kept, dropped): the surviving reference strings and the ones
    rejected, both as clean string lists.
    """
    kept, dropped = [], []
    for token in (plan_references or []):
        t = str(token or "").strip()
        if not t:
            continue
        if resolved.get("kind") != "none" and _spoken(token, query):
            kept.append(t)
        else:
            dropped.append(t)
    return kept, dropped


def _spoken(token: str, query: str) -> bool:
    """True when `token` is literally the user's own text (the query-anchor
    rule used by grounding)."""
    t = str(token or "").strip().lower()
    q = (query or "").lower()
    return bool(t) and t in q