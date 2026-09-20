"""
Unit tests for Waffarha RAG Perfection Blueprint (Pillars 1-5).
"""
import pytest
from core.rag_perfection import (
    normalize_arabizi_and_arabic,
    check_out_of_scope_guardrail,
    classify_intent_robust,
    INTENT_OFFER_LOOKUP,
    INTENT_FAQ_INQUIRY,
    INTENT_OUT_OF_SCOPE,
    INTENT_GREETING,
    INTENT_SUPERLATIVE
)

def test_arabizi_normalization():
    # Test digit mapping - verify both original and transliterated versions are present
    res1 = normalize_arabizi_and_arabic("3ayez a3raf kam offer el KFC?")
    # Should contain original Latin words (kept for BM25 exact matching)
    assert "3ayez" in res1
    assert "a3raf" in res1
    assert "kam" in res1
    # Should also contain Arabic-transliterated versions for dense matching
    # "3" -> ع, "a" -> ا, "y" -> ي, "e" -> ا, "z" -> ز => عاييز / عاياز
    assert "ع" in res1  # should contain at least one Arabic letter from transliteration
    assert "ز" in res1  # ز (from z)

    # Test standard Arabic hamza/alef normalization
    res2 = normalize_arabizi_and_arabic("أنا عايز أشترى كُوبون")
    assert "ا" in res2
    assert "كوبون" in res2

def test_out_of_scope_guardrail():
    # Weather query should trigger guardrail
    deflection = check_out_of_scope_guardrail("عاملين إيه الجو في القاهرة النهاردة؟", "ar")
    assert deflection is not None
    assert "مساعد خدمة عملاء" in deflection

    # Medical query should trigger guardrail
    deflection_med = check_out_of_scope_guardrail("عندي وجع رأس، آخذ إيه دواء؟", "ar")
    assert deflection_med is not None

    # Valid Waffarha query should NOT trigger guardrail
    valid = check_out_of_scope_guardrail("عندكم عرض كنتاكي بكام؟", "ar")
    assert valid is None

def test_classify_intent_robust():
    assert classify_intent_robust("السلام عليكم") == INTENT_GREETING
    assert classify_intent_robust("أرخص عرض عندكم قد إيه؟") == INTENT_SUPERLATIVE
    assert classify_intent_robust("إزاي أشتري كوبون من التطبيق؟") == INTENT_FAQ_INQUIRY
    assert classify_intent_robust("الطقس عامل ايه") == INTENT_OUT_OF_SCOPE
