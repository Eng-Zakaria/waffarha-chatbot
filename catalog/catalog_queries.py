"""
Live catalog query service for the Waffarha Assistant.

Answers customer catalog questions ("KFC offers", "under 200 EGP", "cheapest offer",
"where is KFC branch", "Ramadan deals") by querying ClickHouse LIVE --
main.dim_offers joined to main.dim_partners and main.dim_type_price --
scoped to active offers only.

This is deliberately SEPARATE from the static RAG index (rag_engine.py):
catalog data is dynamic and public, so it should be queried live per request
for fresh prices, availability, and merchant info. The static index keeps
answering FAQ/how-to questions; this module handles "what's available now".

SECURITY: every SQL statement here is parameterized and read-only (SELECT only).
No user input is ever interpolated into SQL -- all filters use named parameters.
"""

import logging
import re
from typing import Optional, List, Dict
import sys
import os

from core import config

# Add parent directory to path for session_manager import
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from session.session_manager import SessionManager

log = logging.getLogger("waffarha-app")

CATALOG_ERROR = {
    "en": "I had trouble reaching the offers catalog right now. Please try again in a moment.",
    "ar": "حصلت مشكلة في الوصول لدليل العروض دلوقتي. جرب تاني بعد شوية.",
}

# --- Intent detection patterns ---------------------------------------------

# Merchant-specific: "KFC offers", "عروض كنتاكي", "deals from Pizza Hut"
# These patterns ONLY match when the query is explicitly asking for offers/deals/coupons
# ALSO matches merchant + price/discount queries (e.g., "KFC price", "KFC discount", "KFC بكام")
# ORDER MATTERS: More specific patterns should come BEFORE more general ones
_MERCHANT_PATTERNS = [
    # "offers from KFC", "deals at Pizza Hut", "coupons by X"
    r"(?:offers?|deals?|coupons?)\s+(?:from|at|by|of|for)\s+(.+)",  # English
    r"(?:عروض|خصومات|كوبونات)\s+(?:من|عند|لـ|ل|بـ)\s+(.+)",  # Arabic
    r"what(?:'s| is)\s+(?:the\s+)?(?:offers?|deals?|coupons?)\s+(?:from|at|by)\s+(.+)",
    r"show\s+me\s+(?:offers?|deals?|coupons?)\s+(?:from|at|by)\s+(.+)",
    r"عروض\s+(.+)",  # "عروض كنتاكي"
    r"عند\s+(.+)\s+عروض",  # "عند كنتاكي عروض"
    # English: "KFC offers", "Pizza Hut deals" (merchant + offers/deals/coupons, no preposition)
    # Only match at START of query or after "for" - not "tell me about X"
    r"^(?:show\s+me\s+)?([A-Za-z&'’\s]{2,})\s+(?:offers?|deals?|coupons?)\b",
    # Arabic: "كنتاكي عروض" (merchant + عروض)
    r"^([؀-ۿ\s]{2,})\s+عروض\b",
    # NEW: Arabic "كم سعر عرض KFC" / "بكام عرض KFC" / "خصم KFC" - MORE SPECIFIC, check first
    # Use word boundaries and stop at punctuation/prepositions
    r"كم\s+سعر\s+عرض\s+([؀-ۿA-Za-z&'’\s]{2,})(?:\s|[؟?!.,،]|$)",
    r"بكام\s+عرض\s+([؀-ۿA-Za-z&'’\s]{2,})(?:\s|[؟?!.,،]|$)",
    r"خصم\s+([؀-ۿA-Za-z&'’\s]{2,})(?:\s|[؟?!.,،]|$)",
    # NEW: Missing "بكام سعر عرض X" variations - extra "سعر" between بكام/عرض
    # User-tested query "بكام سعر عرض كنتاكي؟" was previously falling through
    # to RAG and returning "no offers found" even though KFC has active offers.
    r"بكام\s+سعر\s+عرض\s+([؀-ۿA-Za-z&'’\s]{2,})(?:\s|[؟?!.,،]|$)",
    r"بكام\s+سعر\s+([؀-ۿA-Za-z&'’\s]{2,})(?:\s|[؟?!.,،]|$)",
    # NEW: Reversed word order "سعر عرض X بكام" / "عرض X بكام"
    r"سعر\s+عرض\s+([؀-ۿA-Za-z&'’\s]{2,})\s+بكام\b",
    r"عرض\s+([؀-ۿA-Za-z&'’\s]{2,})\s+بكام\b",
    # NEW: Mixed Arabic/English "discount بتاع KFC" / "price بتاع KFC"
    r"(?:discount|price|cost|بكام|السعر|الخصم)\s+(?:بتاع|بتاع)\s+([؀-ۿA-Za-z&'’\s]{2,})(?:\s|[؟?!.,،]|$)",
    # NEW: Merchant + price/discount queries - "KFC price", "KFC discount", "KFC بكام"
    r"^([A-Za-z&'’\s]{2,})\s+(?:price|cost|discount|بكام|السعر|الخصم)\b",
    r"([A-Za-z&'’\s]{2,})\s+(?:price|cost|discount|بكام|السعر|الخصم)\b",
    # General Arabic "كم/السعر/الخصم" at start - LESS SPECIFIC, check last
    r"^([؀-ۿ\s]{2,})\s+(?:بكام|السعر|الخصم)\b",
]
_MERCHANT_RE = [re.compile(p, re.IGNORECASE) for p in _MERCHANT_PATTERNS]

# Price ceiling: "under 200 EGP", "below 150", "تحت 200 جنيه"
_PRICE_CEILING_PATTERNS = [
    r"(?:under|below|less than|up to|max\s*(?:price)?)\s*(\d+(?:\.\d+)?)\s*(?:egp|le|l\.e|جنيه|ج\.م)?",
    r"(?:تحت|اقل من|أقل من|حد اقصى|حد أقصى)\s*(\d+(?:\.\d+)?)",
]
_PRICE_CEILING_RE = [re.compile(p, re.IGNORECASE) for p in _PRICE_CEILING_PATTERNS]

# Price floor: "over 500", "above 300", "فوق 500"
_PRICE_FLOOR_PATTERNS = [
    r"(?:over|above|more than|min\s*(?:price)?)\s*(\d+(?:\.\d+)?)\s*(?:egp|le|l\.e|جنيه|ج\.م)?",
    r"(?:فوق|اكثر من|أكثر من|اعلى من|أعلى من|حد ادنى|حد أدنى)\s*(\d+(?:\.\d+)?)",
]
_PRICE_FLOOR_RE = [re.compile(p, re.IGNORECASE) for p in _PRICE_FLOOR_PATTERNS]

# Price range: "between 100 and 300", "100 to 300", "من 100 لـ 300"
_PRICE_RANGE_PATTERNS = [
    r"(?:between|from)\s*(\d+(?:\.\d+)?)\s*(?:and|to|-)\s*(\d+(?:\.\d+)?)\s*(?:egp|le|l\.e|جنيه|ج\.م)?",
    r"(\d+(?:\.\d+)?)\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*(?:egp|le|l\.e|جنيه|ج\.م)?",
    r"(?:من|مِن)\s*(\d+(?:\.\d+)?)\s*(?:لـ|ل|الي|حتي|إلى|-)\s*(\d+(?:\.\d+)?)",
]
_PRICE_RANGE_RE = [re.compile(p, re.IGNORECASE) for p in _PRICE_RANGE_PATTERNS]

# Superlative: "cheapest", "most expensive", "highest discount"
# (reusing rag_engine's _looks_like_superlative_price_query logic)
_SUPERLATIVE_PATTERNS = [
    r"\b(?:cheapest|lowest price|least expensive|lowest priced|ارخص|أرخص)\b",
    r"\b(?:most expensive|highest price|priciest|اغلى|أغلى)\b",
    r"\b(?:highest discount|biggest discount|most discount|maximum discount|اعلى خصم|أعلى خصم|اكبر خصم|أكبر خصم)\b",
]
_SUPERLATIVE_RE = [re.compile(p, re.IGNORECASE) for p in _SUPERLATIVE_PATTERNS]

