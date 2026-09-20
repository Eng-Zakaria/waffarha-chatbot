#!/usr/bin/env python3
"""
Conversation test for the BGE-M3 + Qdrant chatbot upgrade.
Runs 15 diverse queries through the full RAG pipeline and prints
each answer for manual quality review.

Usage:
    python tests/test_conversations.py
    python tests/test_conversations.py --verbose
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.rag_engine import RagEngine

# ---------------------------------------------------------------------------
# Test conversations — 15 diverse queries covering all major scenarios
# ---------------------------------------------------------------------------
CONVERSATIONS = [
    # --- Greetings ---
    {
        "id": 1,
        "category": "greeting",
        "query": "السلام عليكم",
        "expect": "Greeting reply, not offer content",
    },
    {
        "id": 2,
        "category": "greeting_en",
        "query": "Hey there!",
        "expect": "English greeting reply",
    },
    # --- Arabic offer queries ---
    {
        "id": 3,
        "category": "offer_arabic",
        "query": "عندكم عرض كنتاكي؟ بكام؟",
        "expect": "KFC offer with price in EGP",
    },
    {
        "id": 4,
        "category": "offer_arabic_casual",
        "query": "عايز اعرف ارخص عرض عندكم",
        "expect": "Cheapest offer details",
    },
    {
        "id": 5,
        "category": "offer_merchant",
        "query": "عرض ماكدونالدز بكام",
        "expect": "McDonald's offer with price",
    },
    # --- English offer queries ---
    {
        "id": 6,
        "category": "offer_english",
        "query": "What offers do you have for pizza?",
        "expect": "Pizza-related offers",
    },
    {
        "id": 7,
        "category": "offer_category",
        "query": "Show me food and beverage deals",
        "expect": "Food & beverage category offers",
    },
    # --- FAQ queries ---
    {
        "id": 8,
        "category": "faq_how_to_buy",
        "query": "ازاي اشتري كوبون من وفرها",
        "expect": "Purchase instructions / FAQ",
    },
    {
        "id": 9,
        "category": "faq_refund",
        "query": "لو عايز استرجع فلوس الكوبون اعمل ايه",
        "expect": "Refund policy / steps",
    },
    {
        "id": 10,
        "category": "faq_payment",
        "query": "بتدفعوا بايه؟ في فوري؟",
        "expect": "Payment methods including Fawry",
    },
    # --- Mixed language ---
    {
        "id": 11,
        "category": "mixed_language",
        "query": "عندكم عروض KFC ولا لا؟",
        "expect": "KFC offers in Arabic reply",
    },
    # --- Price / comparison ---
    {
        "id": 12,
        "category": "price_query",
        "query": "ايه ارخص وجبة عندكم؟",
        "expect": "Cheapest meal/offer details",
    },
    # --- Out of scope (should be deflected) ---
    {
        "id": 13,
        "category": "out_of_scope",
        "query": "عاملين إيه الجو في القاهرة النهاردة؟",
        "expect": "Deflection / cannot answer",
    },
    # --- Edge case: stock ---
    {
        "id": 14,
        "category": "stock_query",
        "query": "لسه فيه كوبونات متبقية لعرض كنتاكي؟",
        "expect": "Stock info or honest 'no data' answer",
    },
    # --- Multi-offer ---
    {
        "id": 15,
        "category": "multi_offer",
        "query": "عايز عروض البيتزا، في عندكم بيتزا هت ولا دومينوز؟",
        "expect": "Pizza Hut and/or Domino's offers",
    },
]


def run_conversations(verbose=False):
    print("=" * 70)
    print("  Conversation Test — BGE-M3 + Qdrant")
    print("=" * 70)

    # Initialize engine
    print("\nLoading RagEngine (BGE-M3 + Qdrant) ...")
    t0 = time.perf_counter()
    engine = RagEngine()
    load_time = time.perf_counter() - t0
    print(f"Engine loaded in {load_time:.1f}s")
    print(f"  Embedding model: {engine.embedding_model_name}")
    print(f"  Backend: {engine.backend}")

    results = []
    print("\n" + "-" * 70)
    for conv in CONVERSATIONS:
        cid = conv["id"]
        query = conv["query"]
        category = conv["category"]
        expect = conv["expect"]

        print(f"\n[{cid:02d}] ({category}) Query: {query}")
        print(f"     Expected: {expect}")

        t1 = time.perf_counter()
        try:
            response = engine.answer(query)
            answer = response.get("answer", "") if isinstance(response, dict) else str(response)
            sources = response.get("sources", []) if isinstance(response, dict) else []
        except Exception as e:
            answer = f"ERROR: {e}"
            sources = []

        elapsed = time.perf_counter() - t1

        # Truncate for display
        display_answer = answer[:500] + "..." if len(answer) > 500 else answer
        print(f"     Answer ({elapsed:.1f}s): {display_answer}")
        if verbose and sources:
            print(f"     Sources: {json.dumps(sources[:3], ensure_ascii=False)[:200]}")

        results.append({
            "id": cid,
            "category": category,
            "query": query,
            "answer": answer,
            "sources_count": len(sources),
            "latency_s": round(elapsed, 2),
            "expect": expect,
        })

    # --- Summary ---
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    total_time = sum(r["latency_s"] for r in results)
    avg_time = total_time / len(results)
    print(f"  Total queries: {len(results)}")
    print(f"  Total time:    {total_time:.1f}s")
    print(f"  Avg latency:   {avg_time:.1f}s/query")

    # Categorize results
    errors = [r for r in results if r["answer"].startswith("ERROR")]
    greetings = [r for r in results if r["category"].startswith("greeting")]
    offers = [r for r in results if r["category"].startswith("offer")]
    faqs = [r for r in results if r["category"].startswith("faq")]
    other = [r for r in results if r["category"] in ("mixed_language", "price_query", "out_of_scope", "stock_query", "multi_offer")]

    print(f"\n  Errors:        {len(errors)}")
    print(f"  Greetings:     {len(greetings)} ({sum(1 for g in greetings if len(g['answer']) > 10)} meaningful)")
    print(f"  Offer queries: {len(offers)}")
    print(f"  FAQ queries:   {len(faqs)}")
    print(f"  Other:         {len(other)}")

    # Save results
    results_path = os.path.join(os.path.dirname(__file__), "conversation_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  Results saved to {results_path}")

    print("\n" + "=" * 70)
    print("  REVIEW EACH ANSWER ABOVE MANUALLY")
    print("  Check: correct info, language, no hallucination, no scaffolding leaks")
    print("=" * 70)

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="Show source details")
    args = parser.parse_args()
    run_conversations(verbose=args.verbose)
