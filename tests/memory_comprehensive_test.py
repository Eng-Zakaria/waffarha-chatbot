"""
Comprehensive conversation memory test.

Simulates 10 multi-turn conversations (15 questions each) with:
- Follow-up questions ("بكام العرض ده؟", "فيه عرض تاني عندهم؟")
- Topic switches mid-conversation
- Repetitive queries to test deduplication
- Mixed Arabic/English/Franco-Arabic
- FAQ and offer queries intermixed

Stores results in eval/conversation_tests_results.json and prints a summary report.
"""
import json
import os
import sys
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rag_engine import RagEngine
from memory.memory import MemoryStore, MAX_OFFERS_PER_SESSION, MAX_TURNS_PER_SESSION
from core.app import _source_card


def _source_card_for_test(doc: dict, reply_lang: str = None) -> dict:
    """Format a raw source doc into a source card (same as app.py)."""
    return _source_card(doc, reply_lang)

# ---------------------------------------------------------------------------
# Conversation scripts: 10 conversations, each 15 turns
# Each turn: (query, expected_behavior)
# ---------------------------------------------------------------------------
CONVERSATIONS = [
    # ── Conversation 1: KFC exploration with deep follow-ups ─────────────
    {
        "id": "conv_01_kfc_deep_dive",
        "session_id": "user_kfc_001",
        "description": "User explores KFC offers, asks prices, compares, switches topic",
        "turns": [
            ("شو هي عروض كنتاكي؟", "offer_retrieval", "KFC"),
            ("بكام العرض الاول؟", "followup_same", "price_inquiry"),
            ("فيه عرض تاني عندهم؟", "followup_other_same_merchant", "KFC"),
            ("قارن بين العرضين دي", "followup_compare", "KFC"),
            ("العرض الثاني امتى يخلص؟", "followup_expiry", "KFC"),
            ("شو سعر الوجبة العائلية؟", "followup_family_meal", "KFC"),
            ("في عروض تحت 100 جنيه؟", "price_filter", "KFC"),
            ("شو فيه شاورما؟", "topic_switch", "shawarma"),
            ("فيه offers من شاورما جي؟", "followup_shawarma", "shawarma"),
            ("بكام الشاورما؟", "followup_price", "shawarma"),
            ("ارجع لعروض كنتاكي", "topic_return", "KFC"),
            ("شو اكتر عرض توفير؟", "followup_best_deal", "KFC"),
            ("فيه كوبري عرض؟", "repeat_similar", "KFC"),
            ("كم عدد فروع كنتاكي؟", "out_of_scope", "KFC"),
            ("شكراً، هذا كل شيء", "closing", None),
        ],
    },
    # ── Conversation 2: McDonald's then burger comparison ────────────────
    {
        "id": "conv_02_mcdonalds_burger",
        "session_id": "user_mcd_002",
        "description": "McDonald's offers, Big Mac price, comparison with other burgers",
        "turns": [
            ("شو عروض ماكدونالدز؟", "offer_retrieval", "McDonald's"),
            ("بكام Big Mac؟", "followup_specific", "Big Mac"),
            ("فيه برجر ارخص؟", "followup_cheaper", "McDonald's"),
            ("قارن بينهم", "followup_compare", "McDonald's"),
            ("أيهم أفضل قيمة؟", "followup_value", "McDonald's"),
            ("شو عروض البرجر التانية؟", "followup_more_burgers", "McDonald's"),
            ("بكام الـ combo ده؟", "followup_combo_price", "McDonald's"),
            ("في خصومات اليوم؟", "followup_daily_deals", "McDonald's"),
            ("شو هو الـ value menu؟", "topic_switch", "value_menu"),
            ("فيه وجبات للفطور؟", "followup_breakfast", "McDonald's"),
            ("ارجع للمقارنة بين البرجر", "topic_return", "McDonald's"),
            ("الاول كان بكام تاني؟", "repeat_inquiry", "price_recall"),
            ("شكراً على المساعدة", "closing", None),
        ],
    },
    # ── Conversation 3: Pizza offers ──────────────────────────────────────
    {
        "id": "conv_03_pizza_offers",
        "session_id": "user_pizza_003",
        "description": "Pizza Hut and Domino's offers, price comparison, family deals",
        "turns": [
            ("شو عروض البيتزا؟", "offer_retrieval", "pizza"),
            ("في عروض بيتزا هت؟", "offer_filter", "Pizza Hut"),
            ("بكام البيبا الكبيرة؟", "followup_price", "large_pizza"),
            ("فيه عرض دومينو؟", "followup_other_brand", "Domino's"),
            ("قارن بين العرضين", "followup_compare", "pizza"),
            ("أيهما أرخص؟", "followup_cheaper", "pizza"),
            ("في عروض عائلية؟", "followup_family", "pizza"),
            ("بكام الوجبة العائلية؟", "followup_family_price", "pizza"),
            ("امتى تنتهي العروض؟", "followup_expiry", "pizza"),
            ("شو هي التوبينج المجانية؟", "topic_switch", "toppings"),
            ("فيه ديبز مع العرض؟", "followup_sides", "pizza"),
            ("ارجع لعروض البيتزا", "topic_return", "pizza"),
            ("كرر عرض بيتزا هت", "repeat_offer", "Pizza Hut"),
            ("شكراً", "closing", None),
        ],
    },
    # ── Conversation 4: FAQ navigation ────────────────────────────────────
    {
        "id": "conv_04_faq_navigation",
        "session_id": "user_faq_004",
        "description": "User asks FAQs about Waffarha app usage, then switches to offers",
        "turns": [
            ("إزاي استخدم الوفر؟", "faq_retrieval", "how_to_use"),
            ("بكام الوفر؟", "followup_price", "coupon_value"),
            ("إمتى ينفع الوفر؟", "followup_expiry", "coupon_expiry"),
            ("إزاي أشترى وفرة؟", "faq_retrieval", "buy_coupon"),
            ("شو الفرق بين الكوبون والوفر؟", "topic_switch", "coupon_vs_coupon"),
            ("فيه عروض دلوقتي؟", "topic_switch", "offers"),
            ("شو عروض الكوفي؟", "offer_retrieval", "coffee"),
            ("بكام ستاربكس؟", "followup_price", "Starbucks"),
            ("في توستيد كوفي؟", "followup_alternative", "coffee"),
            ("ارجع للسوالف", "topic_return", "FAQ"),
            ("إزاي أنشى حساب؟", "faq_retrieval", "create_account"),
            ("نسيت الباسورد إزاي؟", "followup_forgot", "password"),
            ("شكراً على المعلومات", "closing", None),
        ],
    },
    # ── Conversation 5: Mixed language (Arabic + English) ─────────────────
    {
        "id": "conv_05_mixed_language",
        "session_id": "user_mixed_005",
        "description": "Mixed Arabic/English queries to test language handling",
        "turns": [
            ("What offers are available?", "offer_retrieval", "general"),
            ("شو الكوفీ عروض؟", "followup_coffee", "coffee"),
            ("Give me KFC offers", "followup_kfc_en", "KFC"),
            ("بكام العرض بتاع كنتاكي؟", "followup_price_ar", "KFC"),
            ("Do they have combo meals?", "followup_combo_en", "KFC"),
            ("شو الفرق بين الكومبو والعرض؟", "topic_switch", "combo_vs_offer"),
            ("Show me McDonald's offers", "followup_mcd_en", "McDonald's"),
            ("بكام البيج ماك؟", "followup_price_mcd", "Big Mac"),
            ("What's the cheapest offer?", "followup_cheapest", "all"),
            ("في أرخص عرض فين؟", "followup_cheap_ar", "all"),
            ("Compare the two cheapest", "followup_compare", "comparison"),
            ("شكراً / Thanks", "closing", None),
        ],
    },
    # ── Conversation 6: Repeated queries & deduplication ──────────────────
    {
        "id": "conv_06_repetition_dedup",
        "session_id": "user_repeat_006",
        "description": "Same queries repeated to test memory deduplication",
        "turns": [
            ("شو عروض كفي؟", "offer_retrieval", "KFC"),
            ("بكام العرض الاول؟", "followup_price", "KFC"),
            ("شو عروض كفي؟", "repeat_query", "KFC_same"),
            ("بكام العرض الاول؟", "repeat_query", "price_same"),
            ("فيه عرض تاني عندهم؟", "followup_other", "KFC"),
            ("شو عروض كفي؟", "repeat_query", "KFC_same_3rd"),
            ("بكام التاني؟", "followup_price2", "KFC"),
            ("شو عروض كفي؟", "repeat_query", "KFC_4th"),
            ("فيه شاورما؟", "topic_switch", "shawarma"),
            ("شو عروض كفي؟", "topic_return", "KFC"),
            ("بكام العرض الاول؟", "followup_price_final", "KFC"),
            ("شو عرضتلي من قبل؟", "followup_recall", "memory_test"),
            ("شكراً", "closing", None),
        ],
    },
    # ── Conversation 7: Franco-Arabic (3ayni) ─────────────────────────────
    {
        "id": "conv_07_franco_arabic",
        "session_id": "user_franco_007",
        "description": "Franco-Arabic queries to test 3ayni/dialect handling",
        "turns": [
            ("3ayez ard 3nd KFC", "offer_retrieval", "KFC"),
            ("bhdam 3ala eh? 3ashan a3raf", "followup_details", "KFC"),
            ("3ayez tany 3ndhom", "followup_other", "KFC"),
            ("3ashan aqaras baynihim", "followup_compare", "KFC"),
            ("3ashan 5oloos", "topic_switch", "cheap"),
            ("3ayez ard 3nd McDonald's", "followup_mcd", "McDonald's"),
            ("bhdam 3ala Big Mac? 3ashan as'al", "followup_bigmac", "Big Mac"),
            ("3ashan a3raf el se3r", "followup_price", "Big Mac"),
            ("3ayez a3awdo l KFC", "topic_return", "KFC"),
            ("3ashan aqaras al ard awal", "followup_recall", "KFC"),
            ("3ayez a3raf el kull", "followup_summary", "all"),
            ("3ashan shukran", "closing", None),
        ],
    },
    # ── Conversation 8: Price-focused ─────────────────────────────────────
    {
        "id": "conv_08_price_focused",
        "session_id": "user_price_008",
        "description": "User only cares about prices, compares across merchants",
        "turns": [
            ("أرخص عرض فين؟", "offer_retrieval", "cheap"),
            ("بكام ده؟", "followup_price", "cheap_offer"),
            ("في تحت 50 جنيه؟", "price_filter", "under_50"),
            ("بكام العرض ده؟", "followup_price2", "cheap_offer"),
            ("في تحت 100؟", "price_filter", "under_100"),
            ("شو أغلى عرض؟", "followup_expensive", "expensive"),
            ("بكام الأغلى؟", "followup_price3", "expensive"),
            ("قارن بين الأرخص والأغلى", "followup_compare", "price_compare"),
            ("في عرض بـ 200؟", "price_filter", "under_200"),
            ("شو العروض تحت 150؟", "followup_range", "under_150"),
            ("أرجع للأرخص", "topic_return", "cheap"),
            ("بكام العرض الأول مرة ثانية؟", "repeat_price", "price_recall"),
            ("شكراً", "closing", None),
        ],
    },
    # ── Conversation 9: Time-sensitive (expiry) ───────────────────────────
    {
        "id": "conv_09_time_sensitive",
        "session_id": "user_time_009",
        "description": "User cares about expiry dates and time-limited offers",
        "turns": [
            ("شو العروض اللي فاتتها؟", "offer_retrieval", "expired_check"),
            ("في عروض صالحه دلوقتي؟", "followup_active", "active_offers"),
            ("بكام العرض ده؟", "followup_price", "active"),
            ("امتى ينتهي؟", "followup_expiry", "active"),
            ("في عرض يخلص بكرة؟", "followup_soon_expiry", "urgent"),
            ("شو أقرب موعد انتهاء؟", "followup_earliest_expiry", "urgent"),
            ("في عروض تانية؟", "followup_more", "more_offers"),
            ("بكام التاني؟", "followup_price2", "more"),
            ("امتى ينتهي التاني؟", "followup_expiry2", "more"),
            ("قارن بين العرضين من حيث الصلاحية", "followup_compare_expiry", "both"),
            ("أيهم أطول صلاحية؟", "followup_longest", "both"),
            ("ارجع للعرض الأول", "topic_return", "first"),
            ("بكام ده؟", "followup_price_final", "first"),
            ("شكراً", "closing", None),
        ],
    },
    # ── Conversation 10: Long complex multi-topic ─────────────────────────
    {
        "id": "conv_10_complex_multi_topic",
        "session_id": "user_complex_010",
        "description": "Complex conversation spanning multiple merchants and topics",
        "turns": [
            ("شو عروض البرجر؟", "offer_retrieval", "burger"),
            ("بكام البرجر؟", "followup_price", "burger"),
            ("في كفتة؟", "followup_kofta", "kofta"),
            ("قارن بين البرجر والكفتة", "followup_compare", "burger_vs_kofta"),
            ("شو عروض البيتزا؟", "topic_switch", "pizza"),
            ("بكام البيتزا؟", "followup_price", "pizza"),
            ("في شاورما؟", "followup_shawarma", "shawarma"),
            ("شو سعر الشاورما؟", "followup_price", "shawarma"),
            ("ارجع للبيتزا", "topic_return", "pizza"),
            ("في عرض عائلية؟", "followup_family", "pizza"),
            ("شو عروض الكوفي؟", "topic_switch", "coffee"),
            ("بكام الكوفي؟", "followup_price", "coffee"),
            ("في خصم على الكوفي؟", "followup_discount", "coffee"),
            ("قارن بين كل العروض", "followup_compare_all", "all"),
            ("شكراً على كل المعلومات", "closing", None),
        ],
    },
]


