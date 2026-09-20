#!/usr/bin/env python3
"""Unit tests for the anchored follow-up layer (A + C + D) in core/rag_engine.py:

- A: _followup_anchored_answer builds the answer pool from the offers actually
      shown (memory), deterministic, no fresh retrieval.
- C: retrieve() reuses this turn's follow-up resolution and caps pinned
      anchors so unrelated offers never sneak into an anchored comparison.
- D: _summarize_followup turns (shown offers + short message) into a
      structured JSON {is_followup, targets, intent, price_threshold, scope}.

Engine instances are built via RagEngine.__new__ (no index/embeddings/Ollama
needed) so these run fast and deterministically against a stub LLM client.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.rag_engine import RagEngine
from core.faceted import FacetedCatalog


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, **kwargs):
        self.calls += 1
        content = self.responses.pop(0) if self.responses else "{}"
        return {"message": {"content": content}}


def _doc(oid, lang, merchant, title, price=None, old=None, discount=None):
    return {
        "metadata": {
            "source": "offer",
            "id": oid,
            "lang": lang,
            "merchant": merchant,
            "section_id": 1,
            "title": title,
            "price": price,
            "old_price": old,
            "discount": discount,
            "expiry": "2026-12-31",
            "url": f"https://x.test/o-{oid}",
        },
        "text": f"{merchant} {title}",
    }


def _mk_engine(oauth_docs, client=None, offer_intent_words=None):
    eng = RagEngine.__new__(RagEngine)
    eng.llm_model = "test-model"
    eng.llm_options = {}
    eng.client = client or FakeClient([])
    eng.faceted = FacetedCatalog(list(oauth_docs), offer_intent_words=offer_intent_words)
    eng._offer_merchants = sorted(
        {d["metadata"].get("merchant", "") for d in oauth_docs if d["metadata"].get("merchant")},
        key=len, reverse=True)
    eng._followup_ctx = None
    eng._last_followup_verdict = "NEW_TOPIC"
    return eng


def _mem(oid, merchant, title, price, lang="ar"):
    return {"metadata": {
        "source": "offer", "id": oid, "lang": lang,
        "merchant": merchant, "title": title, "price": price,
        "old_price": None, "discount": None, "expiry": "2026-12-31",
        "section_id": 1, "url": f"https://x.test/o-{oid}",
    }}


def test_compare_anchored_to_shown_offers():
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100),
            _doc(2, "en", "Pizza Hut", "Big pizza", 150),
            _doc(2, "ar", "بيتزا هت", "بيتزا كبير", 150),
            _doc(99, "en", "Unrelated", "Bean bags", 999),
            _doc(99, "ar", "غير مرتبط", "كنب", 999)]
    eng = _mk_engine(docs, client=FakeClient([]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 100),
             _mem(2, "Pizza Hut", "بيتزا كبير", 150)]
    ans = eng._followup_anchored_answer("قارن بين العرضين", shown, reply_lang="ar")
    assert ans is not None
    assert "مقارنة بين العروض" in ans
    assert "كومبو زنجر" in ans and "بيتزا كبير" in ans
    assert "كنب" not in ans and "bean" not in ans.lower()
    assert eng.client.calls == 0  # rule fast-path, no LLM needed


def test_cheaper_than_resolves_from_shown_pool():
    docs = [_doc(1, "en", "KFC", "Zinger combo", 800),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 800),
            _doc(2, "en", "Pizza Hut", "Big pizza", 300),
            _doc(2, "ar", "بيتزا هت", "بيتزا كبير", 300),
            _doc(3, "en", "Koshary", "Koshary plate", 150),
            _doc(3, "ar", "كشري", "طبق كشري", 150)]
    eng = _mk_engine(docs, client=FakeClient([(
        '{"is_followup": true, "targets": [0], "intent": "cheaper_than", '
        '"price_threshold": 500, "scope": "shown"}')]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 800),
             _mem(2, "Pizza Hut", "بيتزا كبير", 300),
             _mem(3, "Koshary", "طبق كشري", 150)]
    ans = eng._followup_anchored_answer(
        "فيه أرخص من كده تحت 500 جنيه؟", shown, reply_lang="ar")
    assert ans is not None
    assert "طبق كشري" in ans and "150" in ans
    assert "800" not in ans          # 800 is above the threshold AND not cheapest
    assert eng.client.calls == 1      # exactly one D classification call


def test_other_offer_uses_faceted_pool_excluding_shown():
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100),
            _doc(2, "en", "KFC", "Fried chicken meal", 200),
            _doc(2, "ar", "دجاج كنتاكي", "وجبة دجاج فرايد", 200),
            _doc(3, "en", "Pizza Hut", "Big pizza", 150),
            _doc(3, "ar", "بيتزا هت", "بيتزا كبير", 150)]
    eng = _mk_engine(docs, client=FakeClient([(
        '{"is_followup": true, "targets": [0], "intent": "other_offer", '
        '"price_threshold": null, "scope": "same_merchant"}')]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 100)]
    ans = eng._followup_anchored_answer(
        "عايز عروض تانية من نفس المحل", shown, reply_lang="ar")
    assert ans is not None
    assert "وجبة دجاج فرايد" in ans      # the OTHER KFC offer
    assert "كومبو زنجر" not in ans        # the shown one is excluded
    assert "بيتزا كبير" not in ans        # unrelated merchant never appears
    assert eng.client.calls == 1


def test_self_contained_entity_never_classifies():
    """A follow-up naming a fresh merchant/product/category is self-contained:
    the anchored path must return None WITHOUT spending an LLM call -- e.g.
    "عايز أسعار تليفونات" resolves the generic 'services' category."""
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100)]
    eng = _mk_engine(docs, client=FakeClient([("should never be used")]))
    assert eng.faceted.resolve_category("عايز أسعار تليفونات") is not None
    ans = eng._followup_anchored_answer("عايز أسعار تليفونات", [], reply_lang="ar")
    assert ans is None
    assert eng.client.calls == 0


def test_summarizer_new_topic_falls_through():
    """A short message with no reference to the shown offers is NEW_TOPIC:
    the deterministic gate short-circuits it (zero LLM calls — the gate is
    cheaper and more reliable than the summarizer), the anchored path
    returns None, and normal retrieval handles it."""
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100)]
    eng = _mk_engine(docs, client=FakeClient([(
        '{"is_followup": false, "targets": [], "intent": "same", '
        '"price_threshold": null, "scope": "catalog"}')]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 100)]
    ans = eng._followup_anchored_answer("تفتكر الدنيا هتمطر بكرة؟", shown, reply_lang="ar")
    assert ans is None
    assert eng.client.calls == 0


def test_no_anchor_without_recent_offers_returns_none():
    eng = _mk_engine([])
    assert eng._followup_anchored_answer("قارن بين العرضين", [], reply_lang="ar") is None


def test_robust_number_parsing():
    eng = _mk_engine([])
    assert eng._robust_number("500 جنيه") == 500
    assert eng._robust_number("EGP 1,200") == 1200
    assert eng._robust_number(250) == 250
    assert eng._robust_number("ألف") is None
    assert eng._robust_number(None) is None
    assert eng._robust_number(True) is None


def test_followup_ctx_is_cached_for_retrieve_reuse():
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100),
            _doc(2, "en", "Pizza Hut", "Big pizza", 150),
            _doc(2, "ar", "بيتزا هت", "بيتزا كبير", 150)]
    eng = _mk_engine(docs)
    shown = [_mem(1, "KFC", "كومبو زنجر", 100),
             _mem(2, "Pizza Hut", "بيتزا كبير", 150)]
    targets, verdict = eng._resolve_followup_targets("قارن بين العرضين", shown)
    assert verdict == "SAME_OFFER" and len(targets) == 2
    ctx = eng._followup_ctx
    assert ctx["query"] == "قارن بين العرضين"
    assert ctx["intent"] == "compare"
    assert ctx["verdict"] == "SAME_OFFER"


def test_anaphoric_price_followup_detection():
    from core.rag_engine import _same_offer_price_followup as detect
    assert detect("العرض ده بكام قبل الخصم؟")
    assert detect("التاني كان بكام؟")
    assert detect("السعر الأصلي بتاعه كام؟")
    assert detect("el mc bkaam?")
    assert not detect("عروض من مطعم الجندل")
    assert not detect("عروض من ستار لونج")


def test_self_contained_question_never_anchors():
    """FAQ/driver questions that don't point back at the shown offers are
    NEW topics even when session memory is rich -- the anchored layer must
    not hijack them the moment real offers are in context. (Regression for
    the post-memory-fix suite drop: cashback/privacy/about questions were
    being answered with offer cards because qwen mislabeled them as follow-
    ups.)"""
    eng = _mk_engine([_doc(1, "ar", "Juice me up", "سموثي", 8)])
    shown = [_mem(1, "Juice me up", "سموثي", 8)]
    for q in ["الكاش باك بيتحلل بعد امتى؟",
              "ايه هو وفرها أصلاً؟",
              "privacy بتعكم ايه؟",
              "لو مكتوب expired؟",
              "فيه عروض هدايا؟"]:
        targets, verdict = eng._resolve_followup_targets(q, shown)
        assert verdict == "NEW_TOPIC" and not targets, q
        assert eng._followup_anchored_answer(q, shown, reply_lang="ar") is None, q
    assert eng.client.calls == 0  # deterministic gate, no LLM call


def test_other_offer_does_not_anchor_on_someone_else():
    """'بطاقة الهدية دي بتتبعت لحد تاني؟' = 'can the gift card be sent to
    someone else?' -- 'تاني' here means 'else', NOT 'another offer'. Must be
    treated as a fresh question, not the other_offer anchored path."""
    eng = _mk_engine([_doc(1, "ar", "Juice me up", "سموثي", 8)])
    shown = [_mem(1, "Juice me up", "سموثي", 8)]
    targets, verdict = eng._resolve_followup_targets(
        "بطاقة الهدية دي بتتبعت لحد تاني؟", shown)
    assert verdict == "NEW_TOPIC" and not targets
    assert eng._followup_anchored_answer(
        "بطاقة الهدية دي بتتبعت لحد تاني؟", shown, reply_lang="ar") is None


def test_merchant_named_other_offer_routes_to_other_offer():
    """'عايز عروض تانية من كنتاكي' names a merchant AND asks for a different
    offer from what was shown -- must be OTHER_OFFER, never a re-show of the
    same card."""
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100),
            _doc(2, "en", "Pizza Hut", "Big pizza", 150),
            _doc(2, "ar", "بيتزا هت", "بيتزا كبير", 150)]
    eng = _mk_engine(docs, client=FakeClient([]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 100),
             _mem(2, "Pizza Hut", "بيتزا كبير", 150)]
    targets, verdict = eng._resolve_followup_targets("عايز عروض تانية من KFC", shown)
    assert verdict == "OTHER_OFFER_SAME_SOURCE"
    assert eng._followup_ctx["intent"] == "other_offer"
    assert targets == [shown[0]]  # KFC is the anchor merchant
    assert eng.client.calls == 0  # merchant fast-path, no LLM


def test_merchant_named_comparison_routes_to_compare():
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100),
            _doc(2, "en", "Pizza Hut", "Big pizza", 150),
            _doc(2, "ar", "بيتزا هت", "بيتزا كبير", 150)]
    eng = _mk_engine(docs, client=FakeClient([]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 100),
             _mem(2, "Pizza Hut", "بيتزا كبير", 150)]
    targets, verdict = eng._resolve_followup_targets("قارن بين KFC وPizza Hut", shown)
    assert verdict == "SAME_OFFER"
    assert eng._followup_ctx["intent"] == "compare"
    assert len(targets) == 2


def test_cheaper_than_explicit_number_wins_over_llm():
    """'فيه أرخص من كده تحت 500 جنيه؟' carries its own cap -- an LLM guess
    of the price_threshold must not win over the number literally in the
    query."""
    docs = [_doc(1, "en", "KFC", "Zinger combo", 800),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 800),
            _doc(2, "en", "Pizza Hut", "Big pizza", 300),
            _doc(2, "ar", "بيتزا هت", "بيتزا كبير", 300),
            _doc(3, "en", "Koshary", "Koshary plate", 150),
            _doc(3, "ar", "كشري", "طبق كشري", 150)]
    eng = _mk_engine(docs, client=FakeClient([(
        '{"is_followup": true, "targets": [0], "intent": "cheaper_than", '
        '"price_threshold": 500, "scope": "shown"}')]))
    shown = [_mem(1, "KFC", "كومبو زنجر", 800),
             _mem(2, "Pizza Hut", "بيتزا كبير", 300),
             _mem(3, "Koshary", "طبق كشري", 150)]
    ans = eng._followup_anchored_answer(
        "فيه أرخص من كده تحت 500 جنيه؟", shown, reply_lang="ar")
    assert ans is not None
    assert "طبق كشري" in ans and "150" in ans
    assert "بيتزا" not in ans  # 300 EGP must be excluded: cap is 500 from
                               # the explicit number, not the LLM's guess


def test_superlative_fresh_query_never_anchors():
    """'أرخص حاجة عندك' / 'أفضل عرض' are fresh superlative searches, NOT
    follow-ups on the shown offers -- bare comparatives must not count as
    cross-references."""
    from core.rag_engine import _offers_likely_referenced
    for q in ["أرخص حاجة عندك", "أفضل عرض", "عايز أرخص عرض", "أغلى حاجة"]:
        assert not _offers_likely_referenced(q), q
    # ...but 'أرخص من ده' IS anaphoric
    assert _offers_likely_referenced("فيه أرخص من ده؟")
    assert _offers_likely_referenced("فيه أرخص من كده تحت 200 جنيه؟")
    eng = _mk_engine([_doc(1, "ar", "KFC", "كومبو زنجر", 100)])
    shown = [_mem(1, "KFC", "كومبو زنجر", 100)]
    for q in ["أرخص حاجة عندك", "أفضل عرض", "عايز أرخص عرض", "أغلى حاجة"]:
        targets, verdict = eng._resolve_followup_targets(q, shown)
        assert verdict == "NEW_TOPIC" and not targets, q
        assert eng._followup_anchored_answer(q, shown, reply_lang="ar") is None, q


def test_unknown_merchant_negative_skips_price_followup():
    """"العرض ده بكام قبل الخصم؟" must NEVER be answered with the unknown-
    merchant negative ("مفيش عندنا عروض من بكام") -- it is a price follow-up
    on a previously-shown offer, owned by the anchored layer. Real unknown
    merchant requests (with offer intent) still get the deterministic
    negative."""
    docs = [_doc(1, "en", "KFC", "Zinger combo", 100),
            _doc(1, "ar", "دجاج كنتاكي", "كومبو زنجر", 100)]
    eng = _mk_engine(docs, offer_intent_words={"عروض", "عرض", "خصم"})
    assert eng.faceted.has_offer_intent("العرض ده بكام قبل الخصم؟")
    ans = eng._faceted_answer("العرض ده بكام قبل الخصم؟", reply_lang="ar")
    assert ans is None or "مفيش عندنا عروض من" not in ans
    # genuine unknown-merchant requests still get the deterministic negative
    neg = eng._faceted_answer("عروض من مطعم الجندل", reply_lang="ar")
    assert neg and "مفيش عندنا عروض من" in neg
    # and a franco price follow-up is also never an unknown merchant
    ans2 = eng._faceted_answer("el mc bkaam?", reply_lang="ar")
    assert ans2 is None or "مفيش عندنا عروض من" not in ans2