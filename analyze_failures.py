"""
Diagnostic script: analyzes failed retrieval cases to understand WHY they fail.
Run: python analyze_failures.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.config
sys.modules['config'] = core.config

from core.rag_engine import RagEngine
import json

print("Loading engine...")
engine = RagEngine(
    embedding_model='intfloat/multilingual-e5-base',
    backend='faiss',
    require_llm=False,
)

with open('eval/queries.json', 'r', encoding='utf-8') as f:
    cases = json.load(f)

# Focus on failed categories from the retrieval test
FAILED_IDS = [
    # same_merchant_disambiguation
    "same_merchant_disambiguation_tamara_iftar",
    "same_merchant_disambiguation_tamara_sohour",
    "same_merchant_disambiguation_desoky_buffet",
    "same_merchant_disambiguation_desoky_sohour",
    "same_merchant_disambiguation_insomnia_event",
    "same_merchant_disambiguation_hawawshy_price",
    # offer_ranking
    "offer_highest_discount_verified",
    "offer_most_expensive",
    "offer_cheapest_ar",
    "offer_highest_discount_ar",
    # mixed_language
    "mixed_language_query_kfc",
    "mixed_language_query_asianwok",
    # arabizi
    "arabizi_query_kfc",
    # fuzzy match
    "offer_typo_merchant_asianwok",
    # FAQ failures
    "faq_direct_answer_purchase",
    "faq_direct_answer_use_coupon",
    "faq_direct_answer_bill_payment",
    "faq_arabic_bill_payment",
    "faq_payment_other_wallets_refund",
    # delivery
    "offer_delivery_query_teta",
    "followup_anaphora_delivery_negative",
]

for case in cases:
    if case.get("id") not in FAILED_IDS:
        continue

    query = case["query"]
    expected_id = case.get("expected_id")
    expected_source = case.get("expected_source")

    print(f"\n{'='*80}")
    print(f"ID: {case['id']}")
    print(f"Category: {case.get('category')}")
    print(f"Query: {query}")
    print(f"Expected: {expected_source}:{expected_id}")

    try:
        retrieved = engine.retrieve(query, top_k=10)
        print(f"Retrieved {len(retrieved)} results:")
        for i, r in enumerate(retrieved[:5]):
            meta = r["metadata"]
            title = (meta.get("title") or meta.get("question") or "")[:60]
            merchant = meta.get("merchant", "")
            print(f"  {i+1}. {meta.get('source')}:{meta.get('id')} score={r['combined_score']:.4f} lex={r.get('lexical_hits',0)} \"{title}\" merchant={merchant}")

        # Check if expected is in results
        found = False
        rank = None
        for i, r in enumerate(retrieved):
            if str(r["metadata"].get("id")) == str(expected_id):
                found = True
                rank = i + 1
                break
        if expected_id:
            print(f"  Expected {expected_id} found: {found} (rank: {rank})")
    except Exception as e:
        print(f"  ERROR: {e}")
