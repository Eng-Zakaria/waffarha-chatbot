#!/usr/bin/env python3
"""Fast, offline unit tests for the agent tool registry and the Stage-2
catalog tools (agent/tools/). Uses a layered fake faceted/retrieval facade --
no index, no Ollama."""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


from typing import ClassVar

from agent.tools import Tool, ToolContext, ToolRegistry, ToolResult
from agent.tools.catalog_tools import (
    GetOfferTool,
    SearchOffersTool,
    _fresh_only,
    register_catalog_tools,
)


class _FakeFaceted:
    """Mimics the subset of FacetedCatalog the tools use"""

    def __init__(self, merchants, product_pairs, category_offers, all_price):
        self._merchants = merchants          # {canonical: exist}
        self._products = product_pairs       # {key: (exists, [items])}
        self._categories = category_offers   # {category_key: [items]}
        self._all_price = all_price          # [items]

    def resolve_merchants(self, name):
        if name in self._merchants:
            return [name]
        return []

    def offers_for_merchant(self, canonical, lang, limit, exclude_ids=None,
                            price_filter=None):
        items = self._merchants[canonical]
        return self._slice(items, limit, exclude_ids, price_filter)

    def unknown_merchant_mention(self, text):
        return "SomeMall Restaurant" if "somemall" in text.lower() else None

    def resolve_product(self, key):
        return key if key in self._products else None

    def offers_for_product(self, key, lang, limit, exclude_ids=None,
                           price_filter=None):
        exists, items = self._products[key]
        if not exists:
            return []
        return self._slice(items, limit, exclude_ids, price_filter)

    def resolve_category(self, key):
        return key if key in self._categories else None

    def offers_for_category(self, key, lang, limit, price_filter=None):
        return self._slice(self._categories[key], limit, None, price_filter)

    def in_price_range(self, lo, hi, lang, limit):
        return [i for i in self._all_price if _price(i) is not None
                and (lo is None or _price(i) >= lo)
                and (hi is None or _price(i) <= hi)][:limit]

    @staticmethod
    def _slice(items, limit, exclude_ids, price_filter):
        out = []
        for it in items:
            mid = it["metadata"]
            if exclude_ids and mid.get("id") in exclude_ids:
                continue
            if price_filter:
                p = _price(mid)
                lo, hi = price_filter
                if p is None or (lo is not None and p < lo) or (hi is not None and p > hi):
                    continue
            out.append(it)
            if len(out) >= limit:
                break
        return out


class _FakeFacade:
    """Mimics the RagEngine surface the engine + tools touch."""

    def __init__(self, retrieve_results=None):
        kfc = [_offer("10", "KFC", "Zinger meal", 100, sold=900),
               _offer("11", "KFC", "Fried chicken", 200, sold=800)]
        mcd = [_offer("20", "McDonald's", "Big sandwich", 150, sold=700)]
        ph = [_offer("30", "Pizza Hut", "Medium pizza", 250, sold=600),
              _offer("31", "Pizza Hut", "Large pizza combo", 90, sold=500)]
        self.faceted = _FakeFaceted(
            merchants={"KFC": kfc, "McDonald's": mcd, "Pizza Hut": ph},
            product_pairs={"zinger": (True, kfc[:1])},
            category_offers={"food": kfc + mcd + ph},
            all_price=kfc + mcd + ph,
        )
        self._docs = {("offer", o["metadata"]["id"]): o for o in kfc + mcd + ph}
        self._retrieve = retrieve_results or None

    def retrieve(self, query, top_k=8, history=None, recent_offers=None,
                 normalized_query=None):
        if self._retrieve is not None:
            return self._retrieve
        return list(self._docs.values())

    def _lookup_doc(self, source, doc_id, lang_hint=None):
        return self._docs.get((source, doc_id))


def _offer(oid, merchant, text, price, sold=0):
    return {
        "metadata": {
            "source": "offer", "id": oid, "lang": "en",
            "merchant": merchant, "title": text, "text": text,
            "price": str(price), "old_price": "200", "discount": "50",
            "expiry": "2026-12-31", "sold_count": str(sold),
            "url": f"https://waffarha.test/{oid}",
        },
        "text": f"{merchant} {text}",
    }


