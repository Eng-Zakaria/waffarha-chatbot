"""Stage 1 pre-planner intent router per reports/20260919_stage1_router_design.md
section 3 (+ Gap 2). Decides a deterministic intent label for a turn BEFORE any
planner call, so cheap intents (social, faq, complaint, out-of-scope, ...) never
build a plan prompt and never touch the catalog.

Decision order (locked):
  adversarial -> social (greeting/thanks/smalltalk/farewell, only when no
  offer-topic word) -> faq family (payment_methods/order_status/faq) ->
  complaint -> offer subset (compare/recommend/detail) -> offer_search
  positives (a)-(d) -> out-of-scope veto -> length/noise guard -> residual
  delegate.

Imports directly from core (no agent/facade indirection) -- design section 4.
The catalog corroboration signal (positive "c") is FacetedCatalog.resolve_*,
injected as `catalog` so tests build synthetic FacetedCatalog instances.
"""

import re

from core.greetings import (
    GREETING_PHRASES,
    normalize_router_text,
    social_turn_kind,
)
from core.rag_engine import (
    _looks_like_gibberish,
    _looks_like_injection_attempt,
    _looks_like_out_of_scope,
    _route_faq_topic,
)
from core.rag_perfection import INTENT_OFFER_LOOKUP, classify_intent_robust

# --------------------------------------------------------------------------- #
# Signal sets (all stored NORMALIZED -- matching runs on normalize_router_text)
# --------------------------------------------------------------------------- #

# GLUE: general-knowledge / identity questions with no offer content. Mirror of
# the working-tree agent.engine._OUT_OF_SCOPE_QUESTION_RE; at C1 the engine's
# copy is deleted and this becomes the single owner.
_GENERAL_KNOWLEDGE_RE = re.compile(
    r"(who is|who are|tell me about|what is the capital of|"
    r"من هو|من هي|من هم|مين هو|مين هي|مين هم|ما هي عاصمة|"
    r"من هو رئيس|مين هو رئيس|من هو صاحب|مين هو صاحب)",
    re.IGNORECASE,
)

# Positive (a): bare offer-topic words (design Gap 2). Normalized.
_OFFER_TOPIC_WORDS = frozenset({
    "عروض", "العروض", "عروضا", "offers", "deals",
    "كوبون", "كوبونات", "coupon",
    "خصم", "خصومات", "discount",
    "سعر", "اسعار", "price",
    "تخفيض", "تخفيضات",
})

# Positive (b): explicit-browse phrasing (moved from engine._EXPLICIT_BROWSE_MARKERS,
# stored pre-normalized). Normalized.
_BROWSE_MARKERS = frozenset({
    # English
    "show me", "show all", "show offers", "what do you have",
    "what you have", "all offers", "all the offers", "list offers",
    "list of offers", "browse", "whats available", "what is available",
    "any offers", "do you have", "have any offers", "show me offers",
    "what offers", "see offers", "view offers", "show everything",
    # Arabic (normalized: أ->ا, ة->ه, ى->ي)
    "وريني", "عرضولي", "بينولي", "عايز اشوف", "دلوقتي العروض",
    "كل العروض", "العروض كلها", "اشوف العروض", "شوف العروض",
    "عندك ايه", "عندك اي", "عندكم ايه", "مفيش حاجة", "انت ليك عروض",
    "ما عندك", "ديني", "اريني",
    # Franco
    "waryni", "bynoly", "kol el 3orood", "3orood", "shouf",
})

# offer_recommend (superlative). Normalized.
_SUPERLATIVE_WORDS = frozenset({
    "ارخص", "اغلى", "اعلى خصم", "ارخص حاجة", "اقل سعر",
    "احسن", "افضل", "best", "cheapest", "lowest", "best price",
})

# offer_compare. Normalized.
_COMPARE_WORDS = frozenset({
    "قارن", "الفرق بين", "الفرق", "مقارنة", "ايهما",
    "vs", "versus", "against", "comparison", "compare",
})