# Location/contact: "where is KFC", "KFC phone", "KFC address", "أين كنتاكي"
_LOCATION_PATTERNS = [
    r"(?:where\s+is|location of|address of|branch of|phone|number|contact)\s+(.+)",
    r"(.+)\s+(?:address|phone|number|location|branch|contact)",
    r"(?:أين|عنوان|فرع|تليفون|موبايل|اتصل|أرقام|مكان)\s+(.+)",
    r"(.+)\s+(?:أين|عنوان|فرع|تليفون|موبايل|اتصل)",
]
_LOCATION_RE = [re.compile(p, re.IGNORECASE) for p in _LOCATION_PATTERNS]

# Tags: "Ramadan offers", "hot deals", "limited time", "عروض رمضان"
_TAG_PATTERNS = [
    r"(?:ramadan|رمضان)\s*(?:offers?|deals?|عروض)?",
    r"(?:hot\s+deal|عرض\s+ساخن)",
    r"(?:limited\s+time|عرض\s+لفترة\s+محدودة|فترة\s+محدودة)",
    r"(?:feast|eid|عيد)\s*(?:offers?|deals?|عروض)?",
    r"(?:valentine|فالنتاين)\s*(?:offers?|deals?|عروض)?",
    r"(?:delivery|توصيل)\s*(?:offers?|deals?|عروض)?",
]
_TAG_RE = [re.compile(p, re.IGNORECASE) for p in _TAG_PATTERNS]

# Multi-merchant: handled by _mentioned_merchants from rag_engine (2+ merchants named)

# Comparison patterns: "compare X and Y", "X vs Y", "difference between X and Y"
_COMPARE_PATTERNS = [
    r"(?:compare|compare the)\s+(.+?)\s+(?:and|vs|versus)\s+(.+)",
    r"(.+?)\s+(?:vs|versus)\s+(.+?)\s+(?:offers?|deals?)",
    r"(?:which is better|ايهم أفضل)\s+(.+?)\s+(?:or|ولا)\s+(.+)",
    r"(?:مقارنة بين|compare between)\s+(.+?)\s+(?:و|and)\s+(.+)",
    r"(?:difference between|الفرق بين)\s+(.+?)\s+(?:و|and)\s+(.+)",
]
_COMPARE_RE = [re.compile(p, re.IGNORECASE) for p in _COMPARE_PATTERNS]

# --- Places (main_eg dim_place, verified live 2026-09-28) --------------------
# {place_id: (name_en, name_ar)}. dim_offers.place_id -> dim_place.place_id.
# Live catalog today is ~all Cairo, but the filter is general so answers stay
# honest as coverage diversifies. Inactive places (status 0) kept for matching
# so "Mansoura" resolves then honestly reports no live offers.
PLACES = {
    1: ("Cairo", "القاهرة"), 2: ("Alexandria", "الاسكندرية"),
    3: ("Alex-Cairo", "القاهرة - اسكندرية"), 4: ("Beni Suef", "بني سويف"),
    5: ("Suez", "السويس"), 6: ("Sharkeya", "الشرقية"),
    7: ("Beheira", "البحيرة"), 8: ("Sharm Sheikh", "شرم الشيخ"),
    9: ("Abo Zaabal", "ابو زعبل"), 10: ("Sohag", "سوهاج"),
    11: ("Port Said", "بورسعيد"), 12: ("Garbeya", "الغربية"),
    13: ("Benha", "بنها"), 14: ("Mansoura", "المنصورة"),
    15: ("Delta", "الدلتا"), 16: ("Hurghada", "الغردقة"),
    17: ("Damietta", "دمياط"), 18: ("Fayoum", "الفيوم"),
    19: ("Assiut", "أسيوط"), 20: ("Minya", "المنيا"),
    21: ("Ismailia", "الاسماعيلية"), 22: ("Mersa Matruh", "مرسى مطروح"),
    23: ("Online Store", "أونلاين ستور"), 24: ("Shibin El Kom", "شبين الكوم"),
    25: ("Seasonal branches", "فروع موسمية"), 26: ("Kafr El-Sheikh", "كفر الشيخ"),
    27: ("Qena", "قنا"), 28: ("Monofiya", "المنوفية"),
    29: ("Qalubiya", "القليوبية"), 30: ("Luxor", "الأقصر"),
    31: ("All Egypt Branches", "جميع فروع مصر"), 32: ("Dakahlia", "الدقهلية"),
    33: ("South Sinai", "جنوب سيناء"), 34: ("Aswan", "أسوان"),
    35: ("Red Sea", "البحر الأحمر"), 36: ("Tanta", "طنطا"),
    37: ("Siwa", "سيوة"), 38: ("New Capital", "العاصمة الإدارية"),
    39: ("Cairo test", "القاهرة تيست"), 40: ("North Coast", "الساحل الشمالي"),
    41: ("Giza", "الجيزة"),
}

# Neighborhood (dim_location) -> place_id for the districts customers actually
# name. dim_location has 278 rows with no FK to offers, so this map is for
# *understanding* ("I live in Maadi" -> Cairo) while filtering stays on place_id.
# EN + AR spellings verified against live dim_location 2026-09-28.
NEIGHBORHOOD_TO_PLACE = {
    # Cairo districts
    "maadi": 1, "المعادي": 1, "zamalek": 1, "الزمالك": 1,
    "heliopolis": 1, "مصر الجديدة": 1, "nasr city": 1, "مدينة نصر": 1,
    "new cairo": 1, "القاهرة الجديدة": 1, "5th settlement": 1, "التجمع الخامس": 1,
    "sheikh zayed": 1, "الشيخ زايد": 1, "mohandessin": 1, "mohandissen": 1,
    "المهندسين": 1, "dokki": 1, "الدقى": 1, "agouza": 1, "العجوزة": 1,
    "october": 1, "أكتوبر": 1, "6th of october": 1, "السادس من أكتوبر": 1,
    "shoubra": 1, "شبرا": 1, "helwan": 1, "hilwan": 1, "حلوان": 1,
    "ain shams": 1, "عين شمس": 1, "matareya": 1, "المطرية": 1,
    "rehab": 1, "الرحاب": 1, "madinaty": 1, "مدينتى": 1, "shorouk": 1, "الشروق": 1,
    "mokattam": 1, "المقطم": 1, "haram": 1, "الهرم": 1, "faisal": 1, "فيصل": 1,
    "giza": 41, "الجيزة": 41,
    # Alexandria districts
    "smouha": 2, "سموحة": 2, "miami": 2, "ميامى": 2, "montaza": 2, "montazah": 2,
    "المنتزة": 2, "المنتزه": 2, "sidi gaber": 2, "سيدى جابر": 2,
    "stanley": 2, "stanly": 2, "ستانلى": 2, "agamy": 2, "العجمي": 2,
    "raml station": 2, "محطة الرمل": 2, "bahari": 2, "بحري": 2,
    "mandara": 2, "المندرة": 2, "asafra": 2, "العصافرة": 2,
}


def _ar_contains(name: str, q: str) -> bool:
    """Arabic match tolerant of the definite article on either side:
    'الاسكندرية' matches 'اسكندرية' and vice versa."""
    n = (name or "").strip()
    if not n:
        return False
    if n in q:
        return True
    stem = n[2:] if n.startswith("ال") and len(n) > 3 else n
    if stem and stem in q:
        return True
    return ("ال" + n) in q


def _detect_place(query: str):
    """Returns (place_id, name_en, name_ar) or None. AR names match by
    substring; short EN names need token boundaries (same rule as merchants)."""
    q = query or ""
    ql = q.lower()
    for pid, (en, ar) in PLACES.items():
        if _ar_contains(ar, q):
            return (pid, en, ar)
        enl = en.lower()
        if len(enl) <= 5:
            if re.search(r"(?<![a-z])" + re.escape(enl) + r"(?![a-z])", ql):
                return (pid, en, ar)
        elif enl in ql:
            return (pid, en, ar)
    for name, pid in NEIGHBORHOOD_TO_PLACE.items():
        if any("\u0600" <= ch <= "\u06ff" for ch in name):
            hit = _ar_contains(name, q)
        else:
            hit = name.lower() in ql
        if hit:
            en, ar = PLACES[pid]
            return (pid, en, ar)
    return None