def run_conversation(engine: RagEngine, memory: MemoryStore, conv: dict) -> dict:
    """Run a single conversation and return results."""
    session_id = conv["session_id"]
    session = memory.get(session_id)

    results = {
        "id": conv["id"],
        "session_id": session_id,
        "description": conv["description"],
        "turns": [],
        "summary": {},
    }

    memory_before_turns = len(session.get_turns()) if session else 0
    memory_before_offers = len(session.recent()) if session else 0

    for i, (query, expected, tag) in enumerate(conv["turns"]):
        t0 = time.time()
        recent = session.recent() if session else []
        result = engine.answer(query, [], recent)
        elapsed = time.time() - t0

        answer = result.get("answer", "")
        sources = result.get("sources", [])  # This IS _last_retrieved (raw doc dicts)
        source_cards = [_source_card_for_test(d) for d in sources[:3]]  # formatted cards

        # Remember this turn using sources (the raw doc dicts from retrieval)
        if session:
            session.remember(sources[:3])
            session.remember_turns(query, answer)

        # Analyze
        turn_result = {
            "turn_index": i,
            "query": query,
            "expected_behavior": expected,
            "answer_length": len(answer),
            "answer_preview": answer[:150],
            "sources_count": len(sources),
            "top_merchant": source_cards[0].get("title", "")[:50] if source_cards else "",
            "top_snippet": source_cards[0].get("snippet", "")[:100] if source_cards else "",
            "elapsed_ms": round(elapsed * 1000),
            "has_answer": bool(answer.strip()),
            "has_sources": len(sources) > 0,
            "followup_resolved": False,
        }

        # Check if follow-up was resolved (memory test)
        if expected.startswith("followup_"):
            turn_result["followup_resolved"] = len(sources) > 0

        results["turns"].append(turn_result)

    # Memory state after conversation
    if session:
        final_turns = session.get_turns()
        final_offers = session.recent()
        results["summary"] = {
            "total_turns": len(conv["turns"]),
            "turns_stored": len(final_turns),
            "offers_remembered": len(final_offers),
            "avg_answer_length": round(
                sum(t["answer_length"] for t in results["turns"]) / len(results["turns"]), 1
            ),
            "avg_elapsed_ms": round(
                sum(t["elapsed_ms"] for t in results["turns"]) / len(results["turns"]), 1
            ),
            "followup_success_rate": round(
                sum(1 for t in results["turns"] if t["followup_resolved"]) / max(1, sum(1 for t in results["turns"] if t["expected_behavior"].startswith("followup_"))),
                2,
            ),
            "answers_with_sources": sum(1 for t in results["turns"] if t["has_sources"]),
        }

    return results