def _price(mid):
    try:
        return float(str(mid.get("price", "")).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _ctx(facade):
    return ToolContext(
        facade=facade, reply_lang="en", query="kfc offers",
        normalized_query="kfc offers", history=[], recent_offers=[], user_id=None,
        state=None,
    )


class _FakeState:
    def __init__(self):
        self.tool_calls = 0
        self.metrics = {}


class MyToolNamed(Tool):
    name = "does_not_exist"
    purpose = "test"
    input_schema: ClassVar[dict] = {"limit": {"type": "int", "optional": True}}

    def run(self, ctx, args):
        return ToolResult(ok=True, items=[], summary="n/a")


def test_registry_registers_and_lists():
    reg = register_catalog_tools(ToolRegistry())
    assert set(reg.names()) == {"search_offers", "get_offer"}


def test_registry_rejects_unknown_tool_safely():
    reg = ToolRegistry([SearchOffersTool()])
    res = reg.call("no_such_tool", _ctx(_FakeFacade()), {"query": "x"})
    assert not res.ok
    assert res.error


def test_coerce_drops_unknown_and_coerces_limit():
    reg = ToolRegistry([SearchOffersTool()])
    state = _FakeState()
    ctx = _ctx(_FakeFacade())
    ctx.state = state
    res = reg.call("search_offers", ctx, {"merchant": "KFC", "limit": "3"})
    assert res.ok
    assert len(res.items) == 2  # clamped by data, not by limit
    assert state.tool_calls == 1
    assert res.note["tool"] == "search_offers"


def test_search_offers_by_merchant():
    res = SearchOffersTool().run(_ctx(_FakeFacade()), {"merchant": "KFC", "limit": 6})
    assert res.ok
    ids = {i["metadata"]["id"] for i in res.items}
    assert ids == {"10", "11"}


def test_search_offers_price_filter_respected():
    res = SearchOffersTool().run(
        _ctx(_FakeFacade()), {"merchant": "KFC", "price_range": [150, 999], "limit": 6})
    ids = {i["metadata"]["id"] for i in res.items}
    assert ids == {"11"}


def test_search_offers_unknown_merchant_is_negative():
    # Use a facade with empty retrieval so hybrid fallback also returns nothing,
    # triggering the deterministic unknown-merchant negative.
    class _EmptyRetrieveFacade(_FakeFacade):
        def retrieve(self, query, top_k=8, history=None, recent_offers=None,
                     normalized_query=None):
            return []

    ctx = _ctx(_EmptyRetrieveFacade())
    ctx.query = "somemall restaurant discounts"
    res = SearchOffersTool().run(
        ctx,
        {"merchant": "SomeMall Restaurant", "query": "somemall restaurant discounts",
         "limit": 6})
    assert res.ok
    assert res.items == []
    assert res.note["kind"] == "unknown_merchant"


def test_search_offers_exclude():
    res = SearchOffersTool().run(
        _ctx(_FakeFacade()),
        {"merchant": "KFC", "exclude": ["10"], "limit": 6})
    assert [i["metadata"]["id"] for i in res.items] == ["11"]


def test_get_offer_hit_and_miss():
    facade = _FakeFacade()
    hit = GetOfferTool().run(_ctx(facade), {"id": "10"})
    assert hit.ok and hit.items[0]["metadata"]["id"] == "10"
    miss = GetOfferTool().run(_ctx(facade), {"id": "999"})
    assert not miss.ok
    miss2 = GetOfferTool().run(_ctx(facade), {})
    assert not miss2.ok


def test_search_offers_open_price_span_becomes_concrete():
    """Planner may emit a one-sided range like [0, null]; the tool must not
    forward None bounds into the faceted layer."""

    class _StrictFaceted(_FakeFaceted):
        def in_price_range(self, lo, hi, lang, limit):
            assert lo is not None and hi is not None, "open bounds must not reach faceted"
            return super().in_price_range(lo, hi, lang, limit)

    base = _FakeFacade()
    facade = _FakeFacade()
    facade.faceted = _StrictFaceted(
        merchants=base.faceted._merchants,
        product_pairs=base.faceted._products,
        category_offers=base.faceted._categories,
        all_price=base.faceted._all_price,
    )
    reg = ToolRegistry([SearchOffersTool()])
    res = reg.call("search_offers", _ctx(facade),
                   {"price_range": [0.0, None], "limit": 6})
    assert res.ok
    assert res.note["provenance"].startswith("faceted:price")


def test_fresh_only_reference_date_drops_pre_authored_today():
    """The sample catalog's expiry dates were authored against 2026-08-01,
    not the real current date, so the gate must take an explicit reference
    date: drop offers expired before it, keep ones expiring after it."""

    def expired(oid, expiry):
        o = _offer(oid, "KFC", "Zinger meal", 100, sold=10)
        o["metadata"]["expiry"] = expiry
        return o

    reference_date = datetime.date(2026, 8, 1)
    items = [
        expired("old1", "2026-01-15"),   # expired before the reference date
        expired("old2", "2026-07-31"),   # expired the day before
        expired("mid", "2026-08-01"),    # expires on the reference date -> kept
        expired("new", "2026-12-31"),    # expires after the reference date
    ]
    kept, dropped = _fresh_only(items, reference_date=reference_date)
    assert dropped == 2
    ids = {i["metadata"]["id"] for i in kept}
    assert ids == {"mid", "new"}


def test_bad_args_give_safe_tool_result():
    reg = ToolRegistry([SearchOffersTool()])
    res = reg.call("search_offers", _ctx(_FakeFacade()),
                   {"merchant": "KFC", "limit": "not-a-number"})
    assert not res.ok
    assert "must be an integer" in res.error