# Place intent: "offers in Cairo", "عروض اسكندرية", "near Maadi", "in Giza"
_PLACE_PATTERNS = [
    r"(?:offers?|deals?|coupons?|عروض|خصومات)\s+(?:in|at|near|around|فى|في|قريب من)\s+(.+)",
    r"(?:in|at|near|around|فى|في)\s+(.+?)\s+(?:offers?|deals?|عروض)",
    r"^(?:any|anywhere|فيه|فى|عايز|عاوز|محتاج)\b.*\b(?:in|فى|في)\b(.+)",
]
_PLACE_RE = [re.compile(p, re.IGNORECASE) for p in _PLACE_PATTERNS]

# Time intent: ending soon / starting soon / expiring
_ENDING_SOON_PATTERNS = [
    r"\bending\s+soon\b", r"\bexpires?\s+soon\b", r"\blast\s+chance\b",
    r"\bthis\s+week\b", r"هيخلص\s+قريب|هتخلص\s+قريب|عروض\s+بتنتهي|قربت\s+تخلص|آخر\s+فرصة",
]
_ENDING_SOON_RE = [re.compile(p, re.IGNORECASE) for p in _ENDING_SOON_PATTERNS]

_STARTING_SOON_PATTERNS = [
    r"\bstarting\s+soon\b", r"\bcoming\s+soon\b", r"\bupcoming\b", r"\bnew\s+offers\b",
    r"هيبدأ\s+قريب|هتبدأ\s+قريب|عروض\s+جديدة|عروض\s+جاية",
]
_STARTING_SOON_RE = [re.compile(p, re.IGNORECASE) for p in _STARTING_SOON_PATTERNS]

# --- Unsupported patterns (honestly refused) ---
_UNSUPPORTED_PATTERNS = [
    r"\b(my\s+(coupons?|orders?|purchases?|account|wallet|cashback|balance|points))\b",
    r"كوبوناتي|طلباتي|حسابي|محفظتي|كاش\s*باك|رصيدي|نقاطي",
    r"\b(shipping|tracking|track)\b",
    r"\b(delivery|توصيل|شحن|تتبع)\s+(?:status|tracking|track|حالة|تتبع)\b",
]

# FAQ/how-to patterns that should NOT go to catalog queries
# These are informational questions, not offer lookups
# NOTE: Merchant-specific price/discount queries ("كم سعر عرض KFC", "ماكدونالدز بكام")
# are catalog queries - they need fresh data from ClickHouse, not FAQ answers.
# The patterns below ONLY match GENERIC questions without a merchant name.
# IMPORTANT: Superlative queries ("cheapest", "most expensive", "highest discount")
# are checked FIRST in _detect_intent, so they bypass FAQ check here.
# Also, we exclude any query containing superlative terms from FAQ classification.
_FAQ_PATTERNS = [
    r"\b(how\s+(do|can|to)\s+(i|you|we))\b",
    # "what is/are" - but NOT when the question contains superlative terms anywhere
    # Matches "what is X" but fails if query contains cheapest/most expensive/highest discount etc.
    r"(?:(?!cheapest|most\s+expensive|highest\s+discount|ارخص|أرخص|أغلى|اعلى\s+خصم|أعلى\s+خصم).)*\bwhat\s+(is|are)\b(?!.*\b(?:cheapest|most\s+expensive|highest\s+discount|ارخص|أرخص|أغلى|اعلى\s+خصم|أعلى\s+خصم)\b)",
    r"\bhow\s+does\b",
    r"\b(can|could)\s+(i|you)\b",
    r"\bregister|sign.?up|login|log.?in|create\s+account\b",
    r"\bpay\s+(bill|electricity|phone|internet)\b",
    r"\brefund\s+(policy|how)\b",
    r"\bcashback\s+(policy|how)\b",
    r"\bremove\s+(card|bank)\b",
    r"\bgift\s+voucher\b",
    r"\babout\s+waffarha\b",
    r"\bwhat\s+is\s+waffarha\b",
    r"\bprivacy\s+(policy|information)\b",
    # Generic "how much" without merchant - handled by follow-up/memory or FAQ
    r"^\s*(?:how\s+much|بكام|كام)\s*[؟?!.,]*\s*$",
    # Generic "what's the price/discount" without merchant
    r"^\s*(?:what'?s\s+the\s+(?:discount|price)|بكام\s+ال(?:سعر|خصم)?)",
    # Generic "tell me about" without specific merchant/offer
    r"\btell\s+me\s+about\s+(?!.*(?:offer|deal|coupon|عرض|كوبون|خصم)).+",
    r"ازاي\s+(اشترى|ادفع|استخدم|الغي|احذف|اسجل)",
    r"كيفية\s+(الشراء|الدفع|الاستخدام|الغاء|حذف|التسجيل)",
    r"معلومات\s+(عن|خصوصية|سياسة)",
    r"ايه\s+(هي|هو)\s+وفرها",
    r"سياسة\s+(الخصوصية|الاسترداد|الكاش\s*باك)",
    # Arabic FAQ patterns - specific questions that should go to FAQ not catalog
    r"ممكن\s+ادفع\s+فاتورة\s+\w+",
    r"إيه\s+سياسة\s+الاسترجاع",
    r"أدفع\s+(بـ|ب)\s+\w+\s+كاش",
    r"أدفع\s+(بـ|ب)\s+فودافون",
    r"أدفع\s+(بـ|ب)\s+سهولة",
    r"أدفع\s+(بـ|ب)\s+فرصة",
]
_FAQ_RE = [re.compile(p, re.IGNORECASE) for p in _FAQ_PATTERNS]
_UNSUPPORTED_RE = [re.compile(p, re.IGNORECASE) for p in _UNSUPPORTED_PATTERNS]


def is_catalog_query(query: str) -> bool:
    """Returns True if the query looks like a catalog question (not personal, not FAQ)."""
    q = (query or "").lower()

    # Explicitly NOT catalog: personal queries
    from personal.personal_queries import is_personal_query
    if is_personal_query(q):
        return False

    # Explicitly NOT catalog: unsupported (personal data disguised)
    if any(r.search(q) for r in _UNSUPPORTED_RE):
        return False

    # FAST PATH: Superlative queries ("cheapest", "most expensive", "highest discount")
    # are ALWAYS catalog queries - check before FAQ patterns to avoid false negatives
    if any(r.search(q) for r in _SUPERLATIVE_RE):
        return True

    # FAST PATH: time-sensitive queries are ALWAYS catalog queries
    if any(r.search(q) for r in _ENDING_SOON_RE + _STARTING_SOON_RE):
        return True

    # Place queries ("offers in Cairo", "عروض اسكندرية") need live data
    if _detect_place(query) is not None:
        return True

    # Explicitly NOT catalog: FAQ/how-to questions
    if any(r.search(q) for r in _FAQ_RE):
        return False

    # Catalog patterns
    return any(
        r.search(q) for r in
        _MERCHANT_RE + _PRICE_CEILING_RE + _PRICE_FLOOR_RE + _PRICE_RANGE_RE +
        _SUPERLATIVE_RE + _LOCATION_RE + _TAG_RE + _COMPARE_RE + _PLACE_RE
    )


def _is_unsupported(q: str) -> bool:
    return any(r.search(q) for r in _UNSUPPORTED_RE)


