#!/usr/bin/env python3
"""Fast, offline unit tests for the Stage-3 cascade tools
(agent/tools/cascade_tools.py): retrieve_faq, compare_offers, superlative_offer,
catalog. No Ollama, no index: the heavy core.rag_engine import must NOT fire at
import time (the lazy `_r()` path)."""
import os
import subprocess
import sys
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


from agent.tools import ToolContext, ToolRegistry
from agent.tools.cascade_tools import register_cascade_tools
from agent.tools.catalog_tools import register_catalog_tools


def _offer(oid, merchant="KFC", price="150"):
    return {"metadata": {"source": "offer", "id": oid,
                         "merchant": merchant, "price": str(price),
                         "title": f"offer {oid}"}}


class _FakeFaceted:
    merchants: ClassVar[set[str]] = {"KFC", "زادنا"}

    def resolve_merchants(self, name):
        return [m for m in self.merchants if str(m) in str(name)]

    def resolve_category(self, query):
        if "بيتزا" in (query or ""):
            return "pizza"
        return None

    def offers_for_merchant(self, canonical, lang, limit, exclude_ids=None,
                            price_filter=None):
        items = [_offer("10", "KFC", 150), _offer("11", "KFC", 300)]
        return items[:limit]

    def unknown_merchant_mention(self, text):
        return None

    def cheapest(self, lang, merchant=None, category=None):
        base = [_offer("20", "KFC", 80), _offer("21", "زادنا", 120)]
        if merchant:
            base = [o for o in base if o["metadata"]["merchant"] == merchant]
        if category:
            base = [_offer("30", "بيتزا هت", 250)]
        return base[:1] or []

    def most_expensive(self, lang, merchant=None, category=None):
        return [_offer("22", "KFC", 999)]

    def highest_discount(self, lang, merchant=None, category=None):
        return [_offer("23", "KFC", 300)]


class _FakeFacade:
    def __init__(self):
        self.faceted = _FakeFaceted()
        self._answers = {
            "faq-1": "للاسترداد راسلنا خلال 14 يوم",
            "faq-2": "الدفع عند الاستلام متاح",
        }

    def normalize_arabizi_and_arabic(self, q):
        return q

    def _route_faq_topic(self, query, normalized_query):
        if "استرداد" in (query or ""):
            return ("refund", "faq-1", False)
        return None

    def _faq_topic_answer(self, faq_id, lang, bilingual=False):
        return self._answers.get(faq_id) or None

    def retrieve(self, query, top_k=None, history=None, recent_offers=None,
                 normalized_query=None):
        if "دفع" in (query or ""):
            return [{"metadata": {"source": "faq", "id": "faq-2",
                                  "question": "payment"},
                     "combined_score": 0.92, "answer": "payment ok"}]
        return []

    def _get_faq_direct_answer(self, retrieved, lang, query=None,
                               multi_item=None, followup_verdict=None):
        if retrieved:
            return self._answers.get("faq-2")
        return None

    def _get_comparison_answer(self, entries, lang):
        return "CONTRAST " + " + ".join(o["metadata"]["id"] for o in entries)

    def _offer_card_blocks(self, items, lang):
        return [f"- OFFER {it['metadata']['id']} | {it['metadata']['merchant']}"
                for it in items[:2]]


def _ctx(facade=None, recent=None, query="q", user_id=None, lang="en"):
    return ToolContext(facade=facade or _FakeFacade(), reply_lang=lang,
                       query=query,
                       recent_offers=recent or [_offer("10", "KFC"),
                                                _offer("11", "KFC")],
                       user_id=user_id)


def _make_registry():
    return register_catalog_tools(register_cascade_tools(ToolRegistry()))


def test_cascade_tools_do_not_eagerly_import_rag_engine():
    code = (
        "import sys; sys.path.insert(0, '.'); import agent.tools; "
        "sys.stdout.write(str('core.rag_engine' in sys.modules))"
    )
    ran = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120, check=True)
    assert ran.stdout.strip() == "False", (
        "agent.tools must not import core.rag_engine at import time (~7s)")


def test_register_names():
    assert {"retrieve_faq", "compare_offers", "superlative_offer",
            "catalog"} <= set(_make_registry().names())


def test_retrieve_faq_topic_hit():
    reg = _make_registry()
    ctx = _ctx(query="ايه سياسة الاسترداد؟", lang="ar")
    out = reg.call("retrieve_faq", ctx, {"query": ctx.query})
    assert out.ok and out.note["text"] == "للاسترداد راسلنا خلال 14 يوم"
    assert out.items[0]["metadata"]["source"] == "faq"


