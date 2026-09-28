"""Unit tests for the main_eg customer-service phase (no live DB needed)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from support.support_queries import (
    SupportQueryService,
    is_support_query,
    extract_identifiers,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def named_results(self):
        return iter(self._rows)


class _FakeClient:
    def __init__(self):
        self.queries = []

    def query(self, sql, parameters=None):
        self.queries.append((sql, dict(parameters or {})))
        # Route by table name so each vertical returns a shaped row.
        if "gift_refunds" in sql:
            return _FakeResult([{"id": 7, "order_id": 99, "status": 1,
                                 "total": 200.0, "deduction": 10.0,
                                 "refund_reason": "user asked",
                                 "created_at": "2026-09-01 10:00:00",
                                 "done_at": None, "cancelled_at": None}])
        if "trip_refunds" in sql:
            return _FakeResult([])
        if "fct_coupons" in sql and "voucher_sn = " in sql:
            return _FakeResult([{"coupon_id": 1, "offer_id": 10, "voucher_sn": "ABC123",
                                 "merchant_name": "KFC", "status_v2": "active",
                                 "coupon_sold_price": 100.0, "total_price": 100.0,
                                 "created_at": "2026-09-01", "active_at": None,
                                 "expire_at": "2026-10-01"}])
        if "fct_coupons" in sql:
            return _FakeResult([{"coupon_id": 1, "offer_id": 10, "voucher_sn": "ABC123",
                                 "merchant_name": "KFC", "status_v2": "active",
                                 "coupon_sold_price": 100.0, "total_price": 100.0,
                                 "created_at": "2026-09-01", "active_at": None,
                                 "expire_at": "2026-10-01"}])
        if "fct_bp_orders" in sql:
            return _FakeResult([{"id": 55, "service_name": "Electricity",
                                 "category": "bills", "status": "failed",
                                 "failure_reason": "provider timeout",
                                 "amount": 50.0, "total": 52.0,
                                 "created_at": "2026-09-02", "paid_at": "2026-09-02"}])
        if "fct_trip_orders" in sql and "order_id = " in sql:
            return _FakeResult([{"id": 123, "status": "paid", "provider_en": "GoBus",
                                 "amount": 300.0, "created_at": "2026-09-03"}])
        if "fct_trip_orders" in sql:
            return _FakeResult([])
        if "fct_medical_orders" in sql:
            return _FakeResult([])
        if "fct_gift_vouchers" in sql:
            return _FakeResult([])
        return _FakeResult([])


def test_is_support_query_covers_cs_intents():
    assert is_support_query("where is my refund?")
    assert is_support_query("فين الاسترجاع بتاعي؟")
    assert is_support_query("my voucher ABC123 is not working")
    assert is_support_query("order 123 failed but I was charged")
    assert not is_support_query("what KFC offers do you have?")


def test_extract_identifiers():
    assert extract_identifiers("order 123456")["order_id"] == "123456"
    assert extract_identifiers("voucher ABC-123")["voucher_sn"] == "ABC-123"


def test_support_needs_auth():
    svc = SupportQueryService(client=_FakeClient())
    out = svc.handle("where is my refund?", None, "en")
    assert "signed in" in out["answer"].lower() or "log in" in out["answer"].lower()


def test_support_refund_uses_scoped_query():
    fake = _FakeClient()
    svc = SupportQueryService(client=fake)
    out = svc.handle("where is my refund?", 42, "en")
    assert "gift refund" in out["answer"].lower()
    # every per-user query must carry the trusted uid, never a client id
    assert fake.queries
    for _, params in fake.queries:
        assert params.get("uid") == 42


def test_support_voucher_lookup_scoped():
    fake = _FakeClient()
    svc = SupportQueryService(client=fake)
    out = svc.handle("my voucher ABC123", 7, "en")
    assert "KFC" in out["answer"] or "ABC123" in out["answer"]
    vsn_queries = [p for _, p in fake.queries if p.get("vsn") == "ABC123"]
    assert vsn_queries and all(p.get("uid") == 7 for p in vsn_queries)


def test_support_order_lookup_across_verticals():
    fake = _FakeClient()
    svc = SupportQueryService(client=fake)
    out = svc.handle("order 123456 failed", 9, "en")
    assert "123456" in out["answer"] or "paid" in out["answer"].lower()
    oids = [p.get("oid") for _, p in fake.queries if "oid" in p]
    assert oids and all(isinstance(o, int) for o in oids)


def test_support_tool_needs_user():
    from agent.tools.support_tools import SupportLookupTool
    from agent.tools import ToolContext

    class _Facade:
        pass

    ctx = ToolContext(facade=_Facade(), query="where is my refund?",
                      reply_lang="en", history=[], recent_offers=[],
                      normalized_query=None, user_id=None)
    res = SupportLookupTool().run(ctx, {})
    assert res.ok and res.note.get("kind") == "no_user"


def test_ch_table_uses_configured_database():
    from core import config
    old = config.CLICKHOUSE_DATABASE
    try:
        config.CLICKHOUSE_DATABASE = "main_eg"
        assert config.ch_table("dim_offers") == "main_eg.dim_offers"
        from catalog.catalog_queries import _base_select
        assert "main_eg.dim_offers" in _base_select()
    finally:
        config.CLICKHOUSE_DATABASE = old


class _TimelineFake(_FakeClient):
    """Fake with gift/trip refund rows + a used last coupon."""

    def query(self, sql, parameters=None):
        self.queries.append((sql, dict(parameters or {})))
        if "dim_offers o ON" in sql and "ORDER BY c.created_at DESC LIMIT 1" in sql:
            return _FakeResult([{"coupon_id": 9, "offer_id": 11, "voucher_sn": "VV-1",
                                 "merchant_name": "KFC", "status_v2": "Used",
                                 "coupon_sold_price": 50.0, "total_price": 50.0,
                                 "created_at": "2026-09-01 10:00:00",
                                 "active_at": "2026-09-02 12:00:00",
                                 "expire_at": "2026-10-01 00:00:00",
                                 "receiving_method": "in_store",
                                 "not_refundable": 0,
                                 "mobile_offer_title_en": None,
                                 "mobile_offer_title_ar": None,
                                 "offer_brief_en": "Chicken meal",
                                 "offer_brief_ar": None,
                                 "part_address_en": None, "part_address_ar": None,
                                 "part_tel": "19019", "part_tel2": None}])
        if "FROM unknown_db" in sql:
            return _FakeResult([])
        return super().query(sql, parameters)


def test_last_voucher_used_explains():
    svc = SupportQueryService(client=_TimelineFake())
    out = svc.handle("my last voucher is not working", 7, "en")
    assert "already redeemed" in out["answer"].lower()
    assert "VV-1" in out["answer"]


def test_refund_timeline_gift_completed():
    svc = SupportQueryService(client=_FakeClient())
    out = svc.handle("i applied for a refund but i didn't get it", 42, "en")
    # _FakeClient gift_refunds row is status 1 (completed) with done info
    assert "completed" in out["answer"].lower()
    assert "14431" in out["answer"] or "gift refund" in out["answer"].lower()


def test_refund_timeline_none_when_no_rows():
    class _Empty:
        def query(self, sql, parameters=None):
            class _R:
                def named_results(self):
                    return iter([])
            return _R()
    svc = SupportQueryService(client=_Empty())
    out = svc.handle("where is my refund, it never arrived", 42, "en")
    assert "don't see a refund" in out["answer"].lower()


def test_refund_timeline_trip_pending_old_escalates():
    class _TripOld:
        def __init__(self):
            self.queries = []

        def query(self, sql, parameters=None):
            self.queries.append((sql, dict(parameters or {})))
            from datetime import datetime, timedelta
            old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")

            class _R:
                def __init__(self, rows):
                    self._rows = rows

                def named_results(self):
                    return iter(self._rows)
            if "trip_refunds" in sql:
                return _R([{"id": 120, "order_id": 999, "status": "pending",
                            "amount": 300.0, "total": 290.0, "total_deductions": 10.0,
                            "reason_description": None, "created_at": old}])
            return _R([])
    svc = SupportQueryService(client=_TripOld())
    out = svc.handle("i applied for a trip refund but got nothing", 42, "en")
    assert "human agent" in out["answer"].lower() or "flagging" in out["answer"].lower()


def test_catalog_place_detection():
    from catalog.catalog_queries import _detect_place, is_catalog_query, _detect_intent
    assert _detect_place("offers in Cairo")[0] == 1
    assert _detect_place("عروض اسكندرية")[0] == 2
    assert _detect_place("deals near Maadi")[0] == 1
    assert _detect_place("KFC offers") is None
    assert is_catalog_query("عروض اسكندرية")
    assert is_catalog_query("offers ending soon")
    assert _detect_intent("offers ending soon")["type"] == "ending_soon"
    assert _detect_intent("عروض هتبدأ قريب")["type"] == "starting_soon"
    d = _detect_intent("KFC offers in Cairo")
    assert d["type"] == "merchant" and d["place_id"] == 1
    d2 = _detect_intent("عروض القاهرة")
    assert d2["type"] == "place" and d2["place_id"] == 1