def _detect_intent(query: str) -> dict:
    """Extracts structured intent from a catalog query."""
    q = query or ""
    q_lower = q.lower()

    intent = {
        "type": None,
        "merchant": None,
        "merchants": [],  # For comparison queries
        "price_min": None,
        "price_max": None,
        "direction": None,  # "min", "max", "max_discount"
        "tag": None,
        "location": None,
        "place_id": None,  # dim_place id for "offers in <city>" queries
        "days": None,  # time window for ending_soon
    }

    # Time-sensitive queries first (never hijacked by FAQ/merchant guards)
    for re_obj in _ENDING_SOON_RE:
        if re_obj.search(q):
            intent["type"] = "ending_soon"
            intent["days"] = 7
            return intent
    for re_obj in _STARTING_SOON_RE:
        if re_obj.search(q):
            intent["type"] = "starting_soon"
            return intent

    # Place queries ("offers in Cairo", "عروض اسكندرية") before merchant
    # (city names can overlap merchant text; the place wins when the query
    # is offer-seeking and names a known city/district).
    _place = _detect_place(q)
    if _place is not None and (
        any(r.search(q) for r in _PLACE_RE + _LOCATION_RE)
        or re.search(r"offers?|deals?|coupons?|عروض|خصومات|كوبونات", q, re.IGNORECASE)
    ):
        # "KFC offers in Cairo" names both: keep the merchant intent and
        # carry the city as a filter instead of dropping the merchant.
        # A merchant capture that IS the city name (ال-tolerant) is not a
        # merchant at all — fall through to the place intent below.
        _pen, _par = PLACES[_place[0]]

        def _norm(s: str) -> str:
            s = (s or "").strip().lower()
            return s[2:] if s.startswith("ال") and len(s) > 3 else s

        _city_names = {_norm(_pen), _norm(_par)}
        for re_obj in _MERCHANT_RE:
            m = re_obj.search(q)
            if m:
                merchant = re.sub(r"[؟?!.,،;:\s]+$", "", m.group(1).strip()).strip()
                if merchant and _norm(merchant) not in _city_names:
                    intent["type"] = "merchant"
                    intent["merchant"] = merchant
                    intent["place_id"] = _place[0]
                    return intent
        intent["type"] = "place"
        intent["place_id"] = _place[0]
        return intent

    # Check comparison intent first
    for re_obj in _COMPARE_RE:
        match = re_obj.search(q)
        if match:
            intent["type"] = "compare"
            intent["merchants"] = [match.group(1).strip(), match.group(2).strip()]
            return intent

    # Check superlative first (cheapest/most expensive/highest discount)
    for i, re_obj in enumerate(_SUPERLATIVE_RE):
        if re_obj.search(q):
            if i == 0:
                intent["type"] = "superlative"
                intent["direction"] = "min"
            elif i == 1:
                intent["type"] = "superlative"
                intent["direction"] = "max"
            elif i == 2:
                intent["type"] = "superlative"
                intent["direction"] = "max_discount"
            return intent

    # Check tags (Ramadan, hot deals, limited time, etc.) - before merchant
    for re_obj in _TAG_RE:
        if re_obj.search(q):
            # Map tag pattern to special_display value
            tag_map = {
                "ramadan": "ramadan",
                "رمضان": "ramadan",
                "hot": "hot_deals",
                "ساخن": "hot_deals",
                "limited": "limited_time",
                "محدودة": "limited_time",
                "feast": "feast",
                "eid": "feast",
                "عيد": "feast",
                "valentine": "valentine_offers",
                "فالنتاين": "valentine_offers",
                "delivery": "delivery_section",
                "توصيل": "delivery_section",
            }
            matched_text = re_obj.search(q).group(0).lower()
            for key, value in tag_map.items():
                if key in matched_text:
                    intent["type"] = "tag"
                    intent["tag"] = value
                    return intent

    # Check merchant-specific
    for re_obj in _MERCHANT_RE:
        m = re_obj.search(q)
        if m:
            intent["type"] = "merchant"
            merchant = m.group(1).strip()
            # Clean trailing punctuation that may have been captured
            merchant = re.sub(r"[؟?!.,،;:\s]+$", "", merchant).strip()
            intent["merchant"] = merchant
            return intent

    # Check price ceiling
    for re_obj in _PRICE_CEILING_RE:
        m = re_obj.search(q)
        if m:
            intent["type"] = "price_ceiling"
            intent["price_max"] = float(m.group(1))
            return intent

    # Check price floor
    for re_obj in _PRICE_FLOOR_RE:
        m = re_obj.search(q)
        if m:
            intent["type"] = "price_floor"
            intent["price_min"] = float(m.group(1))
            return intent

    # Check price range
    for re_obj in _PRICE_RANGE_RE:
        m = re_obj.search(q)
        if m:
            intent["type"] = "price_range"
            intent["price_min"] = float(m.group(1))
            intent["price_max"] = float(m.group(2))
            return intent

    # Check location/contact
    for re_obj in _LOCATION_RE:
        m = re_obj.search(q)
        if m:
            intent["type"] = "location"
            intent["merchant"] = m.group(1).strip()
            return intent

    return intent


# --- SQL Query Builders ----------------------------------------------------
# NOTE (main_eg phase): table names go through config.ch_table() so
# CLICKHOUSE_DATABASE=main_eg works with zero code changes. Never hardcode
# `main.` in new queries.

def _base_select() -> str:
    db = config.CLICKHOUSE_DATABASE
    return f"""
    SELECT
        o.offer_id, o.part_id, o.section_id, o.place_id,
        o.mobile_offer_title_en, o.mobile_offer_title_ar,
        o.offer_brief_en, o.offer_brief_ar,
        o.actual_value, o.offer_value, o.offer_discount,
        o.offer_expire_date, o.coupon_expire_date, o.offer_status,
        o.offer_fineprint_en, o.offer_fineprint_ar,
        o.waffarha_advice_en, o.waffarha_advice_ar,
        o.special_display, o.offer_sold_coponos,
        o.rate, o.rate_count,
        p.part_name_en, p.part_name_ar,
        p.part_address_en, p.part_address_ar,
        p.part_tel, p.part_tel2, p.part_tel3, p.part_tel4,
        p.part_website, p.part_facebook, p.part_facebook2,
        p.instagram, p.twitter, p.youtube,
        p.part_glat, p.part_glng,
        p.work_time_en, p.work_time_ar,
        p.location_en, p.location_ar,
        pl.place_name_en, pl.place_name_ar
    FROM {db}.dim_offers o
    LEFT JOIN {db}.dim_partners p ON o.part_id = p.part_id
    LEFT JOIN {db}.dim_place pl ON pl.place_id = o.place_id
    WHERE o.deleted_at IS NULL
      AND o.offer_status = 'active'
      AND (p.status = 'active' OR p.status IS NULL)
"""


# Kept for backward compat (tests import it); resolved dynamically.
_BASE_SELECT = _base_select()

def _build_merchant_filter(merchant: str, lang: str) -> tuple[str, dict]:
    """Builds WHERE clause for merchant filter with fuzzy/alias matching."""
    # Use MERCHANT_ALIASES from config to resolve known aliases
    merchant_clean = merchant.strip().lower()
    canonical = config.MERCHANT_ALIASES.get(merchant_clean, merchant)

    # Use ILIKE for case-insensitive match, with % wildcards for partial matches
    # Try exact match on part_name first, then partial
    where = """
      AND (LOWER(p.part_name_en) = LOWER(%(merchant)s)
           OR LOWER(p.part_name_ar) = LOWER(%(merchant)s)
           OR LOWER(p.part_name_en) LIKE LOWER(%(merchant_partial)s)
           OR LOWER(p.part_name_ar) LIKE LOWER(%(merchant_partial)s))
    """
    params = {
        "merchant": canonical,
        "merchant_partial": f"%{canonical}%",
    }
    return where, params


def _build_price_filter(price_min: Optional[float], price_max: Optional[float]) -> tuple[str, dict]:
    """Builds WHERE clause for price range on actual_value."""
    where_parts = []
    params = {}

    if price_min is not None:
        where_parts.append("AND o.actual_value >= %(price_min)s")
        params["price_min"] = price_min

    if price_max is not None:
        where_parts.append("AND o.actual_value <= %(price_max)s")
        params["price_max"] = price_max

    return (" " + " ".join(where_parts), params) if where_parts else ("", {})


def _build_tag_filter(tag: str) -> tuple[str, dict]:
    """Builds WHERE clause for special_display tag."""
    # special_display is comma-separated; match as a tag
    where = "AND o.special_display LIKE %(tag)s"
    params = {"tag": f"%{tag}%"}
    return where, params


