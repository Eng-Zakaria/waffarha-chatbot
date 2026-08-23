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
from typing import Optional

import config

log = logging.getLogger("waffarha-app")

CATALOG_ERROR = {
    "en": "I had trouble reaching the offers catalog right now. Please try again in a moment.",
    "ar": "حصلت مشكلة في الوصول لدليل العروض دلوقتي. جرب تاني بعد شوية.",
}

# --- Intent detection patterns ---------------------------------------------

# Merchant-specific: "KFC offers", "عروض كنتاكي", "deals from Pizza Hut"
# These patterns ONLY match when the query is explicitly asking for offers/deals/coupons
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

# --- Unsupported patterns (honestly refused) ---
_UNSUPPORTED_PATTERNS = [
    r"\b(my\s+(coupons?|orders?|purchases?|account|wallet|cashback|balance|points))\b",
    r"كوبوناتي|طلباتي|حسابي|محفظتي|كاش\s*باك|رصيدي|نقاطي",
    r"\b(shipping|tracking|track)\b",
    r"\b(delivery|توصيل|شحن|تتبع)\s+(?:status|tracking|track|حالة|تتبع)\b",
]

# FAQ/how-to patterns that should NOT go to catalog queries
# These are informational questions, not offer lookups
_FAQ_PATTERNS = [
    r"\b(how\s+(do|can|to)\s+(i|you|we))\b",
    r"\bwhat\s+(is|are)\b",
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
    r"\bhow\s+much\b",  # "how much is X" - price check, not catalog lookup
    r"\bwhat'?s\s+the\s+(discount|price)\b",  # "what's the discount on X"
    r"\btell\s+me\s+about\b",  # "tell me about X" - info request, not catalog lookup
    r"ازاي\s+(اشترى|ادفع|استخدم|الغي|احذف|اسجل)",
    r"كيفية\s+(الشراء|الدفع|الاستخدام|الغاء|حذف|التسجيل)",
    r"معلومات\s+(عن|خصوصية|سياسة)",
    r"ايه\s+(هي|هو)\s+وفرها",
    r"سياسة\s+(الخصوصية|الاسترداد|الكاش\s*باك)",
    r"بكام\b",  # Arabic "how much"
    r"كام\s+(السعر|الثمن)",  # Arabic "price"
]
_FAQ_RE = [re.compile(p, re.IGNORECASE) for p in _FAQ_PATTERNS]
_UNSUPPORTED_RE = [re.compile(p, re.IGNORECASE) for p in _UNSUPPORTED_PATTERNS]


def is_catalog_query(query: str) -> bool:
    """Returns True if the query looks like a catalog question (not personal, not FAQ)."""
    q = (query or "").lower()

    # Explicitly NOT catalog: personal queries
    from personal_queries import is_personal_query
    if is_personal_query(q):
        return False

    # Explicitly NOT catalog: unsupported (personal data disguised)
    if any(r.search(q) for r in _UNSUPPORTED_RE):
        return False

    # Explicitly NOT catalog: FAQ/how-to questions
    if any(r.search(q) for r in _FAQ_RE):
        return False

    # Catalog patterns
    return any(
        r.search(q) for r in
        _MERCHANT_RE + _PRICE_CEILING_RE + _PRICE_FLOOR_RE + _PRICE_RANGE_RE +
        _SUPERLATIVE_RE + _LOCATION_RE + _TAG_RE
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
        "price_min": None,
        "price_max": None,
        "direction": None,  # "min", "max", "max_discount"
        "tag": None,
        "location": None,
    }

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
            intent["merchant"] = m.group(1).strip()
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

_BASE_SELECT = """
    SELECT
        o.offer_id, o.part_id, o.section_id,
        o.mobile_offer_title_en, o.mobile_offer_title_ar,
        o.offer_brief_en, o.offer_brief_ar,
        o.actual_value, o.offer_value, o.offer_discount,
        o.offer_expire_date, o.offer_status,
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
        p.location_en, p.location_ar
    FROM main.dim_offers o
    LEFT JOIN main.dim_partners p ON o.part_id = p.part_id
    WHERE o.deleted_at IS NULL
      AND o.offer_status = 'active'
      AND (p.status = 'active' OR p.status IS NULL)
"""

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
    where = "AND o.offer_special_display LIKE %(tag)s"
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

    sql = _BASE_SELECT + f" {order_by} LIMIT %(limit)s"
    return sql, {"limit": limit}


# --- Service ---------------------------------------------------------------

