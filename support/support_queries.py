"""Support query service for the main_eg customer-service phase.

Covers what the old personal/catalog split did not: per-user lookups across
ALL order verticals in data/database-sctructure-main-eg.csv, scoped by the
trusted user_id from core/identity.py:

  - deals vouchers  (fct_coupons, by voucher_sn / order_id)
  - bill payments   (fct_bp_orders + bp_orders failure detail)
  - medical orders  (fct_medical_orders + medical_orders reasons/dates)
  - trip orders     (fct_trip_orders + trip_orders + trip_refunds)
  - gift vouchers   (fct_gift_vouchers + gift_vouchers + gift_refunds)
  - refunds         (per-vertical refund tables + refund_date/refunded_at)

Public catalog questions (offers, prices, merchants) stay in
catalog/catalog_queries.py. This module handles "my stuff + my problems".

Security: every per-user SQL is parameterized and scoped by user_id from
identity resolution, never from user text. Voucher/order identifiers found
in free text are used only as additional filters INSIDE the user scope,
so one user can never enumerate another user's orders.
"""

import logging
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import config

log = logging.getLogger("waffarha-app")

SUPPORT_ERROR = {
    "en": "I had trouble reaching your order data right now. Please try again in a moment.",
    "ar": "حصلت مشكلة في الوصول لبيانات طلباتك دلوقتي. جرب تاني بعد شوية.",
}

_NEEDS_AUTH = {
    "en": "To look up your orders or vouchers I need you signed in. Please log in and try again.",
    "ar": "عشان أشوف طلباتك أو القسايم لازم تكون مسجل دخول. سجل دخول وحاول تاني.",
}

# --- Intent detection -------------------------------------------------------
_REFUND_PATTERNS = [
    r"\brefund(s|ed)?\b", r"\bmoney\s*back\b", r"\breturned\b",
    r"استرجاع|استرداد|ريفند|رجع.*فلوس|فلوسي.*رجعت|عايز.*فلوسي",
]
_REFUND_RE = [re.compile(p, re.IGNORECASE) for p in _REFUND_PATTERNS]

_VOUCHER_PATTERNS = [
    r"\bvoucher(s)?\b", r"\bcoupon(s)?\b", r"\bvoucher_sn\b", r"\bqr\b",
    r"قسيم|كوبون|فاوتشر|باركود",
]
_VOUCHER_RE = [re.compile(p, re.IGNORECASE) for p in _VOUCHER_PATTERNS]

_ORDER_PATTERNS = [
    r"\border(s)?\b", r"\border\s*(id|number|no)\b", r"\border\s*status\b",
    r"\bmy\s+(bill|payment|trip|travel|medical|gift)\b",
    r"\bfailed\b", r"\bfailure\b", r"\bnot\s+(received|arrived|working)\b",
    r"\bproblem\b", r"\bissue\b", r"\berror\b", r"\bdeclined\b",
    r"طلب|طلبي|طلباتي|فشل|مشكلة|مش شغال|موصلش|مدفوع|دفعت.*موصلش",
]
_ORDER_RE = [re.compile(p, re.IGNORECASE) for p in _ORDER_PATTERNS]

_BILL_PATTERNS = [r"\bbill\b", r"\brecharge\b", r"\btop.?up\b", r"فاتورة|شحن.*رصيد|كهربا|ميه|غاز"]
_BILL_RE = [re.compile(p, re.IGNORECASE) for p in _BILL_PATTERNS]

_MEDICAL_PATTERNS = [r"\bmedical\b", r"\bclinic\b", r"\bhospital\b", r"\bpharmacy\b",
                     r"طبي|عيادة|مستشفى|صيدلية|تحاليل|أشعة"]
_MEDICAL_RE = [re.compile(p, re.IGNORECASE) for p in _MEDICAL_PATTERNS]

_TRIP_PATTERNS = [r"\btrip\b", r"\btravel\b", r"\bbus\b", r"\bticket\b", r"\bseat\b",
                  r"سفر|رحلة|أتوبيس|اتوبيس|تذكرة|كرسي|مقعد"]
_TRIP_RE = [re.compile(p, re.IGNORECASE) for p in _TRIP_PATTERNS]

_GIFT_PATTERNS = [r"\bgift\b", r"هدية|ギフト"]
_GIFT_RE = [re.compile(p, re.IGNORECASE) for p in _GIFT_PATTERNS]