# complaint / escalation. Normalized.
_COMPLAINT_WORDS = frozenset({
    "شكوى", "شكوي", "شكاوي", "غلط", "غلطة", "مشكلة", "مشاكل",
    "اشتكي", "اشتكى", "ارضني",
})

# payment_methods keyword tier (after route_faq_topic misses). Normalized.
_PAYMENT_WORDS = frozenset({
    "دفع", "ادفع", "الدفع", "payment", "pay",
    "فودافون كاش", "vodafone cash", "محفظة", "wallet",
    "كارت", "كارتة", "فيزا", "بطاقة", "طرق الدفع", "وسائل الدفع",
    "وسيلة الدفع",
})
# A payment query must be HOW-TO phrased to take the tier; bare "كارت خصم"
# offer chatter must not be swallowed.
_PAYMENT_HOWTO = ("كيف", "ازاي", "ازاى", "طريقة", "how", "ادفع",
                  "الدفع", "اقدر", "means", "بتم", "بيتم", "طرق",
                  "وسائل", "وسيلة", "متاح", "لاستقبال")

# order_status keyword tier. Normalized.
_ORDER_STATUS_WORDS = frozenset({
    "حالة الطلب", "مكان الطلب", "طلبي فين", "طلبي وصل", "الطلب وصل",
    "وصل فين", "فين طلبي", "رقم الطلب", "شحنتي", "تتبع", "الطلب اترسل",
    "jessem", "order status", "tracking", "where is my order",
    "الطلب جاي", "طلبي جاي",
})

_COUPON_ID_RE = re.compile(r"\b\d{4,}\b")


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #

def offer_topic_present(nq: str) -> bool:
    return any(w in nq for w in _OFFER_TOPIC_WORDS)


def _looks_like_browse(nq: str) -> bool:
    return any(m in nq for m in _BROWSE_MARKERS)


def _faq_intent_for_rule(rule_name: str) -> str:
    if rule_name.startswith("status_"):
        return "order_status"
    if rule_name.startswith("pay_"):
        return "payment_methods"
    return "faq"