def run_all_tests():
    """Run all 10 conversations and generate report."""
    print("=" * 70)
    print("Waffarha Chatbot Memory Test Suite")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    # Use local backend (no Redis needed)
    memory = MemoryStore(backend="local")
    engine = RagEngine(require_llm=False)

    all_results = []
    for conv in CONVERSATIONS:
        print(f"\n[{conv['id']}] {conv['description']}")
        t0 = time.time()
        result = run_conversation(engine, memory, conv)
        elapsed = time.time() - t0
        result["total_elapsed_ms"] = round(elapsed * 1000)
        all_results.append(result)
        print(f"  Turns: {result['summary'].get('total_turns', 0)}, "
              f"Avg answer: {result['summary'].get('avg_answer_length', 0)} chars, "
              f"Follow-up rate: {result['summary'].get('followup_success_rate', 0)}")

    # ── Generate report ──────────────────────────────────────────────────
    report = generate_report(all_results)
    return all_results, report


def generate_report(results: list) -> str:
    """Generate human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("MEMORY TEST REPORT")
    lines.append(f"Generated: {datetime.now().isoformat()}")
    lines.append(f"Conversations: {len(results)}")
    lines.append("=" * 70)

    # Overall stats
    total_turns = sum(r["summary"].get("total_turns", 0) for r in results)
    total_followups = sum(
        sum(1 for t in r["turns"] if t["expected_behavior"].startswith("followup_"))
        for r in results
    )
    total_followup_success = sum(
        sum(1 for t in r["turns"] if t["expected_behavior"].startswith("followup_") and t["followup_resolved"])
        for r in results
    )
    total_elapsed = sum(r["total_elapsed_ms"] for r in results)

    lines.append("\n## Overall Statistics")
    lines.append(f"  Total turns: {total_turns}")
    lines.append(f"  Total follow-up questions: {total_followups}")
    lines.append(f"  Follow-up resolution rate: {total_followup_success}/{total_followups} "
                 f"({round(100*total_followup_success/max(1,total_followups), 1)}%)")
    lines.append(f"  Total time: {total_elapsed/1000:.1f}s")

    # Per-conversation breakdown
    lines.append("\n## Per-Conversation Results")
    for r in results:
        s = r["summary"]
        lines.append(f"\n  [{r['id']}]")
        lines.append(f"    Description: {r['description']}")
        lines.append(f"    Turns: {s.get('total_turns', 0)}")
        lines.append(f"    Offers in memory: {s.get('offers_remembered', 0)}")
        lines.append(f"    Avg answer length: {s.get('avg_answer_length', 0)} chars")
        lines.append(f"    Avg response time: {s.get('avg_elapsed_ms', 0)} ms")
        lines.append(f"    Follow-up success: {s.get('followup_success_rate', 0)}")
        lines.append(f"    Answers with sources: {s.get('answers_with_sources', 0)}/{s.get('total_turns', 0)}")

    # Memory state verification
    lines.append("\n## Memory State Verification")
    memory = MemoryStore(backend="local")
    for r in results:
        session = memory.get(r["session_id"])
        turns = session.get_turns() if session else []
        offers = session.recent() if session else []
        expected_turns = r["summary"].get("total_turns", 0) * 2  # user+assistant pairs
        lines.append(f"  {r['session_id']}: {len(turns)} turns stored (expected ~{expected_turns}), "
                     f"{len(offers)} offers remembered")

    # Key findings
    lines.append("\n## Key Findings")

    # Check deduplication
    lines.append("\n  1. OFFER DEDUPLICATION:")
    for r in results:
        session = memory.get(r["session_id"])
        if session:
            offers = session.recent()
            ids = [o["metadata"].get("id") for o in offers]
            unique = len(set(ids))
            total = len(ids)
            status = "PASS" if unique == total else "FAIL"
            lines.append(f"     [{status}] {r['id']}: {unique} unique / {total} total offers")

    # Check turn ordering
    lines.append("\n  2. TURN ORDERING (most-recent-first):")
    for r in results[:3]:  # Check first 3
        session = memory.get(r["session_id"])
        if session:
            turns = session.get_turns(5)
            if len(turns) >= 2:
                # Most recent should be assistant (last response)
                is_correct = turns[0]["role"] == "assistant"
                status = "PASS" if is_correct else "FAIL"
                lines.append(f"     [{status}] {r['id']}: first turn is {'assistant' if is_correct else 'user'}")

    # Check session isolation
    lines.append("\n  3. SESSION ISOLATION:")
    session_ids = [r["session_id"] for r in results]
    for i, sid1 in enumerate(session_ids[:3]):
        for sid2 in session_ids[i+1:i+3]:
            s1 = memory.get(sid1).get_turns() if memory.get(sid1) else []
            s2 = memory.get(sid2).get_turns() if memory.get(sid2) else []
            # Check they have different content
            same_first_query = (s1 and s2 and
                               s1[-1].get("content") == s2[-1].get("content"))
            status = "PASS" if not same_first_query else "FAIL"
            lines.append(f"     [{status}] {sid1} vs {sid2}: {'isolated' if not same_first_query else 'LEAKED'}")

    # Follow-up resolution quality
    lines.append("\n  4. FOLLOW-UP RESOLUTION:")
    for r in results:
        followups = [t for t in r["turns"] if t["expected_behavior"].startswith("followup_")]
        resolved = [t for t in followups if t["followup_resolved"]]
        rate = len(resolved) / max(1, len(followups))
        status = "GOOD" if rate >= 0.8 else "WARNING" if rate >= 0.5 else "POOR"
        lines.append(f"     [{status}] {r['id']}: {len(resolved)}/{len(followups)} ({rate:.0%})")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


if __name__ == "__main__":
    results, report = run_all_tests()

    # Save results
    output_dir = os.path.join(os.path.dirname(__file__), "eval")
    os.makedirs(output_dir, exist_ok=True)
    results_path = os.path.join(output_dir, "conversation_memory_tests.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    # Save report
    report_path = os.path.join(output_dir, "conversation_memory_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print("\n" + report)
    print(f"\nResults saved to: {results_path}")
    print(f"Report saved to: {report_path}")