def test_retrieve_faq_direct_answer_fallback():
    reg = _make_registry()
    ctx = _ctx(query="ازاي ادفع؟")
    out = reg.call("retrieve_faq", ctx, {"query": ctx.query})
    assert out.ok and out.items[0]["metadata"]["id"] == "faq-2"
    assert "دفع" in out.note["text"]


def test_retrieve_faq_clean_negative():
    reg = _make_registry()
    ctx = _ctx(query="أرخص عروض")
    out = reg.call("retrieve_faq", ctx, {"query": ctx.query})
    assert out.ok
    assert out.note["kind"] == "no_match"


def test_compare_offers_from_stored_ids():
    reg = _make_registry()
    ctx = _ctx(recent=[_offer("10", "KFC"), _offer("11", "KFC")])
    out = reg.call("compare_offers", ctx, {"offer_ids": ["10", "11"]})
    assert out.ok and out.items == ctx.recent_offers
    assert out.note["text"].startswith("CONTRAST 10 + 11")


def test_compare_offers_by_merchant():
    reg = _make_registry()
    ctx = _ctx()
    out = reg.call("compare_offers", ctx, {"merchant": "KFC", "limit": 2})
    assert out.ok and len(out.items) == 2


def test_compare_offers_needs_two():
    reg = _make_registry()
    ctx = _ctx(recent=[_offer("10", "KFC")])
    out = reg.call("compare_offers", ctx, {})
    assert out.note["kind"] == "no_match"


def test_superlative_cheapest():
    reg = _make_registry()
    out = reg.call("superlative_offer", _ctx(), {"direction": "cheapest"})
    assert out.ok
    assert out.items[0]["metadata"]["id"] == "20"
    assert out.note["text"].startswith("Here's the cheapest")
    assert out.note["provenance"] == "superlative:cheapest"


def test_superlative_category_scope():
    reg = _make_registry()
    out = reg.call("superlative_offer", _ctx(), {"direction": "cheapest",
                                                 "category": "بيتزا"})
    assert out.ok
    assert out.items[0]["metadata"]["id"] == "30"


def test_superlative_bad_direction():
    reg = _make_registry()
    out = reg.call("superlative_offer", _ctx(), {"direction": "cheapestest"})
    assert not out.ok
    assert "direction" in out.error


def test_catalog_personal_needs_user():
    reg = _make_registry()
    out = reg.call("catalog", _ctx(user_id=None),
                   {"scope": "personal"})
    assert out.note["kind"] == "no_user"


def test_catalog_personal_disabled_when_offline(monkeypatch):
    monkeypatch.setattr("core.config.PERSONAL_QUERIES_ENABLED", False)
    reg = _make_registry()
    out = reg.call("catalog", _ctx(user_id=7), {"scope": "personal",
                                                "query": "كوبوناتي"})
    assert out.note["kind"] == "personal_disabled"


def test_catalog_personal_enabled_but_not_personal_question(monkeypatch):
    monkeypatch.setattr("core.config.PERSONAL_QUERIES_ENABLED", True)
    reg = _make_registry()
    out = reg.call("catalog", _ctx(user_id=7), {"scope": "personal",
                                                "query": "أرخص عروض"})
    assert out.note["kind"] == "not_personal"


def test_catalog_scope_all_delegates_to_search():
    reg = _make_registry()
    out = reg.call("catalog", _ctx(), {"scope": "all", "merchant": "KFC"})
    assert out.ok and out.items


def test_faceted_exclude_matches_int_oid_keys():
    """_recs_to_entries must exclude by STRING-normalized id: the catalog's
    oid keys are ints (8119) while the reference pass emits strings
    ('8119'). The old `oid in exclude_ids` comparison silently no-op'd on that
    mismatch, so 'غيرها' / 'cheaper' kept re-showing the offer they excluded."""
    import types

    from core.faceted import FacetedCatalog

    stub = types.SimpleNamespace(_price_span=lambda rec: (100.0, 200.0))
    recs = [
        (8119, {"merchant_canonical": "KFC",
                "en": {"metadata": {"id": "8119", "title": "a"}},
                "ar": {"metadata": {"id": "8119", "title": "أ"}}}),
        (6812, {"merchant_canonical": "KFC",
                "en": {"metadata": {"id": "6812", "title": "b"}},
                "ar": {"metadata": {"id": "6812", "title": "ب"}}}),
    ]
    out = FacetedCatalog._recs_to_entries(
        stub, recs, "en", 10, exclude_ids={"8119"}, price_filter=None)
    assert [o["metadata"]["id"] for o in out] == ["6812"]
    out2 = FacetedCatalog._recs_to_entries(
        stub, recs, "en", 10, exclude_ids={8119}, price_filter=None)
    assert [o["metadata"]["id"] for o in out2] == ["6812"]