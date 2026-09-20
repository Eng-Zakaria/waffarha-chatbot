#!/usr/bin/env python3
"""Regression tests for FAQ-vs-offer routing.

Covers the two mechanisms that keep broad "company info / policy" questions
from being answered with offers:

1. `_should_restrict_to_faq` -- the hard one-directional source gate used in
   retrieve(): a query with FAQ vocabulary and NO offer vocabulary is capped
   to FAQ docs only, so retrieval can never surface an offer for it.
2. `_route_faq_topic` -- the deterministic FAQ topic router, now covering
   company-information ("ما هي وفرها"), data/privacy, and refund phrasings
   that previously fell through to hybrid retrieval.

Pure-function tests only -- no RagEngine instantiation (needs the index),
no Ollama, so they run anywhere.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.rag_engine import (
    _classify_intent,
    _route_faq_topic,
    _should_restrict_to_faq,
)


def test_classify_intent_matrix():
    """Offer queries classify offer, FAQ queries faq, ambiguous ones mixed."""
    assert _classify_intent("عروض كنتاكي") == "offer"
    assert _classify_intent("KFC offers with discount") == "offer"
    assert _classify_intent("ما هي وفرها") == "faq"
    assert _classify_intent("معلومات عن الشركة") == "faq"
    assert _classify_intent("ما هي سياسة الاسترجاع") == "faq"
    assert _classify_intent("How do I use my purchased coupon?") == "mixed"
    assert _classify_intent("عايز أعرف سياسة الاسترجاع لو دفعت") == "mixed"


def test_should_restrict_to_faq_company_and_policy():
    """Company-info / policy / data questions are strongly FAQ -- must never
    be routed to offers."""
    faq_queries = [
        "ما هي وفرها",
        "معلومات عن وفرها",
        "معلومات عن الشركة",
        "ما هي سياسة الاسترجاع",
        "سياسة الخصوصية بتاعتكم",
        "بتجمعوا معلومات إيه عني؟",
        "What is Waffarha?",
        "what is your refund policy",
        "What information does Waffarha collect?",
    ]
    for q in faq_queries:
        assert _should_restrict_to_faq(q), f"expected FAQ-restricted: {q!r}"


def test_should_not_restrict_to_faq_offer_and_mixed():
    """Offer lookups and mixed questions (FAQ + offer vocab) must NOT be
    capped to FAQ docs -- their offer docs must stay reachable."""
    not_faq_queries = [
        "عروض كنتاكي",
        "KFC offers with discount",
        "How do I use my purchased coupon?",
        "عايز أعرف سياسة الاسترجاع لو دفعت كاش",
        "التاني بكام",
        "عروض وفرها على الشاورما",
    ]
    for q in not_faq_queries:
        assert not _should_restrict_to_faq(q), f"expected NOT FAQ-restricted: {q!r}"


def test_route_faq_topic_about_company_arabic():
    """'ما هي وفرها' and friends map to the about-Waffarha FAQ, so the
    deterministic router answers instead of hybrid retrieval."""
    for q in ["ما هي وفرها", "معلومات عن وفرها", "إزاي وفرها بتشتغل",
              "وفرها بتشتغل إزاي", "فكرة وفرها"]:
        rule, faq_id, _ = _route_faq_topic(q, q)
        assert rule == "about_company", q
        assert faq_id == "faq_12_about", q


def test_route_faq_topic_about_company_english():
    """End-anchored English about-Waffarha phrasings map to faq_12_about."""
    for q in ["What is Waffarha?", "what's waffarha", "about waffarha",
              "Tell me about waffarha"]:
        rule, faq_id, _ = _route_faq_topic(q, q)
        assert rule == "about_company_en", q
        assert faq_id == "faq_12_about", q


def test_route_faq_topic_singular_english_return_policy():
    r"""English singular gap: "What's your return policy?" (QA log T4 verbatim)
    matched neither `refund\s*polic\w*` nor `returns\s*polic\w*`, and has no
    coupon/refund-verb vocabulary, so it fell through to offer retrieval."""
    for q in ["What's your return policy?", "what is your return policy?",
              "Do you have a return policy?"]:
        rule, faq_id, _ = _route_faq_topic(q, q)
        assert rule == "refund_policy", f"expected refund_policy for {q!r}"
        assert faq_id == "faq_refund_policy", q


def test_route_faq_topic_does_not_hijack_offer_questions():
    """'عروض وفرها'/offer questions must never be absorbed by the about-
    company rules -- the shared offer-word guard short-circuits them, and
    the about_company_en pattern is end-anchored so "how does waffarha offer
    X" can't match."""
    offer_queries = [
        "عروض وفرها على الشاورما",
        "how does waffarha offer discounts on phones",
    ]
    for q in offer_queries:
        assert _route_faq_topic(q, q) is None, f"expected no FAQ topic: {q!r}"


def test_route_faq_topic_privacy_and_refund():
    """Data/privacy and refund policy phrasings still route deterministically
    (privacy rule extended; refund_policy already present)."""
    rule, faq_id, _ = _route_faq_topic("بتشاركوا بياناتي مع حد؟", "بتشاركوا بياناتي مع حد؟")
    assert rule == "privacy" and faq_id == "faq_13_privacy"
    rule, faq_id, _ = _route_faq_topic("What information does Waffarha collect?",
                                       "What information does Waffarha collect?")
    assert rule == "privacy" and faq_id == "faq_13_privacy"
    rule, faq_id, _ = _route_faq_topic("ما هي سياسة الاسترجاع؟", "ما هي سياسة الاسترجاع؟")
    assert rule == "refund_policy" and faq_id == "faq_refund_policy"
    # "my coupon" + collect must NOT be absorbed by the privacy rule (it's a
    # coupon-usage FAQ at worst, never a data-privacy answer)
    rule, faq_id, _ = _route_faq_topic("ازاي أجمّع الكوبون بتاعي من الفرع",
                                       "ازاي أجمّع الكوبون بتاعي من الفرع")
    assert rule != "privacy" and faq_id != "faq_13_privacy"