class CatalogQueryService:
    """Runs scoped, parameterized ClickHouse queries against dim_offers/dim_partners
    and formats results into customer-facing answers."""

    def __init__(self, client=None):
        self._client = client
        # Cache of active merchants for faster resolution
        self._merchant_cache: Optional[set] = None

    def _get_client(self):
        if self._client is None:
            self._client = config.get_clickhouse_client()
        return self._client

    def _rows(self, sql: str, params: dict) -> list[dict]:
        return [dict(r) for r in self._get_client().query(sql, parameters=params).named_results()]

    def _get_active_merchants(self) -> set:
        """Fetch and cache active merchant names from dim_partners."""
        if self._merchant_cache is None:
            sql = """
                SELECT DISTINCT part_name_en, part_name_ar
                FROM main.dim_partners
                WHERE status = 'active'
                  AND part_id IN (SELECT DISTINCT part_id FROM main.dim_offers WHERE deleted_at IS NULL AND offer_status = 'active')
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

    def list_by_merchant(self, merchant: str, lang: str, limit: int = 10) -> list[dict]:
        merchant = self._resolve_merchant(merchant)
        where, params = _build_merchant_filter(merchant, lang)
        sql = _BASE_SELECT + where + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s"
        params["lim"] = limit
        return self._rows(sql, params)

    def list_by_price_range(self, price_min: Optional[float], price_max: Optional[float], lang: str, limit: int = 10) -> list[dict]:
        where, params = _build_price_filter(price_min, price_max)
        sql = _BASE_SELECT + where + " ORDER BY o.actual_value ASC NULLS LAST LIMIT %(lim)s"
        params["lim"] = limit
        return self._rows(sql, params)

    def get_superlative(self, direction: str, lang: str) -> list[dict]:
        sql, params = _build_superlative_query(direction, limit=1)
        return self._rows(sql, params)

    def get_merchant_location(self, merchant: str, lang: str) -> list[dict]:
        merchant = self._resolve_merchant(merchant)
        where, params = _build_merchant_filter(merchant, lang)
        sql = _BASE_SELECT + where + " LIMIT 5"
        return self._rows(sql, params)

    def list_by_tag(self, tag: str, lang: str, limit: int = 10) -> list[dict]:
        where, params = _build_tag_filter(tag)
        sql = _BASE_SELECT + where + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s"
        params["lim"] = limit
        return self._rows(sql, params)

    def list_multi_merchant(self, merchants: list[str], lang: str, limit_per_merchant: int = 3) -> list[dict]:
        """Fetch offers for multiple merchants (e.g., 'KFC and Pizza Hut')."""
        all_results = []
        for merchant in merchants:
            merchant = self._resolve_merchant(merchant)
            where, params = _build_merchant_filter(merchant, lang)
            sql = _BASE_SELECT + where + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT %(lim)s"
            params["lim"] = limit_per_merchant
            rows = self._rows(sql, params)
            for r in rows:
                r["_matched_merchant"] = merchant
            all_results.extend(rows)
        return all_results

    # -- Formatting Helpers --------------------------------------------------

    def _title(self, row: dict, lang: str) -> str:
        if lang == "ar":
            return (row.get("mobile_offer_title_ar") or row.get("mobile_offer_title_en")
                    or row.get("part_name_ar") or row.get("part_name_en")
                    or f"كوبون {row.get('offer_id')}")
        return (row.get("mobile_offer_title_en") or row.get("mobile_offer_title_ar")
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

        price = row.get("actual_value")
        old_price = row.get("offer_value")
        discount = row.get("offer_discount")
        expiry = row.get("offer_expire_date")

        parts = [f"- {title}"]

        if price is not None:
            price_str = f"{price} {currency}"
            if old_price is not None and old_price != price:
                price_str += f" (was {old_price} {currency})"
            if discount is not None and discount not in (0, "0", "0.0"):
                price_str += f", {discount}% off"
            parts.append(price_str)

        expiry_str = self._short_date(expiry)
        if expiry_str:
            parts.append(f"valid until {expiry_str}" if lang == "en" else f"صالح حتى {expiry_str}")

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

        return " — ".join(parts)

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
                "multi_merchant_header": "Here are offers from {merchants}:",
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
                "multi_merchant_header": "عروض من {merchants}:",
            },
        }[lang]

    def handle(self, query: str, lang: str) -> dict:
        """Main entry point: detect intent, run query, format answer."""
        q = (query or "").strip()
        if not q:
            return {"answer": CATALOG_ERROR.get(lang, CATALOG_ERROR["en"]), "sources": []}

        if _is_unsupported(q.lower()):
            return {"answer": "I can help with offers, prices, and locations — but I can't access personal account data.", "sources": []}

        intent = _detect_intent(q)

        try:
            # Multi-merchant: check if query mentions 2+ known merchants
            from rag_engine import _mentioned_merchants
            mentioned = _mentioned_merchants(q, sorted(self._get_active_merchants(), key=len, reverse=True))
            if len(mentioned) >= 2:
                return self._answer_multi_merchant(mentioned, lang)

            if intent["type"] == "merchant":
                return self._answer_merchant(intent["merchant"], lang)
            elif intent["type"] == "price_ceiling":
                return self._answer_price_ceiling(intent["price_max"], lang)
            elif intent["type"] == "price_floor":
                return self._answer_price_floor(intent["price_min"], lang)
            elif intent["type"] == "price_range":
                return self._answer_price_range(intent["price_min"], intent["price_max"], lang)
            elif intent["type"] == "superlative":
                return self._answer_superlative(intent["direction"], lang)
            elif intent["type"] == "location":
                return self._answer_location(intent["merchant"], lang)
            elif intent["type"] == "tag":
                return self._answer_tag(intent["tag"], lang)
            else:
                # Fallback: generic offer list
                return self._answer_generic(lang)
        except Exception as e:
            log.warning("catalog query failed for query=%r: %s", query, e)
            return {"answer": CATALOG_ERROR.get(lang, CATALOG_ERROR["en"]), "sources": []}

    def _answer_merchant(self, merchant: str, lang: str) -> dict:
        rows = self.list_by_merchant(merchant, lang, limit=10)
        if not rows:
            resolved = self._resolve_merchant(merchant)
            msgs = self._messages(lang)
            return {"answer": f"{msgs['no_offers']} (checked: {resolved})", "sources": []}

        msgs = self._messages(lang)
        header = msgs["merchant_header"].format(merchant=rows[0].get("part_name_en") or merchant)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_price_ceiling(self, price_max: float, lang: str) -> dict:
        rows = self.list_by_price_range(None, price_max, lang, limit=10)
        if not rows:
            msgs = self._messages(lang)
            cur = self._currency(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        cur = self._currency(lang)
        header = msgs["price_header"].format(max=price_max, cur=cur)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_price_floor(self, price_min: float, lang: str) -> dict:
        rows = self.list_by_price_range(price_min, None, lang, limit=10)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        cur = self._currency(lang)
        header = f"Here are offers over {price_min} {cur}:" if lang == "en" else f"دي العروض فوق {price_min} {cur}:"
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_price_range(self, price_min: float, price_max: float, lang: str) -> dict:
        rows = self.list_by_price_range(price_min, price_max, lang, limit=10)
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

        msgs = self._messages(lang)
        cur = self._currency(lang)
        header = msgs["price_range_header"].format(min=price_min, max=price_max, cur=cur)
        lines = [header] + [self._format_offer(r, lang) for r in rows[:5]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_superlative(self, direction: str, lang: str) -> dict:
        rows = self.get_superlative(direction, lang)
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

    def _answer_location(self, merchant: str, lang: str) -> dict:
        rows = self.get_merchant_location(merchant, lang)
        if not rows:
            resolved = self._resolve_merchant(merchant)
            msgs = self._messages(lang)
            return {"answer": f"Couldn't find location info for {resolved}.", "sources": []}

        msgs = self._messages(lang)
        merchant_name = rows[0].get("part_name_en") or rows[0].get("part_name_ar") or merchant
        header = msgs["location_header"].format(merchant=merchant_name)
        lines = [header] + [self._format_offer(r, lang, include_location=True) for r in rows[:3]]
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_tag(self, tag: str, lang: str) -> dict:
        rows = self.list_by_tag(tag, lang, limit=10)
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

    def _answer_multi_merchant(self, merchants: list[str], lang: str) -> dict:
        rows = self.list_multi_merchant(merchants, lang, limit_per_merchant=3)
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

    def _answer_generic(self, lang: str) -> dict:
        """Fallback: show a few top active offers."""
        sql = _BASE_SELECT + " ORDER BY o.offer_discount DESC NULLS LAST LIMIT 5"
        rows = self._rows(sql, {})
        if not rows:
            msgs = self._messages(lang)
            return {"answer": msgs["no_offers"], "sources": []}

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