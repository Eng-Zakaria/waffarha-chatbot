"""
Interactive manual test script for Waffarha RAG Chatbot & Perfection Pillars.
Fixed UTF-8 encoding issues for proper Arabic character display.
"""
import sys
import os

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8')

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.rag_perfection import (
    normalize_arabizi_and_arabic,
    check_out_of_scope_guardrail,
    classify_intent_robust
)
from core.rag_engine import RagEngine

def run_manual_tests():
    print("=" * 80)
    print("Waffarha RAG Chatbot - Manual Testing & Verification Script")
    print("=" * 80)

    # 1. Test Arabizi Normalization
    print("\n--- [Pillar 4] Testing Arabizi Normalization ---")
    queries_arabizi = [
        "3ayez a3raf kam offer el KFC?",
        "3ayez ashtry kobon",
        "7abib 3ard el pizza",
    ]
    for q in queries_arabizi:
        norm = normalize_arabizi_and_arabic(q)
        print(f"Original:  {q}")
        print(f"Normalized: {norm}")
        print("-" * 40)

    # 2. Test Out-of-Scope Guardrails
    print("\n--- [Pillar 3] Testing Out-of-Scope Guardrails ---")
    queries_oos = [
        ("عاملين إيه الجو في القاهرة النهاردة؟", "Weather query"),
        ("عندي وجع رأس، آخذ إيه دواء؟", "Medical query"),
        ("نكتة حلوة كده", "Joke query"),
        ("عندكم عرض كنتاكي بكام؟", "Valid Waffarha query"),
    ]
    for q, desc in queries_oos:
        deflection = check_out_of_scope_guardrail(q, "ar")
        print(f"[{desc}] Query: {q}")
        if deflection:
            print(f"  -> BLOCKED (Deflection): {deflection}")
        else:
            print("  -> PASSED (Valid Domain Query)")
        print("-" * 40)

    # 3. Test Intent Classification
    print("\n--- [Pillar 1] Testing Robust Intent Routing ---")
    queries_intent = [
        "السلام عليكم",
        "أرخص عرض عندكم قد إيه؟",
        "إزاي أشتري كوبون من التطبيق؟",
        "الطقس عامل ايه",
        "عايز اعرف عرض كنتاكي بكام",
    ]
    for q in queries_intent:
        intent = classify_intent_robust(q)
        print(f"Query: {q}")
        print(f"  -> Intent: {intent}")
        print("-" * 40)

    # 4. Test Full RAG Engine Pipeline (if index exists)
    print("\n--- Testing Full RAG Engine Pipeline ---")
    try:
        engine = RagEngine()
        test_queries = [
            "3ayez a3raf kam offer el KFC?",
            "أرخص عرض عندكم",
            "طريقة استرداد ثمن الكوبون",
        ]
        for q in test_queries:
            print(f"\nQuery: {q}")
            response = engine.answer(q)
            print(f"Answer:\n{response['answer']}")
            print(f"Sources count: {len(response['sources'])}")
            print("=" * 60)
    except Exception as e:
        print(f"Note: RAG Engine initialization skipped or index not fully built: {e}")
        print("To run full RAG retrieval tests, ensure index is built via 'python ingest/build_index.py'")

if __name__ == "__main__":
    run_manual_tests()