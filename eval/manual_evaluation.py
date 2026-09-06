"""
Manual evaluation of Waffarha chatbot via API.
Runs 12 conversation flows with 10-20 messages each, all in Arabic.
Evaluates accuracy, context retention, and response quality.
"""
import json
import re
import time
import sys
import requests
from datetime import datetime

# Force UTF-8 output for Windows console
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE_URL = "http://localhost:8000"
SESSION_ID = "manual_eval_session"

def chat(query, history=None):
    """Send a chat request and return response."""
    if history is None:
        history = []
    payload = {
        "query": query,
        "history": history,
        "session_id": SESSION_ID,
        "lang": "ar"
    }
    try:
        resp = requests.post(f"{BASE_URL}/api/chat", json=payload, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e), "answer": "", "sources": []}

def check_answer(answer, keywords=None, forbidden=None, must_contain=None, out_of_scope=False):
    """Evaluate an answer against expected criteria."""
    if not answer or not answer.strip():
        return False, "Empty answer"

    text = answer.lower()
    text_ar = answer

    # Out of scope should not contain price/offer info
    if out_of_scope:
        price_patterns = ["جنيه", "egp", "le ", "le$", "price"]
        if any(p in text for p in price_patterns):
            return False, "Should have deflected but gave price info"
        # Good deflection: mentions support or doesn't answer
        if any(p in text_ar for p in ["لا يوجد", "مفيش", "عندكم", "خدمات", "دعم", "support"]):
            return True, "Correctly deflected out-of-scope query"
        return False, "Gave an answer instead of deflecting"

    # Check forbidden keywords
    if forbidden:
        for kw in forbidden:
            if kw.lower() in text:
                return False, f"Forbidden keyword found: {kw}"

    # Check must_contain (all must be present)
    if must_contain:
        missing = []
        for kw in must_contain:
            if kw.lower() not in text.lower() and kw not in text_ar:
                missing.append(kw)
        if missing:
            return False, f"Missing required: {missing}"

    # Check keywords (at least one must be present)
    if keywords:
        found = [kw for kw in keywords if kw.lower() in text.lower() or kw in text_ar]
        if found:
            return True, f"Keywords found: {found}"
        return False, f"No keywords matched: {keywords}"

    # If no criteria, just check non-empty
    return True, "Non-empty answer (no specific criteria)"

# ============================================================================
# CONVERSATION DEFINITIONS
# Format: (query, keywords_to_find, forbidden_keywords, must_contain, out_of_scope)
# ============================================================================