def _build_superlative_query(direction: str, limit: int = 1) -> tuple[str, dict]:
    """Builds query for cheapest/most expensive/highest discount."""
    if direction == "max_discount":
        order_by = "ORDER BY o.offer_discount DESC NULLS LAST"
    elif direction == "min":
        order_by = "ORDER BY o.actual_value ASC NULLS LAST"
    else:  # max
        order_by = "ORDER BY o.actual_value DESC NULLS LAST"

    sql = _base_select() + f" {order_by} LIMIT %(limit)s"
    return sql, {"limit": limit}


# --- Service ---------------------------------------------------------------

# --- Phase 1 live-only filtering (fix/routing-and-freshness) -----------------
# Organic catalog answers surface live offers only. Effective expiry mirrors
# _format_offer below (later of summary / tier / coupon date); rows without a
# parseable date are kept (unprovable). Anchored/explicit-validity flows never
# pass through here. Gated by config.LIVE_ONLY_ORGANIC (default true).

def _row_effective_expiry(row: dict):
    expiry = row.get("offer_expire_date")
    for date_candidate in (row.get("tier_expiry"), row.get("coupon_expire_date")):
        s = str(date_candidate)[:10] if date_candidate else ""
        if s and (not expiry or s > str(expiry)[:10]):
            expiry = s
    return expiry


def _row_is_live(row: dict, now=None) -> bool:
    from core.freshness import parse_expiry, today as _today
    if not isinstance(row, dict):
        return True
    exp = parse_expiry(_row_effective_expiry(row))
    if exp is None:
        return True
    return exp >= (now or _today())


def _live_rows(rows: list, now=None) -> list:
    if not bool(getattr(config, "LIVE_ONLY_ORGANIC", True)):
        return list(rows or [])
    return [r for r in (rows or []) if _row_is_live(r, now)]