# Coupons-only listing: "show my coupons" with no other vertical/trouble/refund
# signal should list vouchers, not the cross-vertical recent-orders mix.
_COUPONS_ONLY_PATTERNS = [
    r"\bmy\s+(coupons?|vouchers?)\b", r"\bhow\s+many\s+coupons\b",
    r"كوبوناتي|قسايمي|عروضي|كام\s+كوبون|عدد\s+كوبونات",
]
_COUPONS_ONLY_RE = [re.compile(p, re.IGNORECASE) for p in _COUPONS_ONLY_PATTERNS]

# Troubleshooting: "my last voucher was not making it through"
_TROUBLE_PATTERNS = [
    r"\blast\s+(voucher|coupon)\b", r"\bnot\s+(working|going\s+through|accepted)\b",
    r"\bwouldn'?t\s+(work|go\s+through|redeem)\b", r"\bcouldn'?t\s+(use|redeem)\b",
    r"\bfailed\s+to\s+redeem\b", r"\bnot\s+making\s+it\s+through\b",
    r"آخر\s+(كوبون|قسيمة)|الكوبون\s+(مش\s+شغال|مشتغلش|ماتقبلش)|القسيمة\s+(مش\s+شغالة|مشتغلتش)",
    r"مش\s+عايز\s+يتقبل|بيترفض|اترفض",
]
_TROUBLE_RE = [re.compile(p, re.IGNORECASE) for p in _TROUBLE_PATTERNS]

# Refund missing: "i applied for a refund but i didn't get it"
_REFUND_MISSING_PATTERNS = [
    r"\brefund\b.*\b(not|n'?t|never)\b.*\b(received|arrived|got|refunded|reflected)\b",
    r"\brefund\b.*\b(got\s+nothing|no\s+refund|without\s+refund|but\s+nothing)\b",
    r"\bapplied\s+for\s+.*refund\b", r"\brefund\s+didn'?t\s+(arrive|come)\b",
    r"\bwhere'?s\s+my\s+refund\b", r"\bstill\s+waiting.*refund\b",
    r"قدمت\s+على\s+استرجاع|الاسترجاع\s+(موصلش|مجاش|لسه)|فين\s+فلوسي|فلوسي\s+مرجعتش",
]
_REFUND_MISSING_RE = [re.compile(p, re.IGNORECASE) for p in _REFUND_MISSING_PATTERNS]

# Wait-vs-escalate thresholds, measured on live main_eg 2026-09-28:
# gift refunds complete in median 4h / p90 15h -> wait 2 days, then escalate.
# medical paid->refunded median 2h / p90 4.5d -> wait 5 days.
# trip pending refunds in the data are 118-420 days old -> >30d is stuck.
GIFT_REFUND_WAIT_DAYS = 2
MEDICAL_REFUND_WAIT_DAYS = 5
TRIP_REFUND_ESCALATE_DAYS = 30

# Identifiers customers paste: order ids, voucher serials.
_ORDER_ID_RE = re.compile(r"\border\s*(?:id|number|no\.?|#)?\s*[:#]?\s*(\d{4,20})", re.IGNORECASE)
_VOUCHER_SN_RE = re.compile(r"(?:voucher|قسيم\w*|كوبون)\s*(?:sn|number|no\.?|#|رقم)?\s*[:#]?\s*([A-Za-z0-9-]{4,40})", re.IGNORECASE)
_BARE_LONG_ID_RE = re.compile(r"\b(\d{6,20})\b")


def is_support_query(query: str) -> bool:
    q = query or ""
    return any(r.search(q) for r in (_REFUND_RE + _VOUCHER_RE + _ORDER_RE + _TROUBLE_RE))


def _is_refund(q: str) -> bool:
    return any(r.search(q) for r in _REFUND_RE)


def _vertical(q: str) -> str | None:
    if any(r.search(q) for r in _BILL_RE):
        return "bill"
    if any(r.search(q) for r in _MEDICAL_RE):
        return "medical"
    if any(r.search(q) for r in _TRIP_RE):
        return "trip"
    if any(r.search(q) for r in _GIFT_RE):
        return "gift"
    return None


def extract_identifiers(query: str) -> dict:
    q = query or ""
    out: dict = {}
    m = _ORDER_ID_RE.search(q)
    if m:
        out["order_id"] = m.group(1)
    m = _VOUCHER_SN_RE.search(q)
    if m:
        out["voucher_sn"] = m.group(1)
    if not out:
        m = _BARE_LONG_ID_RE.search(q)
        if m:
            out["maybe_id"] = m.group(1)
    return out