def intent(query, *, catalog=None, classify=None, _fq=None):
    """Deterministic Stage-1 intent for a single turn. `catalog` is a
    FacetedCatalog (synthetic in tests); `classify` is injectable so tests keep
    this pure; `_fq` overrides the FAQ ruler (tests assert the faq mapping).

    Returns {'intent', 'reason', 'nq', 'detail'}.`
    """
    raw = (query or "").strip()
    if raw == "":
        return {"intent": "unclear", "reason": "guard:empty",
                "nq": "", "detail": None}
    nq = normalize_router_text(raw)
    classifier = classify or classify_intent_robust

    # 1) adversarial -- always first
    if _looks_like_injection_attempt(raw):
        return {"intent": "adversarial", "reason": "injection",
                "nq": nq, "detail": None}

    # 2) social -- greeting/thanks/smalltalk/farewell only when NO offer word
    if not offer_topic_present(nq):
        kind = social_turn_kind(raw)
        if kind in ("greeting", "thanks", "smalltalk", "farewell"):
            return {"intent": kind, "reason": f"social:{kind}",
                    "nq": nq, "detail": None}

    # 3) faq family
    ruler = _fq or _route_faq_topic
    hit = ruler(raw, nq)
    if hit:
        rule_name, faq_id, _ = hit
        intent_name = _faq_intent_for_rule(rule_name)
        return {"intent": intent_name, "reason": f"faq:{rule_name}",
                "nq": nq, "detail": {"faq_id": faq_id}}

    # 4) complaint
    if _any_in(nq, _COMPLAINT_WORDS):
        return {"intent": "complaint", "reason": "complaint",
                "nq": nq, "detail": None}

    # 5) offer subset signals (before generic offer_search positives)
    if _any_in(nq, _COMPARE_WORDS):
        return {"intent": "offer_compare", "reason": "subset:compare",
                "nq": nq, "detail": None}
    if _any_in(nq, _SUPERLATIVE_WORDS):
        return {"intent": "offer_recommend", "reason": "subset:recommend",
                "nq": nq, "detail": None}

    # 5b) offer_detail: coupon id, or a lone merchant mention
    detail = _entity_detail(raw, catalog, nq)
    if (_COUPON_ID_RE.search(raw)
            or (detail["merchants"] and len(detail["merchants"]) == 1
                and not detail["category"] and not detail["product"]
                and not offer_topic_present(nq))):
        return {"intent": "offer_detail", "reason": "subset:detail",
                "nq": nq, "detail": detail}

    # 6) offer_search positives (a)-(d)
    oos_match = bool(_GENERAL_KNOWLEDGE_RE.search(raw)
                     or _looks_like_out_of_scope(raw))
    positive = None
    if offer_topic_present(nq):
        positive = ("positive:a", None)
    elif _looks_like_browse(nq):
        positive = ("positive:b", None)
    elif detail["merchants"] or detail["category"] or detail["product"]:
        positive = ("positive:c", detail)
    else:
        verdict = classifier(raw)
        # (d) must never fire on a GK/identity/domain question: the classifier's
        # no-client heuristic otherwise falls back to OFFER_LOOKUP for "من هو
        # رئيس مصر" and (d) would swallow it before the OOS veto below runs.
        # The OOS veto after this block then catches the truly out-of-scope.
        # Also never on short all-Latin noise ("asdf" reads as a word because
        # `a` is a vowel, so the gibberish check alone lets it through).
        if (verdict == INTENT_OFFER_LOOKUP and not oos_match
                and len(nq) >= 4 and not _looks_like_gibberish(raw)
                and not _short_latin_noise(raw, nq)):
            positive = ("positive:d", None)
    if positive:
        return {"intent": "offer_search", "reason": positive[0],
                "nq": nq, "detail": positive[1]}

    # 7) out-of-scope veto (GK/identity + broader domain), pre-planner
    if oos_match:
        return {"intent": "out_of_scope", "reason": "oos",
                "nq": nq, "detail": None}

    # 8) length / noise guard
    if not nq or len(nq) < 2 or _looks_like_gibberish(raw) \
            or _short_latin_noise(raw, nq):
        return {"intent": "unclear", "reason": "guard:noise",
                "nq": nq, "detail": None}

    # 9) residual -- real question, no strong signal: let the planner decide
    return {"intent": "delegate", "reason": "delegate:residual",
            "nq": nq, "detail": detail}


def _any_in(nq: str, words) -> bool:
    return any(w in nq for w in words)


def _short_latin_noise(raw: str, nq: str) -> bool:
    """<=4 chars, all Latin, no digits -> ambiguous noise ("asdf", "xcvb").
    Genuine short brands ("kfc") are caught earlier by positive (c), so this
    only ever fires when the catalog corroborated nothing."""
    if not nq or any("\u0600" <= ch <= "\u06FF" for ch in raw):
        return False
    return len(nq) <= 4 and bool(nq)


def _entity_detail(raw, catalog, nq) -> dict:
    merchants = []
    category = None
    product = None
    if catalog is not None:
        try:
            merchants = catalog.resolve_merchants(raw) or []
        except Exception:
            merchants = []
        try:
            category = catalog.resolve_category(raw)
        except Exception:
            category = None
        try:
            product = catalog.resolve_product(raw)
        except Exception:
            product = None
    return {"merchants": merchants, "category": category,
            "product": product}


__all__ = [
    "GREETING_PHRASES",
    "intent",
    "offer_topic_present",
    "_OFFER_TOPIC_WORDS",
    "_BROWSE_MARKERS",
    "_GENERAL_KNOWLEDGE_RE",
]