class CatalogQueryService:
    """Runs scoped, parameterized ClickHouse queries against dim_offers/dim_partners
    and formats results into customer-facing answers."""

    def __init__(self, client=None):
        self._client = client
        # Cache of active merchants for faster resolution
        self._merchant_cache: Optional[set] = None
        # Initialize session manager for comparison queries
        self.session_manager = SessionManager()

    def _get_client(self):
        if self._client is None:
            self._client = config.get_clickhouse_client()
        return self._client

    def _rows_raw(self, sql: str, params: dict) -> list[dict]:
        rows = []
        for r in self._get_client().query(sql, parameters=params).named_results():
            row_dict = dict(r)
            # Convert datetime objects to ISO format strings for JSON serialization
            for key, value in row_dict.items():
                if hasattr(value, 'isoformat'):
                    row_dict[key] = value.isoformat()
            rows.append(row_dict)
        return rows

    def _attach_tiers(self, rows: list[dict]) -> None:
        """Enriches offer rows with their currently-purchasable dim_type_price
        tiers (status=1), aggregated into min/max price + count + latest expiry.
        Rows without an offer_id (e.g. the active-merchants query) are skipped.

        The site renders every offer page from exactly this table -- offer 8291
        lists 18 meal coupons while dim_offers.actual_value only holds the
        cheapest summary price. Attaching the tiers here keeps live answers on
        par with the site instead of reporting one number."""
        ids = sorted({r.get("offer_id") for r in rows if r.get("offer_id") is not None})
        if not ids:
            return
        tiers_by_offer = {}
        # clickhouse_connect parameter binding: one placeholder per id.
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ", ".join(f"%(id_{i})s" for i in range(len(chunk)))
            params = {f"id_{i}": oid for i, oid in enumerate(chunk)}
            sql = (
                "SELECT offer_id, type_price_name, type_price_name_ar, "
                "type_price_price, type_price_price_before_discount, type_price_discount, "
                "start_date, expire_date "
                f"FROM {config.ch_table('dim_type_price')} "
                f"WHERE status = 1 AND offer_id IN ({placeholders})"
            )
            for r in self._rows_raw(sql, params):
                tiers_by_offer.setdefault(r["offer_id"], []).append(r)
        for row in rows:
            oid = row.get("offer_id")
            if oid is None:
                continue
            ts = tiers_by_offer.get(oid)
            if not ts:
                continue
            row["tiers"] = ts
            prices = []
            expiries = []
            for t in ts:
                p = t.get("type_price_price")
                if p is not None:
                    try:
                        pf = float(p)
                    except (TypeError, ValueError):
                        pf = None
                    if pf is not None and pf > 0:
                        prices.append(pf)
                e = t.get("expire_date")
                if e:
                    s = str(e)[:10]
                    if len(s) == 10 and s[4] == "-":
                        expiries.append(s)
            row["n_tiers"] = len(ts)
            if prices:
                row["min_price"] = min(prices)
                row["max_price"] = max(prices)
            if expiries:
                row["tier_expiry"] = max(expiries)

    def _rows(self, sql: str, params: dict) -> list[dict]:
        rows = self._rows_raw(sql, params)
        self._attach_tiers(rows)
        return rows

    # LIMIT applies in SQL before _live_rows drops expired rows, so a plain
    # LIMIT n can return n expired rows and an empty page even when live rows
    # exist (proven live 2026-09-28: top-discount Cairo rows are 2013 relics).
    # Over-fetch, then filter, then slice at the answer layer.
    _OVERFETCH = 5

    def _rows_live(self, sql: str, params: dict, limit: int, session_id: Optional[str] = None) -> list[dict]:
        params = dict(params or {})
        params["lim"] = min(int(limit) * self._OVERFETCH, 50)
        rows = _live_rows(self._rows(sql, params))
        if session_id:
            self.session_manager.add_offers_to_session(session_id, rows)
        return rows

    def _get_active_merchants(self) -> set:
        """Fetch and cache active merchant names from dim_partners."""
        if self._merchant_cache is None:
            db = config.CLICKHOUSE_DATABASE
            sql = f"""
                SELECT DISTINCT part_name_en, part_name_ar
                FROM {db}.dim_partners
                WHERE status = 'active'
                  AND part_id IN (SELECT DISTINCT part_id FROM {db}.dim_offers WHERE deleted_at IS NULL AND offer_status = 'active')
            """
            rows = self._rows(sql, {})
            merchants = set()
            for r in rows:
                if r.get("part_name_en"):
                    merchants.add(r["part_name_en"].strip())
                if r.get("part_name_ar"):
                    merchants.add(r["part_name_ar"].strip())
            self._merchant_cache = merchants
        return self._merchant_cache

    def _resolve_merchant(self, merchant: str) -> str:
        """Resolve merchant name using aliases and fuzzy matching against known merchants."""
        merchant_clean = merchant.strip().lower()

        # Check aliases first
        if merchant_clean in config.MERCHANT_ALIASES:
            return config.MERCHANT_ALIASES[merchant_clean]

        # Check exact match against known merchants
        known = self._get_active_merchants()
        for known_m in known:
            if known_m.lower() == merchant_clean:
                return known_m

        # Partial match
        for known_m in known:
            if merchant_clean in known_m.lower() or known_m.lower() in merchant_clean:
                return known_m

        # Fuzzy match (difflib)
        import difflib
        close = difflib.get_close_matches(merchant_clean, [m.lower() for m in known], n=1, cutoff=0.8)
        if close:
            for known_m in known:
                if known_m.lower() == close[0]:
                    return known_m

        return merchant  # Return original if no match

    # -- Query Methods -------------------------------------------------------

    def list_by_merchant(self, merchant: str, lang: str, limit: int = 10, session_id: Optional[str] = None, place_id: Optional[int] = None) -> list[dict]:
        merchant = self._resolve_merchant(merchant)
        where, params = _build_merchant_filter(merchant, lang)
        if place_id is not None:
            where += " AND o.place_id = %(pid)s"
            params["pid"] = place_id
        sql = _base_select() + where + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s"
        return self._rows_live(sql, params, limit, session_id)

    def list_by_price_range(self, price_min: Optional[float], price_max: Optional[float], lang: str, limit: int = 10, session_id: Optional[str] = None) -> list[dict]:
        where, params = _build_price_filter(price_min, price_max)
        sql = _base_select() + where + " ORDER BY o.actual_value ASC NULLS LAST LIMIT %(lim)s"
        return self._rows_live(sql, params, limit, session_id)

    def get_superlative(self, direction: str, lang: str, session_id: Optional[str] = None) -> list[dict]:
        sql, params = _build_superlative_query(direction, limit=5)
        rows = _live_rows(self._rows(sql, params))
        if session_id:
            self.session_manager.add_offers_to_session(session_id, rows)
        return rows[:1]

    def get_merchant_location(self, merchant: str, lang: str, session_id: Optional[str] = None) -> list[dict]:
        merchant = self._resolve_merchant(merchant)
        where, params = _build_merchant_filter(merchant, lang)
        sql = _base_select() + where + " LIMIT 5"
        rows = self._rows(sql, params)
        if session_id:
            self.session_manager.add_offers_to_session(session_id, rows)
        return _live_rows(rows)

    def list_by_tag(self, tag: str, lang: str, limit: int = 10, session_id: Optional[str] = None) -> list[dict]:
        where, params = _build_tag_filter(tag)
        sql = _base_select() + where + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s"
        return self._rows_live(sql, params, limit, session_id)

    def list_by_place(self, place_id: int, lang: str, limit: int = 10, session_id: Optional[str] = None) -> list[dict]:
        """Offers valid in one city/place (dim_offers.place_id)."""
        sql = (_base_select() + " AND o.place_id = %(pid)s"
               " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s")
        return self._rows_live(sql, {"pid": place_id}, limit, session_id)

    def list_ending_soon(self, lang: str, days: int = 7, limit: int = 10, session_id: Optional[str] = None) -> list[dict]:
        """Live offers expiring within `days` (last-chance questions)."""
        sql = (_base_select() + " AND o.offer_expire_date BETWEEN now() AND now() + INTERVAL %(days)s DAY"
               " ORDER BY o.offer_expire_date ASC NULLS LAST LIMIT %(lim)s")
        rows = self._rows(sql, {"days": days, "lim": limit})
        if session_id:
            self.session_manager.add_offers_to_session(session_id, rows)
        return _live_rows(rows)

    def list_starting_soon(self, lang: str, limit: int = 10, session_id: Optional[str] = None) -> list[dict]:
        """Active offers whose sale window opens in the future."""
        sql = (_base_select() + " AND o.offer_start_date > now()"
               " ORDER BY o.offer_start_date ASC NULLS LAST LIMIT %(lim)s")
        rows = self._rows(sql, {"lim": limit})
        if session_id:
            self.session_manager.add_offers_to_session(session_id, rows)
        return _live_rows(rows)

    def live_places(self) -> list[dict]:
        """Cities/places that actually have live offers right now (for honest
        'nothing in X, try Y' answers). Cached per process."""
        if getattr(self, "_live_places_cache", None) is not None:
            return self._live_places_cache
        db = config.CLICKHOUSE_DATABASE
        try:
            rows = self._rows_raw(
                f"""SELECT o.place_id AS pid, pl.place_name_en AS en, pl.place_name_ar AS ar,
                           count() AS n
                    FROM {db}.dim_offers o
                    LEFT JOIN {db}.dim_place pl ON pl.place_id = o.place_id
                    WHERE o.deleted_at IS NULL AND o.offer_status = 'active'
                      AND o.offer_expire_date > now()
                    GROUP BY o.place_id, pl.place_name_en, pl.place_name_ar
                    ORDER BY n DESC""", {})
        except Exception:  # noqa: BLE001
            rows = []
        self._live_places_cache = rows
        return rows

    def list_multi_merchant(self, merchants: list[str], lang: str, limit_per_merchant: int = 3, session_id: Optional[str] = None) -> list[dict]:
        """Fetch offers for multiple merchants (e.g., 'KFC and Pizza Hut')."""
        all_results = []
        for merchant in merchants:
            merchant = self._resolve_merchant(merchant)
            where, params = _build_merchant_filter(merchant, lang)
            sql = _base_select() + where + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s"
            rows = self._rows_live(sql, params, limit_per_merchant)
            for r in rows:
                r["_matched_merchant"] = merchant
            all_results.extend(rows)
        if session_id:
            self.session_manager.add_offers_to_session(session_id, all_results)
        return _live_rows(all_results)

    # -- Formatting Helpers --------------------------------------------------

    def _title(self, row: dict, lang: str) -> str:
        # Live main_eg: mobile_offer_title_* is NULL for ~all live offers
        # (verified 2026-09-28: 1/658 has it); the real title is offer_brief_*.
        if lang == "ar":
            return (row.get("mobile_offer_title_ar") or row.get("mobile_offer_title_en")
                    or row.get("offer_brief_ar") or row.get("offer_brief_en")
                    or row.get("part_name_ar") or row.get("part_name_en")
                    or f"كوبون {row.get('offer_id')}")
        return (row.get("mobile_offer_title_en") or row.get("mobile_offer_title_ar")
                or row.get("offer_brief_en") or row.get("offer_brief_ar")
                or row.get("part_name_en") or row.get("part_name_ar")
                or f"Coupon {row.get('offer_id')}")

    def _currency(self, lang: str) -> str:
        c = config.CURRENCY
        if isinstance(c, dict):
            return c.get(lang, c.get("en", "EGP"))
        return c

    def _short_date(self, v) -> str:
        if not v:
            return ""
        # Handle datetime objects
        if hasattr(v, 'strftime'):
            return v.strftime("%Y-%m-%d")
        return str(v)[:10]

    def _clean_text(self, text) -> str:
        """Clean HTML tags and entities like fetch_offers_clickhouse.py does."""
        if not text:
            return ""
        import html, re
        _HTML_TAG_RE = re.compile(r"<[^>]+>")
        _WHITESPACE_RE = re.compile(r"\s+")
        t = _HTML_TAG_RE.sub(" ", str(text))
        t = html.unescape(t)
        t = _WHITESPACE_RE.sub(" ", t)
        return t.strip()

    def _format_offer(self, row: dict, lang: str, include_location: bool = False) -> str:
        """Formats one offer row into a customer-facing line."""
        title = self._title(row, lang)
        currency = self._currency(lang)

        def _num(v):
            s = str(v)
            return s.replace(".0", "") if s.endswith(".0") else s

        price = row.get("actual_value")
        old_price = row.get("offer_value")
        discount = row.get("offer_discount")
        expiry = row.get("offer_expire_date")

        tiers = row.get("tiers") or []
        n_tiers = row.get("n_tiers") or 0
        min_price = row.get("min_price")
        max_price = row.get("max_price")
        # Multi-tier offers render a price RANGE + options list (like the site
        # does) instead of the single summary dim_offers.actual_value.
        has_range = n_tiers > 1 and min_price is not None and max_price is not None \
            and float(min_price) != float(max_price)

        # Effective expiry = the later of the summary date, the latest tier
        # date, and any coupon_expire_date (the site shows the tier/coupon
        # date -- offer 8291: summary 2026-08-31 vs tiers valid until
        # 2026-09-30).
        c = row.get("coupon_expire_date")
        eff_expiry = expiry
        for date_candidate in (row.get("tier_expiry"), c):
            s = str(date_candidate)[:10] if date_candidate else ""
            if s and (not eff_expiry or s > str(eff_expiry)[:10]):
                eff_expiry = s

        parts = [f"- {title}"]

        if has_range:
            if lang == "ar":
                parts.append(f"من {_num(min_price)} حتى {_num(max_price)} {currency} ({n_tiers} خيارات)")
            else:
                parts.append(f"from {_num(min_price)} to {_num(max_price)} {currency} ({n_tiers} options)")
            shown = 0
            for t in tiers:
                tname = (t.get("type_price_name_ar") if lang == "ar" else t.get("type_price_name")) \
                    or t.get("type_price_name") or t.get("type_price_name_ar")
                tp = t.get("type_price_price")
                if not tname or tp in (None, ""):
                    continue
                tline = f"- {tname}: {_num(tp)} {currency}"
                tbefore = t.get("type_price_price_before_discount")
                tdiscount = t.get("type_price_discount")
                if lang == "ar":
                    extra = []
                    if tbefore not in (None, "", 0, "0") and str(tbefore) != str(tp):
                        extra.append(f"كانت {_num(tbefore)} {currency}")
                    if tdiscount not in (None, "", 0, "0"):
                        extra.append(f"خصم {_num(tdiscount)}%")
                    if extra:
                        tline += " (" + "، ".join(extra) + ")"
                else:
                    extra = []
                    if tbefore not in (None, "", 0, "0") and str(tbefore) != str(tp):
                        extra.append(f"was {_num(tbefore)} {currency}")
                    if tdiscount not in (None, "", 0, "0"):
                        extra.append(f"{_num(tdiscount)}% off")
                    if extra:
                        tline += " (" + ", ".join(extra) + ")"
                parts.append(tline)
                shown += 1
                if shown >= 6:
                    break
            if n_tiers > shown:
                rest = n_tiers - shown
                if lang == "ar":
                    parts.append(f"... و{rest} خيارات أخرى -- شوفهم كاملين في رابط العرض")
                else:
                    parts.append(f"... and {rest} more options -- see them all via the offer link")
        else:
            if price is not None:
                price_str = f"{_num(price)} {currency}"
                if old_price is not None and old_price != price:
                    price_str += f" (was {_num(old_price)} {currency})"
                if discount is not None and discount not in (0, "0", "0.0"):
                    price_str += f", {discount}% off"
                parts.append(price_str)

        expiry_str = self._short_date(eff_expiry)
        if expiry_str:
            parts.append(f"valid until {expiry_str}" if lang == "en" else f"صالح حتى {expiry_str}")

        place = row.get(f"place_name_{lang}") or row.get("place_name_en")
        if place:
            parts.append(f"📍 {self._clean_text(place)}")

        if include_location:
            loc_parts = []
            address = row.get(f"part_address_{lang}") or row.get("part_address_en")
            if address:
                loc_parts.append(f"Address: {self._clean_text(address)}")
            hours = row.get(f"work_time_{lang}") or row.get("work_time_en")
            if hours:
                loc_parts.append(f"Hours: {self._clean_text(hours)}")
            phone = row.get("part_tel") or row.get("part_tel2")
            if phone:
                loc_parts.append(f"Phone: {phone}")
            website = row.get("part_website")
            if website:
                loc_parts.append(f"Website: {website}")
            facebook = row.get("part_facebook")
            if facebook:
                loc_parts.append(f"Facebook: {facebook}")
            instagram = row.get("instagram")
            if instagram:
                loc_parts.append(f"Instagram: {instagram}")

            if loc_parts:
                parts.append(" | ".join(loc_parts))

        if has_range and row.get("offer_id") is not None:
            url_lang = "ar" if lang == "ar" else "en"
            label = "تحقق من كل الخيارات" if lang == "ar" else "See all options"
            parts.append(f"📍 🔗 {label}: https://waffarha.com/{url_lang}/o-{row.get('offer_id')}")

        return ("\n".join(parts) if has_range else " — ".join(parts))

    # -- Answer Builders -----------------------------------------------------

    def _messages(self, lang: str) -> dict:
        return {
            "en": {
                "no_offers": "I couldn't find any active offers matching that.",
                "merchant_header": "Here are the current offers from {merchant}:",
                "price_header": "Here are offers under {max} {cur}:",
                "price_range_header": "Here are offers between {min} and {max} {cur}:",
                "cheapest": "Here's the cheapest offer available right now:",
                "most_expensive": "Here's the most expensive offer available right now:",
                "highest_discount": "Here's the offer with the highest discount right now:",
                "location_header": "Here are the details for {merchant}:",
                "tag_header": "Here are the current {tag} offers:",
                "place_header": "Here are the current offers in {place}:",
                "place_empty": ("I don't see any live offers in {place} right now. "
                                "Live offers are currently in: {live_places}."),
                "ending_soon_header": "These offers end within {days} days — last chance:",
                "ending_soon_empty": "Nothing is expiring in the next {days} days.",
                "starting_soon_header": "These offers start soon:",
                "starting_soon_empty": "No upcoming offers are announced right now.",
                "multi_merchant_header": "Here are offers from {merchants}:",
                "compare_header": "Here's a comparison of offers from {merchants}:",
                "compare_no_offers": "I don't have any offers from {merchants} in your recent history to compare.",
            },
            "ar": {
                "no_offers": "مفيش عروض نشطة مطابقة للطلب.",
                "merchant_header": "دي العروض الحالية من {merchant}:",
                "price_header": "دي العروض تحت {max} {cur}:",
                "price_range_header": "دي العروض بين {min} و {max} {cur}:",
                "cheapest": "ده أرخص عرض متاح دلوقتي:",
                "most_expensive": "ده أغلى عرض متاح دلوقتي:",
                "highest_discount": "ده العرض اللي عليه أعلى خصم دلوقتي:",
                "location_header": "تفاصيل {merchant}:",
                "tag_header": "دي عروض {tag} الحالية:",
                "place_header": "دي العروض الحالية في {place}:",
                "place_empty": ("مفيش عروض شغالة في {place} دلوقتي. "
                                "العروض الشغالة حاليا في: {live_places}."),
                "ending_soon_header": "العروض دي هتخلص خلال {days} أيام — آخر فرصة:",
                "ending_soon_empty": "مفيش حاجة هتخلص خلال {days} أيام.",
                "starting_soon_header": "العروض دي هتبدأ قريب:",
                "starting_soon_empty": "مفيش عروض جاية معلنة دلوقتي.",
                "multi_merchant_header": "عروض من {merchants}:",
                "compare_header": "مقارنة بين عروض {merchants}:",
                "compare_no_offers": "مفيش عروض من {merchants} في سجلك الحديث للمقارنة.",
            },
        }[lang]

    def handle(self, query: str, lang: str, session_id: Optional[str] = None) -> dict:
        """Main entry point: detect intent, run query, format answer."""
        q = (query or "").strip()
        if not q:
            return {"answer": CATALOG_ERROR.get(lang, CATALOG_ERROR["en"]), "sources": []}

        if _is_unsupported(q.lower()):
            return {"answer": "I can help with offers, prices, and locations — but I can't access personal account data.", "sources": []}

        intent = _detect_intent(q)

        try:
            # Multi-merchant: check if query mentions 2+ known merchants
            from core.rag_engine import _mentioned_merchants
            mentioned = _mentioned_merchants(q, sorted(self._get_active_merchants(), key=len, reverse=True))
            if len(mentioned) >= 2:
                return self._answer_multi_merchant(mentioned, lang, session_id)

            if intent["type"] == "merchant":
                return self._answer_merchant(intent["merchant"], lang, session_id,
                                             place_id=intent.get("place_id"))
            elif intent["type"] == "place":
                return self._answer_place(intent["place_id"], lang, session_id)
            elif intent["type"] == "ending_soon":
                return self._answer_ending_soon(intent.get("days") or 7, lang, session_id)
            elif intent["type"] == "starting_soon":
                return self._answer_starting_soon(lang, session_id)
            elif intent["type"] == "price_ceiling":
                return self._answer_price_ceiling(intent["price_max"], lang, session_id)
            elif intent["type"] == "price_floor":
                return self._answer_price_floor(intent["price_min"], lang, session_id)
            elif intent["type"] == "price_range":
                return self._answer_price_range(intent["price_min"], intent["price_max"], lang, session_id)
            elif intent["type"] == "superlative":
                return self._answer_superlative(intent["direction"], lang, session_id)
            elif intent["type"] == "location":
                return self._answer_location(intent["merchant"], lang, session_id)
            elif intent["type"] == "tag":
                return self._answer_tag(intent["tag"], lang, session_id)
            elif intent["type"] == "compare":
                return self._answer_compare(intent["merchants"], lang, session_id)
            else:
                # Fallback: generic offer list
                return self._answer_generic(lang, session_id)
        except Exception as e:
            log.warning("catalog query failed for query=%r: %s", query, e)
            return {"answer": CATALOG_ERROR.get(lang, CATALOG_ERROR["en"]), "sources": []}

    def _answer_merchant(self, merchant: str, lang: str, session_id: Optional[str] = None, place_id: Optional[int] = None) -> dict:
        rows = self.list_by_merchant(merchant, lang, limit=10, session_id=session_id, place_id=place_id)
        if not rows:
            resolved = self._resolve_merchant(merchant)
            msgs = self._messages(lang)
            return {"answer": f"{msgs['no_offers']} (checked: {resolved})", "sources": []}

        msgs = self._messages(lang)
        header = msgs["merchant_header"].format(merchant=rows[0].get("part_name_en") or merchant)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _place_label(self, place_id: int, lang: str) -> str:
        en, ar = PLACES.get(place_id, ("", ""))
        return ar if lang == "ar" else en

    def _live_places_label(self, lang: str) -> str:
        rows = self.live_places()
        names = [(r.get("ar") or r.get("en")) if lang == "ar"
                 else (r.get("en") or r.get("ar")) for r in rows]
        names = [n for n in names if n]
        return ", ".join(names[:5]) if names else ("Cairo" if lang == "en" else "القاهرة")

    def _answer_place(self, place_id: int, lang: str, session_id: Optional[str] = None) -> dict:
        msgs = self._messages(lang)
        rows = self.list_by_place(place_id, lang, limit=10, session_id=session_id)
        if not rows:
            return {"answer": msgs["place_empty"].format(
                place=self._place_label(place_id, lang),
                live_places=self._live_places_label(lang)), "sources": []}
        header = msgs["place_header"].format(place=self._place_label(place_id, lang))
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_ending_soon(self, days: int, lang: str, session_id: Optional[str] = None) -> dict:
        msgs = self._messages(lang)
        rows = self.list_ending_soon(lang, days=days, limit=10, session_id=session_id)
        if not rows:
            return {"answer": msgs["ending_soon_empty"].format(days=days), "sources": []}
        header = msgs["ending_soon_header"].format(days=days)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_starting_soon(self, lang: str, session_id: Optional[str] = None) -> dict:
        msgs = self._messages(lang)
        rows = self.list_starting_soon(lang, limit=10, session_id=session_id)
        if not rows:
            return {"answer": msgs["starting_soon_empty"], "sources": []}
        header = msgs["starting_soon_header"]
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_price_ceiling(self, price_max: float, lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.list_by_price_range(None, price_max, lang, limit=10, session_id=session_id)
        if not rows:
            msgs = self._messages(lang)
            cur = self._currency(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        cur = self._currency(lang)
        header = msgs["price_header"].format(max=price_max, cur=cur)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_price_floor(self, price_min: float, lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.list_by_price_range(price_min, None, lang, limit=10, session_id=session_id)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        cur = self._currency(lang)
        header = f"Here are offers over {price_min} {cur}:" if lang == "en" else f"دي العروض فوق {price_min} {cur}:"
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_price_range(self, price_min: float, price_max: float, lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.list_by_price_range(price_min, price_max, lang, limit=10, session_id=session_id)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        cur = self._currency(lang)
        header = msgs["price_range_header"].format(min=price_min, max=price_max, cur=cur)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_superlative(self, direction: str, lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.get_superlative(direction, lang, session_id=session_id)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        if direction == "min":
            header = msgs["cheapest"]
        elif direction == "max":
            header = msgs["most_expensive"]
        else:
            header = msgs["highest_discount"]

        lines = [header, self._format_offer(rows[0], lang)]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_location(self, merchant: str, lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.get_merchant_location(merchant, lang, session_id=session_id)
        if not rows:
            resolved = self._resolve_merchant(merchant)
            msgs = self._messages(lang)
            return {"answer": f"Couldn't find location info for {resolved}.", "sources": []}

        msgs = self._messages(lang)
        merchant_name = rows[0].get("part_name_en") or rows[0].get("part_name_ar") or merchant
        header = msgs["location_header"].format(merchant=merchant_name)
        lines = [header] + [self._format_offer(r, lang, include_location=True) for r in rows[:3]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_tag(self, tag: str, lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.list_by_tag(tag, lang, limit=10, session_id=session_id)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        tag_label = {
            "ramadan": "Ramadan" if lang == "en" else "رمضان",
            "hot_deals": "Hot Deals" if lang == "en" else "عروض ساخنة",
            "limited_time": "Limited Time" if lang == "en" else "فترة محدودة",
            "feast": "Eid/Feast" if lang == "en" else "العيد",
            "valentine_offers": "Valentine's" if lang == "en" else "فالنتاين",
            "delivery_section": "Delivery" if lang == "en" else "توصيل",
        }.get(tag, tag)

        header = msgs["tag_header"].format(tag=tag_label)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_multi_merchant(self, merchants: list[str], lang: str, session_id: Optional[str] = None) -> dict:
        rows = self.list_multi_merchant(merchants, lang, limit_per_merchant=3, session_id=session_id)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        merchant_names = ", ".join(merchants[:3])
        header = msgs["multi_merchant_header"].format(merchants=merchant_names)
        lines = [header]

        # Group by merchant
        from collections import defaultdict
        by_merchant = defaultdict(list)
        for r in rows:
            m = r.get("_matched_merchant") or r.get("part_name_en") or "Unknown"
            by_merchant[m].append(r)

        for merchant, m_rows in by_merchant.items():
            lines.append(f"\n{merchant}:")
            lines.extend(self._format_offer(r, lang) for r in m_rows[:3])

        return {"answer": "\n".join(lines), "sources": []}

    def _answer_compare(self, merchants: List[str], lang: str, session_id: Optional[str] = None) -> dict:
        """Compare offers for merchants using session memory.

        FIX for Issue #3: Previously this method retrieved fresh offers instead of
        scoping to previously shown offers, and could truncate mid-word due to
        insufficient context. Now it uses session memory to get the offers that
        were actually shown to the user, and ensures complete answer formatting.
        """
        msgs = self._messages(lang)

        if not session_id:
            return {"answer": msgs["compare_no_offers"].format(merchants=", ".join(merchants)), "sources": []}

        # Get offers from session memory for each merchant
        merchant_offers = self.session_manager.get_offers_for_merchants(session_id, merchants)

        # Check if we have offers for any merchants
        total_offers = sum(len(offers) for offers in merchant_offers.values())
        if total_offers == 0:
            return {"answer": msgs["compare_no_offers"].format(merchants=", ".join(merchants)), "sources": []}

        # Build the comparison answer
        merchant_names = " & ".join(merchants)
        lines = [msgs["compare_header"].format(merchants=merchant_names)]

        for merchant in merchants:
            offers = merchant_offers.get(merchant, [])
            if offers:
                lines.append(f"\n**{merchant}:**")
                # Show all available offers for this merchant (limit to 3 to avoid truncation)
                for offer in offers[:3]:
                    lines.append(self._format_offer(offer, lang))
            else:
                # Merchant not found in session
                if lang == "ar":
                    lines.append(f"\n**{merchant}:** {msgs['no_offers']}")
                else:
                    lines.append(f"\n**{merchant}:** {msgs['no_offers']}")

        # Ensure the answer is complete (no truncation mid-word)
        answer = "\n".join(lines)

        # Validate that no words are cut off mid-word
        words = answer.split()
        for i, word in enumerate(words):
            # Check for incomplete words (ending with hyphen indicating truncation)
            if word.endswith('-') and i < len(words) - 1:
                log.warning(f"Detected potential truncation in compare answer: '{word}'")

        return {"answer": answer, "sources": []}

    def _answer_generic(self, lang: str, session_id: Optional[str] = None) -> dict:
        """Fallback: show a few top active offers."""
        sql = _base_select() + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT 5"
        rows = _live_rows(self._rows(sql, {}))
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        if session_id:
            self.session_manager.add_offers_to_session(session_id, rows)

        msgs = self._messages(lang)
        header = "Here are some top offers right now:" if lang == "en" else "بعض أفضل العروض دلوقتي:"
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}


# --- Module-level convenience function (for testing) ---

def run_catalog_query(query: str, lang: str = "en") -> dict:
    """Quick test function: python -m ingest.catalog_queries 'KFC offers'"""
    service = CatalogQueryService()
    return service.handle(query, lang)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        result = run_catalog_query(q)
        print(result["answer"])
    else:
        print("Usage: python -m ingest.catalog_queries 'your query here'")