_MESSAGES = {
    "en": {
        "no_orders": "I couldn't find any orders on your account for that.",
        "refund_header": "Here's what I found about your refunds:",
        "orders_header": "Here are your recent orders:",
        "voucher_header": "Here's what I found for your voucher:",
        "voucher_not_found": "I couldn't find that voucher on your account. Double-check the voucher code.",
        "order_not_found": "I couldn't find that order on your account. Double-check the order number.",
        "no_refunds": "I don't see any refunds on your account yet.",
        "last_voucher_header": "Here's your most recent voucher:",
        "no_vouchers": "I couldn't find any vouchers on your account yet.",
        "timeline_header": "Here's the timeline for your refund:",
        "timeline_none": ("I don't see a refund request on your account. If you want one, "
                          "tell me which order or voucher it's for and I'll check whether it's refundable."),
        "escalate": ("This looks stuck — I'm flagging it for a human agent. "
                     "Please contact support with this reference so they can push it through."),
        "wait": ("It's still within the normal processing window. If it hasn't arrived by {date}, "
                 "contact us again and we'll escalate it."),
    },
    "ar": {
        "no_orders": "ملقتش طلبات على حسابك للحاجة دي.",
        "refund_header": "دي نتيجة البحث عن الاسترجاعات بتاعتك:",
        "orders_header": "دي أحدث طلباتك:",
        "voucher_header": "دي نتيجة البحث عن القسيمة:",
        "voucher_not_found": "ملقتش القسيمة دي على حسابك. اتأكد من كود القسيمة.",
        "order_not_found": "ملقتش الطلب ده على حسابك. اتأكد من رقم الطلب.",
        "no_refunds": "مفيش استرجاعات على حسابك لحد دلوقتي.",
        "last_voucher_header": "دي آخر قسيمة عندك:",
        "no_vouchers": "ملقتش أي قسايم على حسابك لحد دلوقتي.",
        "timeline_header": "ده التسلسل الزمني للاسترجاع بتاعك:",
        "timeline_none": ("مش لاقي طلب استرجاع على حسابك. لو عايز تطلب استرجاع، "
                          "قولي رقم الطلب أو القسيمة وأنا أتأكد إذا كانت مسترجعة ولا لأ."),
        "escalate": ("الموضوع ده شكله معلق — هحوّله لموظف بشري. "
                     "تواصل مع الدعم بالرقم المرجعي ده عشان يخلصوهولك."),
        "wait": ("لسه في مدة المعالجة الطبيعية. لو موصلش لحد {date}، "
                 "كلمنا تاني وهنصعّد الموضوع."),
    },
}


def _short(v) -> str:
    if not v:
        return ""
    return str(v)[:16] if len(str(v)) > 16 else str(v)


def _date(v) -> str:
    if not v:
        return ""
    return str(v)[:10]


def _today_str() -> str:
    from datetime import date
    return date.today().isoformat()


def _age_days(v) -> int | None:
    """Whole days from a datetime value to today; None when unparseable."""
    if not v:
        return None
    try:
        from datetime import datetime, date
        if isinstance(v, str):
            d = datetime.fromisoformat(str(v)[:19]).date()
        elif isinstance(v, datetime):
            d = v.date()
        elif isinstance(v, date):
            d = v
        else:
            return None
        return (date.today() - d).days
    except (ValueError, TypeError):
        return None


# Status labels decoded from live main_eg data (2026-09-28). Numeric gift
# codes have no lookup table; labels below are grounded in paid_at/used_at/
# refund_date/done_at evidence, not guesses.
_GIFT_ORDER_STATUS = {
    1: {"en": "paid", "ar": "مدفوعة"},
    2: {"en": "paid", "ar": "مدفوعة"},
    3: {"en": "awaiting payment", "ar": "في انتظار الدفع"},
    4: {"en": "cancelled", "ar": "ملغي"},
    5: {"en": "paid", "ar": "مدفوعة"},
}
_GIFT_VOUCHER_STATUS = {
    # fct_gift_vouchers.status is a string already (Used/Refunded/Paid/Cancelled);
    # raw gift_vouchers.status codes map as below.
    1: {"en": "valid", "ar": "صالحة"},
    2: {"en": "used", "ar": "مستخدمة"},
    3: {"en": "refunded", "ar": "مسترجعة"},
    5: {"en": "cancelled", "ar": "ملغية"},
}
_GIFT_REFUND_STATUS = {
    1: {"en": "completed", "ar": "مكتملة"},
    3: {"en": "cancelled", "ar": "ملغية"},
    5: {"en": "pending", "ar": "قيد الانتظار"},
}


def _gift_status_label(table: dict, code, lang: str) -> str:
    try:
        key = int(code)
    except (TypeError, ValueError):
        return str(code) if code is not None else ""
    return table.get(key, {}).get(lang, str(code))


