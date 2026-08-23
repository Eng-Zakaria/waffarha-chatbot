"""
Personal-data query service for the Waffarha Assistant.

Answers per-user questions ("show my coupons", "what's the status of my
order", "how much have I spent") by querying ClickHouse LIVE -- the
main.fct_coupons fact table (joined to dim_purchasing_status for
human-readable status names and dim_offers for offer titles) -- scoped to a
trusted user_id.

This is deliberately SEPARATE from the static RAG index (rag_engine.py):
personal data is dynamic and per-user, so it must be queried per request,
never embedded. The static index keeps answering catalog/FAQ questions; this
module handles "my stuff".

SECURITY: every SQL statement here is scoped by user_id, and user_id must
come from identity.py's resolver (never from the client). Do not add any
query here that accepts a user_id or identifier from user input directly.

WHAT IS SUPPORTED (with the current schema):
  - list / count my coupons
  - coupon / order status (via dim_purchasing_status)
  - validity / expiry (expire_at)
  - how much I paid / saved (total_price, discount)
  - spending by merchant
  - coupon expiry details
  - payment method used for purchase
  - merchant contact/location info
  - offer terms/fine print

WHAT IS NOT (honestly refused, not fabricated):
  - wallet / cashback / points balance   (no such tables)
  - shipping / delivery tracking          (no tracking table)
  - account profile (name/email/phone)    (no dim_users)
  - saved cards on file                   (dim_payment_methods is a catalog)
"""
import logging
import re

import config

log = logging.getLogger("waffarha-app")

PERSONAL_ERROR = {
    "en": "I had trouble reaching your account data right now. Please try again in a moment.",
    "ar": "حصلت مشكلة في الوصول لبيانات حسابك دلوقتي. جرب تاني بعد شوية.",
}

# --- Intent detection -----------------------------------------------------
_PERSONAL_PATTERNS = [
    r"\bmy\s+(coupons?|orders?|purchases?|vouchers?|refunds?|gift)\b",
    r"\bmy\s+(wallet|cashback|balance|points|spending|account)\b",
    r"\bhow\s+many\s+coupons\b",
    r"\bwhere('s| is)\s+my\s+order\b",
    r"\bmy\s+order\s+status\b",
    r"\bwhat('s| is)\s+the\s+status\s+of\s+my\b",
    # Arabic
    r"كوبوناتي|كوبونات اللي|طلباتي|حسابي|محفظتي|كاش باكي|عروضي|فين طلبي|فاتورتي|قسيمتي",
    r"عندي\s+كوبونات",
]
_PERSONAL_RE = [re.compile(p, re.IGNORECASE) for p in _PERSONAL_PATTERNS]

_UNSUPPORTED_PATTERNS = [
    r"\b(wallet|cashback|balance|points)\b",
    r"محفظتي|كاش باكي|رصيدي|نقاطي",
    r"\b(shipping|delivery|track|tracking)\b",
    r"توصيل|شحن|تتبع",
    r"\bmy\s+account\b",
    r"حسابي|بياناتي|بروفايلي",
    r"\bsaved\s+cards?\b",
    r"كروت محفوظة",
]
_UNSUPPORTED_RE = [re.compile(p, re.IGNORECASE) for p in _UNSUPPORTED_PATTERNS]

_STATUS_PATTERNS = [
    r"\bstatus\b", r"\bvalid\b", r"\bexpired?\b", r"\bused\b", r"\bactive\b",
    r"حالة|صالح|خلص|استخدمت|فعّال|شغال",
]
_STATUS_RE = [re.compile(p, re.IGNORECASE) for p in _STATUS_PATTERNS]

_SPENDING_PATTERNS = [
    r"\b(spend|spent|paid|pay|total|cost)\b",
    r"دفعت|صرفت|إجمالي|بكام",
]
_SPENDING_RE = [re.compile(p, re.IGNORECASE) for p in _SPENDING_PATTERNS]

