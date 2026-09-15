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
        ("Ø¹Ø§Ù…Ù„ÙŠÙ† Ø¥ÙŠÙ‡ Ø§Ù„Ø¬Ùˆ ÙÙŠ Ø§Ù„Ù‚Ø§Ù‡Ø±Ø© Ø§Ù„Ù†Ù‡Ø§Ø±Ø¯Ø©ØŸ", "Weather query"),
        ("Ø¹Ù†Ø¯ÙŠ ÙˆØ¬Ø¹ Ø±Ø£Ø³ØŒ Ø¢Ø®Ø° Ø¥ÙŠÙ‡ Ø¯ÙˆØ§Ø¡ØŸ", "Medical query"),
        ("Ù†ÙƒØªØ© Ø­Ù„ÙˆØ© ÙƒØ¯Ù‡", "Joke query"),
        ("Ø¹Ù†Ø¯ÙƒÙ… Ø¹Ø±Ø¶ ÙƒÙ†ØªØ§ÙƒÙŠ Ø¨ÙƒØ§Ù…ØŸ", "Valid Waffarha query"),
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
        "Ø§Ù„Ø³Ù„Ø§Ù… Ø¹Ù„ÙŠÙƒÙ…",
        "Ø£Ø±Ø®Øµ Ø¹Ø±Ø¶ Ø¹Ù†Ø¯ÙƒÙ… Ù‚Ø¯ Ø¥ÙŠÙ‡ØŸ",
        "Ø¥Ø²Ø§ÙŠ Ø£Ø´ØªØ±ÙŠ ÙƒÙˆØ¨ÙˆÙ† Ù…Ù† Ø§Ù„ØªØ·Ø¨ÙŠÙ‚ØŸ",
        "Ø§Ù„Ø·Ù‚Ø³ Ø¹Ø§Ù…Ù„ Ø§ÙŠÙ‡",
        "Ø¹Ø§ÙŠØ² Ø§Ø¹Ø±Ù Ø¹Ø±Ø¶ ÙƒÙ†ØªØ§ÙƒÙŠ Ø¨ÙƒØ§Ù…",
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
            "Ø£Ø±Ø®Øµ Ø¹Ø±Ø¶ Ø¹Ù†Ø¯ÙƒÙ…",
            "Ø·Ø±ÙŠÙ‚Ø© Ø§Ø³ØªØ±Ø¯Ø§Ø¯ Ø«Ù…Ù† Ø§Ù„ÙƒÙˆØ¨ÙˆÙ†",
        ]
        for q in test_queries:
            print(f"\nQuery: {q}")
            response = engine.answer(q)
            print(f"Answer:\n{response['answer']}")
            print(f"Sources count: {len(response['sources'])}")
            print("=" * 60)
    except Exception as e:
        print(f"Note: RAG Engine initialization skipped or index not fully built: {e}")
        print("To run full RAG retrieval tests, ensure index is built via 'python ingestion/loaders/build_index.py'")

if __name__ == "__main__":
    run_manual_tests()