class SupportQueryService:
    """User-scoped lookups across all main_eg order verticals."""

    def __init__(self, client=None):
        self._client = client

    def _get_client(self):
        if self._client is None:
            self._client = config.get_clickhouse_client()
        return self._client

    def _rows(self, sql, params):
        return [dict(r) for r in self._get_client().query(sql, parameters=params).named_results()]

    def _limit(self) -> int:
        try:
            return max(1, min(int(getattr(config, "SUPPORT_ORDER_LIMIT", 5)), 20))
        except (TypeError, ValueError):
            return 5

    # -- per-vertical listings (all scoped by user_id) -----------------------
    def recent_coupons(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT coupon_id, offer_id, voucher_sn, merchant_name, status_v2,
                       coupon_sold_price, total_price, created_at, active_at, expire_at
                FROM {db}.fct_coupons WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    def recent_bp_orders(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT id, service_name, category, status, failure_reason,
                       amount, requested_amount, created_at, paid_at
                FROM {db}.fct_bp_orders WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    def recent_medical_orders(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT order_id, order_status, category_en, provider_en, branch_en,
                       service_names_en, amount, created_at
                FROM {db}.fct_medical_orders WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    def recent_trip_orders(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT order_id, status, provider_en, departure_station_en,
                       arrival_station_en, travel_date, amount, created_at
                FROM {db}.fct_trip_orders WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    def recent_gift_vouchers(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT id, order_id, voucher, status, merchant_name, branch_name,
                       requested_amount, created_at, used_at
                FROM {db}.fct_gift_vouchers WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    def gift_refunds(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT id, order_id, status, total, deduction, refund_reason,
                       created_at, done_at, cancelled_at
                FROM {db}.gift_refunds WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    def trip_refunds(self, user_id, limit=None):
        lim = limit or self._limit()
        db = config.CLICKHOUSE_DATABASE
        return self._rows(
            f"""SELECT id, order_id, status, amount, total, total_deductions,
                       reason_description, created_at
                FROM {db}.trip_refunds WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT %(lim)s""",
            {"uid": user_id, "lim": lim},
        )

    # -- targeted lookups (identifier + user scope) --------------------------
    def lookup_voucher(self, user_id, voucher_sn: str):
        db = config.CLICKHOUSE_DATABASE
        rows = self._rows(
            f"""SELECT coupon_id, offer_id, voucher_sn, merchant_name, status_v2,
                       coupon_sold_price, total_price, created_at, active_at, expire_at
                FROM {db}.fct_coupons
                WHERE user_id = %(uid)s AND voucher_sn = %(vsn)s LIMIT 3""",
            {"uid": user_id, "vsn": voucher_sn},
        )
        if rows:
            return rows
        return self._rows(
            f"""SELECT id, order_id, voucher, status, merchant_name, branch_name,
                       requested_amount, created_at, used_at
                FROM {db}.fct_gift_vouchers
                WHERE user_id = %(uid)s AND voucher = %(vsn)s LIMIT 3""",
            {"uid": user_id, "vsn": voucher_sn},
        )

    def lookup_order(self, user_id, order_id) -> dict:
        """Search all verticals for one order id; returns {vertical: rows}."""
        db = config.CLICKHOUSE_DATABASE
        found: dict = {}
        try:
            oid = int(str(order_id).strip())
        except (TypeError, ValueError):
            return found
        queries = {
            "coupon": (f"SELECT coupon_id AS id, status_v2 AS status, merchant_name,"
                       f" total_price AS amount, created_at FROM {db}.fct_coupons"
                       f" WHERE user_id = %(uid)s AND (order_id = %(oid)s OR coupon_id = %(oid)s) LIMIT 3",
                       {"uid": user_id, "oid": oid}),
            "bill": (f"SELECT id, status, failure_reason, service_name, requested_amount AS amount, created_at"
                     f" FROM {db}.fct_bp_orders WHERE user_id = %(uid)s AND id = %(oid)s LIMIT 3",
                     {"uid": user_id, "oid": oid}),
            "medical": (f"SELECT order_id AS id, order_status AS status, provider_en,"
                        f" service_names_en, amount, created_at FROM {db}.fct_medical_orders"
                        f" WHERE user_id = %(uid)s AND order_id = %(oid)s LIMIT 3",
                        {"uid": user_id, "oid": oid}),
            "trip": (f"SELECT order_id AS id, status, provider_en, amount, created_at"
                     f" FROM {db}.fct_trip_orders WHERE user_id = %(uid)s AND order_id = %(oid)s LIMIT 3",
                     {"uid": user_id, "oid": oid}),
            "gift": (f"SELECT id, order_id, status, merchant_name, requested_amount AS amount, created_at"
                     f" FROM {db}.fct_gift_vouchers WHERE user_id = %(uid)s AND order_id = %(oid)s LIMIT 3",
                     {"uid": user_id, "oid": oid}),
        }
        for vertical, (sql, params) in queries.items():
            try:
                rows = self._rows(sql, params)
            except Exception as e:  # noqa: BLE001 -- one vertical failing must not kill the rest
                log.warning("support lookup_order %s failed: %s", vertical, e)
                continue
            if rows:
                found[vertical] = rows
        return found

    def last_coupon_full(self, user_id):
        """Most recent coupon joined to offer + partner context for diagnosis."""
        db = config.CLICKHOUSE_DATABASE
        rows = self._rows(
            f"""SELECT c.coupon_id, c.offer_id, c.voucher_sn, c.merchant_name,
                       c.status_v2, c.coupon_sold_price, c.total_price,
                       c.created_at, c.active_at, c.expire_at,
                       o.receiving_method, o.not_refundable,
                       o.mobile_offer_title_en, o.mobile_offer_title_ar,
                       o.offer_brief_en, o.offer_brief_ar,
                       pt.part_address_en, pt.part_address_ar,
                       pt.part_tel, pt.part_tel2
                FROM {db}.fct_coupons c
                LEFT JOIN {db}.dim_offers o ON o.offer_id = c.offer_id
                LEFT JOIN {db}.dim_partners pt ON pt.part_id = o.part_id
                WHERE c.user_id = %(uid)s
                ORDER BY c.created_at DESC LIMIT 1""",
            {"uid": user_id},
        )
        return rows[0] if rows else None

    def _answer_last_voucher(self, user_id, lang: str):
        msg = _MESSAGES[lang]
        r = self.last_coupon_full(user_id)
        if not r:
            return {"answer": msg["no_vouchers"], "sources": []}
        if lang == "ar":
            title = (r.get("mobile_offer_title_ar") or r.get("mobile_offer_title_en")
                     or r.get("offer_brief_ar") or r.get("offer_brief_en")
                     or r.get("merchant_name") or f"كوبون {r.get('coupon_id')}")
        else:
            title = (r.get("mobile_offer_title_en") or r.get("mobile_offer_title_ar")
                     or r.get("offer_brief_en") or r.get("offer_brief_ar")
                     or r.get("merchant_name") or f"Coupon {r.get('coupon_id')}")
        vsn = r.get("voucher_sn") or ""
        status = (r.get("status_v2") or "").strip()
        exp = _date(r.get("expire_at"))
        used = _date(r.get("active_at"))
        lines = [msg["last_voucher_header"], f"- {title} ({vsn}): {status}"]
        slow = status.lower()
        if slow == "used":
            lines.append(("It was already redeemed"
                          + (f" on {used}" if used else "")
                          + ". A used voucher can't be used again — if the merchant didn't honor it, "
                             "tell me the order details and I'll flag it.")
                         if lang == "en" else
                         ("القسيمة دي اتستخدمت بالفعل"
                          + (f" يوم {used}" if used else "")
                          + ". القسيمة المستخدمة مينفعش تستخدم تاني — لو التاجر ماقبلهاش، "
                             "قولي تفاصيل الطلب وأنا أحوّل الموضوع."))
        elif slow in ("refund", "refunded"):
            lines.append(("This voucher was refunded. Check your payment method for the amount."
                          if lang == "en" else
                          "القسيمة دي تم استرجاع قيمتها. راجع وسيلة الدفع بتاعتك."))
        elif slow == "canceled":
            lines.append(("This voucher was cancelled."
                          if lang == "en" else "القسيمة دي ملغية."))
        elif exp and exp < _today_str():
            lines.append((f"It expired on {exp}, so it can't be redeemed anymore."
                          if lang == "en" else
                          f"صلاحيتها خلصت يوم {exp}، فمينفعش تستخدم دلوقتي."))
        else:
            how = []
            if r.get("receiving_method"):
                how.append((f"redemption: {r['receiving_method']}")
                           if lang == "en" else f"طريقة الاستلام: {r['receiving_method']}")
            if exp:
                how.append((f"valid until {exp}") if lang == "en" else f"صالحة حتى {exp}")
            phone = r.get("part_tel") or r.get("part_tel2")
            if phone:
                how.append(f"{'Merchant phone' if lang == 'en' else 'تليفون التاجر'}: {phone}")
            if lang == "en":
                lines.append("It looks valid. " + (" — ".join(how) if how else
                             "Show the voucher code at the merchant."))
            else:
                lines.append("شكلها سارية. " + (" — ".join(how) if how else
                             "اعرض كود القسيمة عند التاجر."))
        return {"answer": "\n".join(lines), "sources": []}

    def latest_gift_refund(self, user_id):
        db = config.CLICKHOUSE_DATABASE
        rows = self._rows(
            f"""SELECT id, order_id, status, total, deduction, refund_reason,
                       created_at, done_at, cancelled_at
                FROM {db}.gift_refunds WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT 1""",
            {"uid": user_id},
        )
        return rows[0] if rows else None

    def latest_trip_refund(self, user_id):
        db = config.CLICKHOUSE_DATABASE
        rows = self._rows(
            f"""SELECT id, order_id, status, amount, total, total_deductions,
                       reason_description, created_at
                FROM {db}.trip_refunds WHERE user_id = %(uid)s
                ORDER BY created_at DESC LIMIT 1""",
            {"uid": user_id},
        )
        return rows[0] if rows else None

    def latest_refunded_coupon(self, user_id):
        db = config.CLICKHOUSE_DATABASE
        rows = self._rows(
            f"""SELECT coupon_id, voucher_sn, merchant_name, status_v2, total_price,
                       created_at, expire_at
                FROM {db}.fct_coupons
                WHERE user_id = %(uid)s AND status_v2 = 'Refund'
                ORDER BY created_at DESC LIMIT 1""",
            {"uid": user_id},
        )
        return rows[0] if rows else None

    def latest_medical_refund(self, user_id):
        db = config.CLICKHOUSE_DATABASE
        rows = self._rows(
            f"""SELECT id, status, refunded_at, paid_at, total, provider_id
                FROM {db}.medical_orders
                WHERE user_id = %(uid)s AND refunded_at IS NOT NULL
                ORDER BY refunded_at DESC LIMIT 1""",
            {"uid": user_id},
        )
        return rows[0] if rows else None

    def _wait_until(self, created, wait_days: int) -> str:
        try:
            from datetime import datetime, timedelta
            base = (datetime.fromisoformat(str(created)[:19])
                    if isinstance(created, str) else created)
            return (base + timedelta(days=wait_days)).strftime("%Y-%m-%d")
        except (ValueError, TypeError, AttributeError):
            return f"{wait_days} days from the request"

    def _answer_refund_timeline(self, user_id, lang: str, vertical: str | None):
        msg = _MESSAGES[lang]
        lines = [msg["timeline_header"]]
        found = False

        def _check(label: str, row, kind: str):
            nonlocal found
            if not row:
                return
            found = True
            if kind == "gift":
                st = _gift_status_label(_GIFT_REFUND_STATUS, row.get("status"), lang)
                if int(row.get("status") or 0) == 1:
                    lines.append(
                        f"- {label} #{row.get('id')} (order {row.get('order_id')}): {st}"
                        + (f" on {_date(row.get('done_at'))}" if row.get("done_at") else "")
                        + f" — total={row.get('total')} deduction={row.get('deduction')}. "
                        + (("The money was sent back to your original payment method."
                             " If it's not there, contact support with this reference.")
                            if lang == "en" else
                            ("الفلوس اتبعتت لوسيلة الدفع الأصلية بتاعتك."
                             " لو مش ظاهرة، تواصل مع الدعم بالرقم المرجعي ده.")))
                elif int(row.get("status") or 0) == 5:
                    age = _age_days(row.get("created_at"))
                    if age is not None and age >= GIFT_REFUND_WAIT_DAYS:
                        lines.append(f"- {label} #{row.get('id')}: {st}. " + msg["escalate"])
                    else:
                        lines.append(f"- {label} #{row.get('id')}: {st}. " + msg["wait"].format(
                            date=self._wait_until(row.get("created_at"), GIFT_REFUND_WAIT_DAYS)))
                else:
                    lines.append(f"- {label} #{row.get('id')}: {st}. " + (
                        "This refund request was cancelled. Tell me the order number if you want to request it again."
                        if lang == "en" else
                        "طلب الاسترجاع ده اتلغى. قولي رقم الطلب لو عايز تطلبه تاني."))
            elif kind == "trip":
                st = (row.get("status") or "").strip().lower()
                if st == "approved":
                    lines.append(f"- {label} #{row.get('id')} (order {row.get('order_id')}): "
                                 f"{row.get('status')} — total={row.get('total')}. " + (
                        "Approved and processed back to your payment method."
                        if lang == "en" else "اتقبل واتنفذ على وسيلة الدفع بتاعتك."))
                elif st == "pending":
                    age = _age_days(row.get("created_at")) or 0
                    if age >= TRIP_REFUND_ESCALATE_DAYS:
                        lines.append(f"- {label} #{row.get('id')} (order {row.get('order_id')}): "
                                     f"{row.get('status')} ({age} days). " + msg["escalate"])
                    else:
                        lines.append(f"- {label} #{row.get('id')} (order {row.get('order_id')}): "
                                     f"{row.get('status')}. " + (
                            "It's under review by the trip provider."
                            if lang == "en" else "الطلب قيد المراجعة من شركة الرحلات."))
                else:
                    lines.append(f"- {label} #{row.get('id')}: {row.get('status')}"
                                 + (f" — {row.get('reason_description')}" if row.get("reason_description") else ""))
            elif kind == "coupon":
                lines.append(f"- {label} {row.get('voucher_sn') or row.get('coupon_id')}: "
                             f"{row.get('status_v2')}. " + (
                    "Recorded as refunded — check your original payment method for the amount."
                    if lang == "en" else
                    "متسجل أنه مسترجع — راجع وسيلة الدفع الأصلية بتاعتك."))
            elif kind == "medical":
                lines.append(f"- {label} #{row.get('id')}: refunded on {_date(row.get('refunded_at'))}"
                             f" — total={row.get('total')}. " + (
                    "Check your original payment method."
                    if lang == "en" else "راجع وسيلة الدفع الأصلية بتاعتك."))

        if vertical in (None, "gift"):
            try:
                _check("gift refund", self.latest_gift_refund(user_id), "gift")
            except Exception as e:  # noqa: BLE001
                log.warning("refund timeline gift failed: %s", e)
        if vertical in (None, "trip"):
            try:
                _check("trip refund", self.latest_trip_refund(user_id), "trip")
            except Exception as e:  # noqa: BLE001
                log.warning("refund timeline trip failed: %s", e)
        if vertical in (None, "medical"):
            try:
                _check("medical order", self.latest_medical_refund(user_id), "medical")
            except Exception as e:  # noqa: BLE001
                log.warning("refund timeline medical failed: %s", e)
        if vertical is None:
            try:
                _check("coupon", self.latest_refunded_coupon(user_id), "coupon")
            except Exception as e:  # noqa: BLE001
                log.warning("refund timeline coupon failed: %s", e)
        if not found:
            return {"answer": msg["timeline_none"], "sources": []}
        return {"answer": "\n".join(lines), "sources": []}

    # -- entry point ----------------------------------------------------------
    def handle(self, query, user_id, lang: str):
        lang = "ar" if lang == "ar" else "en"
        if user_id is None:
            return {"answer": _NEEDS_AUTH[lang], "sources": []}
        q = query or ""
        ql = q.lower()
        ids = extract_identifiers(q)
        vertical = _vertical(ql)
        try:
            if ids.get("voucher_sn"):
                return self._answer_voucher(user_id, ids["voucher_sn"], lang)
            oid = ids.get("order_id") or ids.get("maybe_id")
            if oid and (any(k in ql for k in ("order", "طلب", "voucher", "قسيم", "كوبون", "refund", "استرجاع")) or oid != ids.get("maybe_id")):
                return self._answer_order(user_id, oid, lang)
            if any(r.search(q) for r in _TROUBLE_RE):
                return self._answer_last_voucher(user_id, lang)
            if (vertical is None and not _is_refund(ql)
                    and any(r.search(q) for r in _COUPONS_ONLY_RE)):
                return self._answer_coupons_only(user_id, lang)
            if any(r.search(q) for r in _REFUND_MISSING_RE):
                return self._answer_refund_timeline(user_id, lang, vertical)
            if _is_refund(ql):
                return self._answer_refunds(user_id, lang, vertical)
            return self._answer_recent(user_id, lang, vertical)
        except Exception as e:  # noqa: BLE001
            log.warning("support query failed for user_id=%s: %s", user_id, e)
            return {"answer": SUPPORT_ERROR[lang], "sources": []}

    # -- answers ----------------------------------------------------------------
    def _answer_voucher(self, user_id, voucher_sn: str, lang: str):
        rows = self.lookup_voucher(user_id, voucher_sn)
        msg = _MESSAGES[lang]
        if not rows:
            return {"answer": msg["voucher_not_found"], "sources": []}
        lines = [msg["voucher_header"]]
        for r in rows[:3]:
            name = r.get("merchant_name") or r.get("voucher") or r.get("voucher_sn") or f"#{r.get('coupon_id') or r.get('id')}"
            status = r.get("status_v2") or r.get("status") or ""
            exp = _date(r.get("expire_at"))
            tail = f": {status}" if status else ""
            if exp:
                tail += f" ({'valid until' if lang == 'en' else 'صالح حتى'} {exp})"
            lines.append(f"- {_short(name)}{tail}")
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_order(self, user_id, order_id, lang: str):
        found = self.lookup_order(user_id, order_id)
        msg = _MESSAGES[lang]
        if not found:
            return {"answer": msg["order_not_found"], "sources": []}
        lines = [msg["orders_header"]]
        for vertical, rows in found.items():
            for r in rows[:2]:
                status = r.get("status") or ""
                # pending in trip/bp means created-but-unpaid, never a failure.
                if vertical in ("trip", "bill") and str(status).lower() == "pending":
                    status = ("pending — not paid yet" if lang == "en"
                              else "في الانتظار — لم يتم الدفع بعد")
                merch = r.get("merchant_name") or r.get("service_name") or r.get("provider_en") or vertical
                fail = r.get("failure_reason")
                line = f"- #{r.get('id') or r.get('order_id') or order_id} ({vertical}) {merch}: {status}"
                if fail:
                    line += f" — {fail}" if lang == "en" else f" — السبب: {fail}"
                lines.append(line.strip())
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_refunds(self, user_id, lang: str, vertical: str | None):
        msg = _MESSAGES[lang]
        lines = [msg["refund_header"]]
        any_found = False
        if vertical in (None, "gift"):
            try:
                grows = self.gift_refunds(user_id)
            except Exception as e:  # noqa: BLE001
                log.warning("gift_refunds failed: %s", e)
                grows = []
            for r in grows[:3]:
                any_found = True
                status = _gift_status_label(_GIFT_REFUND_STATUS, r.get("status"), lang)
                lines.append(f"- gift refund #{r.get('id')} (order {r.get('order_id')}): "
                             f"{status} total={r.get('total')} deduction={r.get('deduction')}")
        if vertical in (None, "trip"):
            try:
                trows = self.trip_refunds(user_id)
            except Exception as e:  # noqa: BLE001
                log.warning("trip_refunds failed: %s", e)
                trows = []
            for r in trows[:3]:
                any_found = True
                lines.append(f"- trip refund #{r.get('id')} (order {r.get('order_id')}): "
                             f"{r.get('status')} total={r.get('total')}")
        if not any_found:
            # Fall back to coupon-level refund signal when no refund rows exist.
            try:
                coups = self.recent_coupons(user_id)
            except Exception:  # noqa: BLE001
                coups = []
            refunded = [c for c in coups if (c.get("status_v2") or "").lower().find("refund") >= 0]
            for c in refunded[:3]:
                any_found = True
                lines.append(f"- coupon {c.get('voucher_sn') or c.get('coupon_id')}: {c.get('status_v2')}")
        if not any_found:
            return {"answer": msg["no_refunds"], "sources": []}
        return {"answer": "\n".join(lines), "sources": []}

    def _answer_recent(self, user_id, lang: str, vertical: str | None):
        msg = _MESSAGES[lang]
        lines = [msg["orders_header"]]
        buckets: list[tuple[str, list]] = []
        try:
            if vertical in (None, "bill"):
                buckets.append(("bill", self.recent_bp_orders(user_id)))
            if vertical in (None, "medical"):
                buckets.append(("medical", self.recent_medical_orders(user_id)))
            if vertical in (None, "trip"):
                buckets.append(("trip", self.recent_trip_orders(user_id)))
            if vertical in (None, "gift"):
                buckets.append(("gift", self.recent_gift_vouchers(user_id)))
            if vertical is None:
                buckets.append(("coupon", self.recent_coupons(user_id)))
        except Exception as e:  # noqa: BLE001
            log.warning("support recent failed: %s", e)
            return {"answer": SUPPORT_ERROR[lang], "sources": []}
        shown = 0
        for name, rows in buckets:
            for r in rows[:2]:
                status = r.get("status") or r.get("status_v2") or r.get("order_status") or ""
                oid = r.get("id") or r.get("order_id") or r.get("coupon_id") or ""
                merch = (r.get("merchant_name") or r.get("service_name")
                         or r.get("provider_en") or r.get("category_en") or name)
                lines.append(f"- #{oid} ({name}) {merch}: {status} {_date(r.get('created_at'))}".strip())
                shown += 1
                if shown >= 5:
                    break
            if shown >= 5:
                break
        if shown == 0:
            return {"answer": msg["no_orders"], "sources": []}
        return {"answer": "\n".join(lines), "sources": []}