_COUNT_PATTERNS = [r"\bhow\s+many\b", r"كام\s+كوبون|عدد\s+كوبونات"]
_COUNT_RE = [re.compile(p, re.IGNORECASE) for p in _COUNT_PATTERNS]

# NEW: expiry-specific patterns
_EXPIRY_PATTERNS = [
    r"\b(expire|expiry|expiration|valid|until|when.*expire)\b",
    r"متى\s+(ينتهي|تنتهي|يخلص|تخلص)|انتهاء|صلاحية|صالح\s+لحد|لحد\s+إمتى",
]
_EXPIRY_RE = [re.compile(p, re.IGNORECASE) for p in _EXPIRY_PATTERNS]

# NEW: payment method patterns
_PAYMENT_PATTERNS = [
    r"\b(how\s+(did|do)\s+i\s+pay|payment\s+method|paid\s+(with|by|using))\b",
    r"دفعت\s+(بإيه|بأي|ازاي)|طريقة\s+الدفع|الدفع\s+ب",
]
_PAYMENT_RE = [re.compile(p, re.IGNORECASE) for p in _PAYMENT_PATTERNS]

# NEW: merchant location/contact patterns
_MERCHANT_LOCATION_PATTERNS = [
    r"\b(where\s+is|location|address|branch|phone|contact|number)\b",
    r"أين|عنوان|فرع|تليفون|موبايل|اتصل|أرقام|مكان",
]
_MERCHANT_LOCATION_RE = [re.compile(p, re.IGNORECASE) for p in _MERCHANT_LOCATION_PATTERNS]

# NEW: offer terms/fine print patterns
_TERMS_PATTERNS = [
    r"\b(terms|conditions|fine\s+print|policy|rules|details)\b",
    r"شروط|أحكام|تفاصيل|سياسة|بنود|قواعد",
]
_TERMS_RE = [re.compile(p, re.IGNORECASE) for p in _TERMS_PATTERNS]


def is_personal_query(query: str) -> bool:
    q = query or ""
    return any(r.search(q) for r in _PERSONAL_RE)


def _is_unsupported(q: str) -> bool:
    return any(r.search(q) for r in _UNSUPPORTED_RE)


def _is_status(q: str) -> bool:
    return any(r.search(q) for r in _STATUS_RE)


def _is_spending(q: str) -> bool:
    return any(r.search(q) for r in _SPENDING_RE)


def _is_count(q: str) -> bool:
    return any(r.search(q) for r in _COUNT_RE)


def _is_expiry(q: str) -> bool:
    return any(r.search(q) for r in _EXPIRY_RE)


def _is_payment(q: str) -> bool:
    return any(r.search(q) for r in _PAYMENT_RE)


def _is_merchant_location(q: str) -> bool:
    return any(r.search(q) for r in _MERCHANT_LOCATION_RE)


def _is_terms(q: str) -> bool:
    return any(r.search(q) for r in _TERMS_RE)