conversations = [
    # --- Conversation 1: Restaurant offers exploration ---
    {
        "name": "مطاعم وعروض الطعام",
        "messages": [
            ("السلام عليكم، شوية عروض المطاعم؟", ["أهلا", "welcome", "help"], None, None, False),
            ("عايز أكل برجر، فين أحسن عرض؟", ["burger", "برجر", "عرض"], None, None, False),
            ("كام سعر البرجر ده؟", ["35", "جنيه", "EGP"], None, None, False),
            ("طيب في عرض تشيكن أيضا؟", ["chicken", "تشيكن", "كشوري"], None, None, False),
            ("قارنيلي بين البرجر والتشيكن", ["burger", "chicken", "برجر", "تشيكن"], None, None, False),
            ("الشيكين عرضه كام بالضبط؟", ["29", "جنيه", "EGP"], None, None, False),
            ("عايز أعرف عرض كشري", ["koshary", "كشري"], None, None, False),
            ("كام سعر الكشري؟", ["90", "جنيه", "EGP"], None, None, False),
            ("الكشري ده بيوصل للبيت ولا لا؟", ["يوصل", "delivery"], None, None, False),
            ("شكراً ليكم على المعلومات", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 2: Hotel offers ---
    {
        "name": "فنادق وإقامة",
        "messages": [
            ("مرحبا، عندكم عروض فنادق؟", ["أهلا", "hotel", "fundoq"], None, None, False),
            ("عايز إقامة نهار في هيلتون الزمالك", ["hilton", "هيلتون", "1680"], None, None, False),
            ("كام سعر الإقامة النهارية؟", ["1680", "جنيه", "EGP"], None, None, False),
            ("في إقامة ليلية معاهم؟", ["7240", "ليلية", "night"], None, None, False),
            ("قارنيلي بين النهار والليلة", ["1680", "7240", "نهار", "ليلة"], None, None, False),
            ("الإقامة النهارية دى فيها إفطار؟", ["breakfast", "إفطار"], None, None, False),
            ("فين الفندق ده بالضبط؟", ["zamalek", "الزمالك"], None, None, False),
            ("عندكم فنادق أخرى في الإسكندرية؟", ["alexandria", "اسكندرية"], None, None, False),
            ("كام أغلى فندق عندكم؟", ["2044", "steigenberger", "pyramids"], None, None, False),
            ("شكراً، هتفكر في الأمر", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 3: KFC specific offers ---
    {
        "name": "عروض كنتاكي",
        "messages": [
            ("عندي شغف كنتاكي، عندكم إيه؟", ["kfc", "كنتاكي", "عرض"], None, None, False),
            ("عايز أعرف التفاصيل", ["250", "189", "خصم", "دجاج"], None, None, False),
            ("كام كان سعره قبل الخصم؟", ["485", "قبل", "old", "was"], None, None, False),
            ("العرض ده لسه شغال؟", ["صالح", "شغال", "valid"], None, None, False),
            ("إمتى بيخلص العرض؟", ["2026", "expiry", "صالح حتى"], None, None, False),
            ("في عروض تانية من كنتاكي؟", ["4", "other", "تانية", "عروض"], None, None, False),
            ("لو عايز أכול عيلتي، أنصحني بأيه؟", ["family", "عائلة", "وجبة"], None, None, False),
            ("عندكم خدمة توصيل؟", ["delivery", "يوصل", "توصيل"], None, None, False),
            ("مشوار تمام، شكراً", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 4: FAQ - How to use the app ---
    {
        "name": "أسئلة الاستخدام",
        "messages": [
            ("أنا جديد على التطبيق، ازاي أبدأ؟", ["register", "تسجيل", "كود"], None, None, False),
            ("إزاي أشتري كوبون من التطبيق؟", ["cart", "عربة", "شراء"], None, None, False),
            ("طرق الدفع المتاحة إيه؟", ["visa", "apple pay", "fawry"], None, None, False),
            ("أنا اشتريت كوبون، إزاي أستخدمه؟", ["orders", "كوبون", "كود"], None, None, False),
            ("حالة الطلب 'مستعمل' معناه إيه؟", ["used", "مستعمل", "استخدم"], None, None, False),
            ("أنا عايز أرجع فلوسي، إزاي؟", ["refund", "استرداد", "إلغاء"], None, None, False),
            ("الكاش باك بيتصرف إزاي؟", ["30", "cashback", "كاش باك"], None, None, False),
            ("إيه فائدة تطبيق وفرها بالضبط؟", ["group", "شراء جماعي", "negotiate"], None, None, False),
            ("شكراً على التوضيح", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 5: Superlative queries ---
    {
        "name": "أفضل العروض وأسعارها",
        "messages": [
            ("أرخص عرض عندكم قد إيه؟", ["35", "cheapest", "ar3s"], None, None, False),
            ("أغلى عرض فين وكام؟", ["9100", "yacht", "ylt", "agla"], None, None, False),
            ("أكبر خصم فين؟", ["85", "amani", "tunsi", "discount"], None, None, False),
            ("في عروض تحت 100 جنيه؟", ["100", "under", "تحت"], None, None, False),
            ("في عروض من 300 لحد 800؟", ["300", "800", "between", "من"], None, None, False),
            ("أنصحوني بأحسن عرض حاليا", ["recommend", "best", "احسن"], None, None, False),
            ("عندي ميزانية 200 جنيه، إيه المناسب؟", ["200", "budget", "ميزانية"], None, None, False),
            ("شكرا على التوصيات", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 6: Entertainment & activities ---
    {
        "name": "ترفيه وأنشطة",
        "messages": [
            ("عايز أفكر إيه في نهاية الأسبوع؟", ["entertainment", "ترفيه", "activity"], None, None, False),
            ("في عروض cinema؟", ["cinema", "سينما", "movie"], None, None, False),
            ("كام سعر تذكرة السينما؟", ["21", "cinema", "جنيه"], None, None, False),
            ("عروض فون قديم كام؟", ["143", "fun kingdom", "kingdom"], None, None, False),
            ("أنيمانيا زوو بكام؟", ["32", "animania", "zoo"], None, None, False),
            ("القرية الفرعونية فيها عروض قد إيه؟", ["63", "pharaonic", "قرية"], None, None, False),
            ("عروض للأطفال فين؟", ["kids", "أطفال", "entertainment"], None, None, False),
            ("شكرا، هروح مع العيلة", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 7: Food & beverages beyond fast food ---
    {
        "name": "أطعمة ومشروبات متنوعة",
        "messages": [
            ("عايز أشرب قهوة، عندكم إيه؟", ["coffee", "قهوة", "koffeeshop"], None, None, False),
            ("كام سعر قهوة كوفي شوب؟", ["88", "coffee", "جنيه"], None, None, False),
            ("في شاورما؟ كام سعرها؟", ["shawerma", "شاورما", "47"], None, None, False),
            ("ويفليشوس عندهم عرض بكام؟", ["10", "wafflicious", "waffle"], None, None, False),
            ("زادنا عندها حلويات كام؟", ["70", "zadna", "حلوى"], None, None, False),
            ("حندرد ديجريز عندها سحور بكام؟", ["85", "ramadan", "سحور"], None, None, False),
            ("أتلانتس كورنرز فيه كام عرض؟", ["130", "atlantis", "corners"], None, None, False),
            ("شكرا، أكل الكترونات", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 8: Wellness & beauty ---
    {
        "name": "عناية وجمال",
        "messages": [
            ("عايز أعمل سبا ومساج", ["spa", "مساج", "wellness"], None, None, False),
            ("كام سعر الـ spa؟", ["price", "كام", "سعر"], None, None, False),
            ("عروض تجميل وشعر فين؟", ["beauty", "تجميل", "hair", "شعر"], None, None, False),
            ("كام teeth whitening عند Dental Boss؟", ["750", "dental", "teeth"], None, None, False),
            ("أرخص عرض في Dental Boss كام؟", ["75", "dental", "cheapest", "ar3s"], None, None, False),
            ("بامبو نيل سبا فيه عروض كام؟", ["nail", "bamboo", "spaa"], None, None, False),
            ("في عروض gym؟", ["gym", "جيم", "fitness"], None, None, False),
            ("شكرا على العروض", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 9: Mixed Franco-Arabic queries ---
    {
        "name": "استعلامات عربية مفرنجلة",
        "messages": [
            ("3ayez a3raf kam offer el KFC?", ["kfc", "189", "250"], None, None, False),
            ("discount McDonald's be kam ya som3a?", ["mcdonald", "79", "discount"], None, None, False),
            ("waffle wafflicious be kam ya basha?", ["waffle", "10", "wafflicious"], None, None, False),
            ("hilton zamalek 3afya kam?", ["hilton", "1680", "zamalek"], None, None, False),
            ("fun kingdom 3afyat kam?", ["fun kingdom", "143"], None, None, False),
            ("arabizi offer kam?", ["price", "kam", "سعر"], None, None, False),
            ("shukran ya mu3allem", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 10: Out-of-scope & hallucination tests ---
    {
        "name": "اختبارات النطاق والاحتيال",
        "messages": [
            ("عاملين إيه الجو في القاهرة النهاردة؟ ☀️", [], ["weather", "temperature", "طقس"], None, True),
            ("عندي وجع头部، آخذ إيه دواء؟ 🤒", [], ["ibuprofen", "medicine", "دواء"], None, True),
            ("اعمليلي نكتة 😂", [], ["joke", "نكتة"], None, True),
            ("سويتشي من ستياربكس ☕", ["starbucks", "EGP", "جنيه"], None, True),
            ("عندكم بيتزا هت؟🍕", ["pizza", "hut", "EGP"], None, True),
            ("الطقس عامل ايه", [], ["weather"], None, True),
            ("مين رئيس مصر دلوقتي؟ 🇪🇬", [], ["president"], None, True),
            ("في عرض بيليني؟ 👔", ["bellini", "EGP", "100"], None, True),
            ("شكرا على الشفافية", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 11: Price range filtering ---
    {
        "name": "تصفية حسب السعر",
        "messages": [
            ("عايز عروض تحت 50 جنيه 🙏", ["50", "under", "تحت"], None, None, False),
            ("في حاجات اغلى من كده؟", ["100", "200", "more", "اغلى"], None, None, False),
            ("عايز عروض من 100 لحد 300", ["100", "300", "between", "من"], None, None, False),
            ("في حاجة فوق 500؟ 🏦", ["500", "above", "فوق"], None, None, False),
            ("أنا طالب وبقتي ضيقة، في عروض تحت 30 جنيه؟", ["30", "student", "تحت"], None, None, False),
            ("أحسن عرض تحت 100 جنيه هو إيه؟", ["100", "best", "احسن"], None, None, False),
            ("شكرا على المساعدة", ["شكرا", "thanks"], None, None, False),
        ]
    },
    # --- Conversation 12: Delivery & availability ---
    {
        "name": "التوصيل والتوافر",
        "messages": [
            ("كشري التحرير بيوصل للدار؟ 🛵", ["koshary", "delivery", "يوصل"], None, None, False),
            ("الحواوشي الرفاعي فيه توصيل؟", ["hawawshy", "delivery", "يوصل"], None, None, False),
            ("ويفليشوس بيوصل للبيت؟", ["wafflicious", "delivery", "يوصل"], None, None, False),
            ("العرض ده بيوصل؟ 🚗", ["delivery", "يوصل"], None, None, False),
            ("فاضل كام كوبون من كنتاكي؟ 📦", ["stock", "فاضل", "remaining"], None, None, False),
            ("أنيمانيا زوو فاضي ولا فيه كوبونات؟", ["animania", "stock", "فاضل"], None, None, False),
            ("شكرا، هطلب دلوقتي", ["شكرا", "thanks"], None, None, False),
        ]
    },
]

# ============================================================================
# RUN EVALUATION
# ============================================================================

results = {
    "timestamp": datetime.now().isoformat(),
    "total_conversations": len(conversations),
    "conversations": []
}

total_questions = 0
total_passed = 0
total_score = 0.0

for conv_idx, conv in enumerate(conversations):
    print(f"\n{'='*60}")
    print(f"Conversation {conv_idx + 1}: {conv['name']}")
    print(f"{'='*60}")

    conv_result = {
        "name": conv["name"],
        "messages": [],
        "score": 0.0,
        "passed": 0,
        "total": 0
    }

    history = []

    for msg_idx, msg_data in enumerate(conv["messages"]):
        query, keywords, forbidden, must_contain, out_of_scope = msg_data

        print(f"\n  [{msg_idx + 1}] Q: {query[:70]}...")

        response = chat(query, history)
        answer = response.get("answer", "")

        print(f"      A: {answer[:150]}...")

        # Evaluate
        total_questions += 1
        conv_result["total"] += 1

        passed, reason = check_answer(answer, keywords, forbidden, must_contain, out_of_scope)

        if passed:
            score = 1.0
            status = "PASSED"
            total_passed += 1
        else:
            score = 0.0
            status = f"FAILED: {reason}"

        total_score += score

        conv_result["messages"].append({
            "question": query,
            "answer": answer,
            "score": score,
            "status": status,
            "reason": reason
        })
        print(f"      Status: {status}")

        # Update history
        if answer:
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": answer})

        # Small delay between requests
        time.sleep(1.5)

    conv_result["score"] = conv_result["passed"] / max(conv_result["total"], 1) if conv_result["passed"] > 0 else 0
    results["conversations"].append(conv_result)
    passed_count = sum(1 for m in conv_result["messages"] if m["score"] > 0)
    print(f"\n  Conversation Score: {passed_count}/{conv_result['total']} passed")

# Summary
print(f"\n{'='*60}")
print("EVALUATION SUMMARY")
print(f"{'='*60}")
print(f"Total Conversations: {len(conversations)}")
print(f"Total Questions: {total_questions}")
print(f"Questions Passed: {total_passed}")
overall_score = (total_score / total_questions * 100) if total_questions > 0 else 0
print(f"Overall Score: {overall_score:.1f}%")
print(f"\nBreakdown by Conversation:")
for conv in results["conversations"]:
    passed = sum(1 for m in conv["messages"] if m["score"] > 0)
    total = len(conv["messages"])
    pct = (passed / total * 100) if total > 0 else 0
    print(f"  - {conv['name']}: {passed}/{total} ({pct:.0f}%)")

# Save results
output_path = f"eval/manual_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\nResults saved to: {output_path}")