# --- Messages -------------------------------------------------------------
_MESSAGES = {
    "en": {
        "no_coupons": "I couldn't find any coupons on your account yet.",
        "unsupported": ("I can show you your coupons and order status, but I don't "
                        "have access to your wallet/cashback balance, shipping "
                        "tracking, or profile details yet."),
        "list_header": "Here are your recent coupons:",
        "status_header": "Here's the status of your coupons:",
        "spending_header": "Here's your purchase summary:",
        "count": "You have {n} coupon(s) on your account.",
        "spending_line": "You've made {n} purchase(s), totaling {total} {cur} (saved {saved} {cur}).",
        "by_merchant": "Most active merchants: {list}",
        "valid": "valid until {d}",
        # NEW messages
        "expiry_header": "Here are your coupons with expiry dates:",
        "payment_header": "Here's how you paid for your coupons:",
        "merchant_header": "Here are the merchant details for your coupons:",
        "terms_header": "Here are the terms for your coupons:",
        "no_expiry": "No expiry date available for this coupon.",
        "no_payment": "Payment method not recorded for this coupon.",
        "no_merchant_info": "No contact/location info available for this merchant.",
        "no_terms": "No terms/fine print available for this offer.",
    },
    "ar": {
        "no_coupons": "لسه مفيش كوبونات على حسابك.",
        "unsupported": ("أقدر أعرضلك كوبوناتك وحالة طلباتك، بس لسه معنديش "
                        "الوصول لرصيد محفظتك/الكاش باك أو تتبع الشحن أو بيانات "
                        "البروفايل."),
        "list_header": "دي أحدث كوبوناتك:",
        "status_header": "دي حالة كوبوناتك:",
        "spending_header": "ده ملخص مشترياتك:",
        "count": "عندك {n} كوبون على حسابك.",
        "spending_line": "عملت {n} عملية شراء، بإجمالي {total} {cur} (وفّرت {saved} {cur}).",
        "by_merchant": "أكتر التجار نشاطًا: {list}",
        "valid": "صالح حتى {d}",
        # NEW messages
        "expiry_header": "دي كوبوناتك مع تواريخ الانتهاء:",
        "payment_header": "إزاي دفعت في كوبوناتك:",
        "merchant_header": "تفاصيل التجار لكوبوناتك:",
        "terms_header": "شروط العروض لكوبوناتك:",
        "no_expiry": "مفيش تاريخ انتهاء متاح للكوبون ده.",
        "no_payment": "طريقة الدفع مش مسجلة للكوبون ده.",
        "no_merchant_info": "مفيش معلومات تواصل/عنوان متاحة للتاجر ده.",
        "no_terms": "مفيش شروط/تفاصيل دقيقة متاحة للعرض ده.",
    },
}


def _currency(lang: str) -> str:
    c = config.CURRENCY
    if isinstance(c, dict):
        return c.get(lang, c.get("en", "EGP"))
    return c


def _title(row, lang: str) -> str:
    if lang == "ar":
        return (row.get("mobile_offer_title_ar") or row.get("mobile_offer_title_en")
                or row.get("merchant_name") or f"كوبون {row.get('coupon_id')}")
    return (row.get("mobile_offer_title_en") or row.get("mobile_offer_title_ar")
            or row.get("merchant_name") or f"Coupon {row.get('coupon_id')}")


def _status_name(row, lang: str) -> str:
    if lang == "ar":
        return (row.get("pur_status_name_ar") or row.get("status_v2")
                or f"#{row.get('coupon_status')}")
    return (row.get("pur_status_name") or row.get("status_v2")
            or f"#{row.get('coupon_status')}")


def _short_date(v) -> str:
    if not v:
        return ""
    return str(v)[:10]


# --- Service --------------------------------------------------------------
class PersonalQueryService:
    """Runs scoped, parameterized ClickHouse queries against fct_coupons and
    formats the results into a customer-facing answer."""

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            self._client = config.get_clickhouse_client()
        return self._client

    def _rows(self, sql, params):
        return [dict(r) for r in self._get_client().query(sql, parameters=params).named_results()]

    _COUPON_SELECT = """
        SELECT c.coupon_id, c.offer_id, c.voucher_sn, c.merchant_name,
               c.coupon_status, c.coupon_sold_price, c.discount, c.total_price,
               c.created_at, c.active_at, c.expire_at, c.payment_name_en,
               c.status_v2,
               p.pur_status_name, p.pur_status_name_ar,
               o.mobile_offer_title_en, o.mobile_offer_title_ar,
               o.offer_fineprint_en, o.offer_fineprint_ar,
               o.waffarha_advice_en, o.waffarha_advice_ar,
               o.part_address_en, o.part_address_ar,
               o.part_tel, o.part_tel2,
               o.part_website, o.part_facebook,
               pm.payment_name_en AS payment_method_name_en,
               pm.payment_name_ar AS payment_method_name_ar
        FROM main.fct_coupons c
        LEFT JOIN main.dim_purchasing_status p ON p.pur_status_id = c.coupon_status
        LEFT JOIN main.dim_offers o ON o.offer_id = c.offer_id
        LEFT JOIN main.dim_partners pt ON pt.part_id = o.part_id
        LEFT JOIN main.dim_payment_methods pm ON pm.payment_id = c.coupon_payment_method
        WHERE c.user_id = %(uid)s
    """

    def list_coupons(self, user_id, limit=10):
        return self._rows(
            self._COUPON_SELECT + " ORDER BY c.created_at DESC LIMIT %(lim)s",
            {"uid": user_id, "lim": limit},
        )

    def count_coupons(self, user_id):
        rows = self._rows(
            "SELECT COUNT(*) AS n FROM main.fct_coupons WHERE user_id = %(uid)s",
            {"uid": user_id},
        )
        return rows[0]["n"] if rows else 0

    def spending(self, user_id):
        rows = self._rows(
            """SELECT COUNT(*) AS n, COALESCE(SUM(total_price),0) AS total,
                      COALESCE(SUM(discount),0) AS saved
               FROM main.fct_coupons WHERE user_id = %(uid)s""",
            {"uid": user_id},
        )
        return rows[0] if rows else {"n": 0, "total": 0, "saved": 0}

    def by_merchant(self, user_id, limit=5):
        return self._rows(
            """SELECT merchant_name AS m, COUNT(*) AS n
               FROM main.fct_coupons
               WHERE user_id = %(uid)s AND merchant_name != ''
               GROUP BY merchant_name ORDER BY n DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": limit},
        )

    # -- answer builders ---------------------------------------------------
    def handle(self, query, user_id, lang):
        q = (query or "").lower()
        if _is_unsupported(q):
            return {"answer": _MESSAGES[lang]["unsupported"], "sources": []}
        try:
            if _is_expiry(q):
                return self._answer_expiry(user_id, lang)
            if _is_payment(q):
                return self._answer_payment(user_id, lang)
            if _is_merchant_location(q):
                return self._answer_merchant_location(user_id, lang)
            if _is_terms(q):
                return self._answer_terms(user_id, lang)
            if _is_status(q):
                return self._answer_status(user_id, lang)
            if _is_spending(q):
                return self._answer_spending(user_id, lang)
            if _is_count(q):
                return self._answer_count(user_id, lang)
            return self._answer_list(user_id, lang)
        except Exception as e:
            log.warning("personal query failed for user_id=%s: %s", user_id, e)
            return {"answer": PERSONAL_ERROR.get(lang, PERSONAL_ERROR["en"]), "sources": []}

    def _answer_list(self, user_id, lang):
        rows = self.list_coupons(user_id)
        if not rows:
            return {"answer": _MESSAGES[lang]["no_coupons"], "sources": []}
        cur = _currency(lang)
        lines = [_MESSAGES[lang]["list_header"]]
        for r in rows[:5]:
            price = r.get("coupon_sold_price")
            line = f"- {_title(r, lang)} — {_status_name(r, lang)}"
            if price is not None:
                line += f" ({price} {cur})"
            lines.append(line)
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_status(self, user_id, lang):
        rows = self.list_coupons(user_id, limit=5)
        if not rows:
            return {"answer": _MESSAGES[lang]["no_coupons"], "sources": []}
        lines = [_MESSAGES[lang]["status_header"]]
        for r in rows:
            line = f"- {_title(r, lang)}: {_status_name(r, lang)}"
            expire = _short_date(r.get("expire_at"))
            if expire:
                line += f" ({_MESSAGES[lang]['valid'].format(d=expire)})"
            lines.append(line)
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_count(self, user_id, lang):
        n = self.count_coupons(user_id)
        return {"answer": _MESSAGES[lang]["count"].format(n=n), "sources": []}

    def _answer_spending(self, user_id, lang):
        s = self.spending(user_id)
        cur = _currency(lang)
        lines = [_MESSAGES[lang]["spending_header"],
                 _MESSAGES[lang]["spending_line"].format(
                     n=s["n"], total=round(s["total"], 2), saved=round(s["saved"], 2), cur=cur)]
        merchants = self.by_merchant(user_id)
        if merchants:
            names = ", ".join(f"{m['m']} ({m['n']})" for m in merchants[:3])
            lines.append(_MESSAGES[lang]["by_merchant"].format(list=names))
        return {"answer": "\n".join(lines), "sources": []}

    # NEW: expiry details
    def _answer_expiry(self, user_id, lang):
        rows = self.list_coupons(user_id, limit=10)
        if not rows:
            return {"answer": _MESSAGES[lang]["no_coupons"], "sources": []}
        lines = [_MESSAGES[lang]["expiry_header"]]
        for r in rows:
            line = f"- {_title(r, lang)}"
            expire = _short_date(r.get("expire_at"))
            if expire:
                line += f" ({_MESSAGES[lang]['valid'].format(d=expire)})"
            else:
                line += f" ({_MESSAGES[lang]['no_expiry']})"
            lines.append(line)
        return {"answer": "\n".join(lines), "sources": []}

    # NEW: payment method used
    def _answer_payment(self, user_id, lang):
        rows = self.list_coupons(user_id, limit=10)
        if not rows:
            return {"answer": _MESSAGES[lang]["no_coupons"], "sources": []}
        lines = [_MESSAGES[lang]["payment_header"]]
        for r in rows:
            line = f"- {_title(r, lang)}"
            payment = r.get(f"payment_method_name_{lang}") or r.get("payment_name_en")
            if payment:
                line += f": {payment}"
            else:
                line += f" ({_MESSAGES[lang]['no_payment']})"
            lines.append(line)
        return {"answer": "\n".join(lines), "sources": []}

    # NEW: merchant contact/location
    def _answer_merchant_location(self, user_id, lang):
        rows = self.list_coupons(user_id, limit=10)
        if not rows:
            return {"answer": _MESSAGES[lang]["no_coupons"], "sources": []}
        lines = [_MESSAGES[lang]["merchant_header"]]
        for r in rows:
            line = f"- {_title(r, lang)} ({r.get('merchant_name', 'Unknown')})"
            parts = []
            address = r.get(f"part_address_{lang}") or r.get("part_address_en")
            if address:
                parts.append(f"Address: {address}")
            phone = r.get("part_tel") or r.get("part_tel2")
            if phone:
                parts.append(f"Phone: {phone}")
            website = r.get("part_website")
            if website:
                parts.append(f"Website: {website}")
            facebook = r.get("part_facebook")
            if facebook:
                parts.append(f"Facebook: {facebook}")
            if parts:
                line += " — " + " | ".join(parts)
            else:
                line += f" ({_MESSAGES[lang]['no_merchant_info']})"
            lines.append(line)
        return {"answer": "\n".join(lines), "sources": []}

    # NEW: offer terms/fine print
    def _answer_terms(self, user_id, lang):
        rows = self.list_coupons(user_id, limit=10)
        if not rows:
            return {"answer": _MESSAGES[lang]["no_coupons"], "sources": []}
        lines = [_MESSAGES[lang]["terms_header"]]
        for r in rows:
            line = f"- {_title(r, lang)}"
            terms = r.get(f"offer_fineprint_{lang}") or r.get(f"waffarha_advice_{lang}")
            if terms:
                line += f": {terms[:300]}"  # truncate long terms
            else:
                line += f" ({_MESSAGES[lang]['no_terms']})"
            lines.append(line)
        return {"answer": "\n".join(lines), "sources": []}