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

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

BASE_URL = "http://localhost:8000"
SESSION_ID = "manual_eval_session"

def chat(query, history=None):
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
    if not answer or not answer.strip():
        return False, "Empty answer"

    text = answer.lower()
    text_ar = answer

    if out_of_scope:
        price_patterns = ["جنيه", "egp", "le ", "le$"]
        if any(p in text for p in price_patterns):
            return False, "Should have deflected but gave price info"
        deflection_patterns = ["لا يوجد", "مفيش", "عندكم", "خدمات", "دعم", "support", "غير متاح"]
        if any(p in text_ar for p in deflection_patterns):
            return True, "Correctly deflected out-of-scope query"
        return False, "Gave an answer instead of deflecting"

    if forbidden:
        for kw in forbidden:
            if kw.lower() in text:
                return False, f"Forbidden keyword found: {kw}"

    if must_contain:
        missing = []
        for kw in must_contain:
            if kw.lower() not in text.lower() and kw not in text_ar:
                missing.append(kw)
        if missing:
            return False, f"Missing required: {missing}"

    if keywords:
        found = [kw for kw in keywords if kw.lower() in text.lower() or kw in text_ar]
        if found:
            return True, f"Keywords found: {found}"
        return False, f"No keywords matched: {keywords}"

    return True, "Non-empty answer (no specific criteria)"

# Each message: (query, keywords_to_find, forbidden, must_contain, out_of_scope)
conversations = [
    # --- Conv 1: Restaurant offers ---
    {
        "name": "Restaurant Offers",
        "messages": [
            ("السلام عليكم، شوية عروض المطاعم؟", ["ahlan", "welcome", "help"], None, None, False),
            ("عايز أكل برجر، فين أحسن عرض؟", ["burger", "عرض"], None, None, False),
            ("كام سعر البرجر ده؟", ["35", "جنيه"], None, None, False),
            ("طيب في عرض تشيكن أيضا؟", ["chicken", "تشيكن"], None, None, False),
            ("قارنيلي بين البرجر والتشيكن", ["burger", "chicken"], None, None, False),
            ("الشيكين عرضه كام بالضبط؟", ["29", "جنيه"], None, None, False),
            ("عايز أعرف عرض كشري", ["koshary", "كشري"], None, None, False),
            ("كام سعر الكشري؟", ["90", "جنيه"], None, None, False),
            ("الكشري ده بيوصل للبيت ولا لا؟", ["يوصل", "delivery"], None, None, False),
            ("شكراً ليكم على المعلومات", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 2: Hotel offers ---
    {
        "name": "Hotel Offers",
        "messages": [
            ("مرحبا، عندكم عروض فنادق؟", ["hotel", "fundoq"], None, None, False),
            ("عايز إقامة نهار في هيلتون الزمالك", ["hilton", "1680"], None, None, False),
            ("كام سعر الإقامة النهارية؟", ["1680", "جنيه"], None, None, False),
            ("في إقامة ليلية معاهم؟", ["7240", "night"], None, None, False),
            ("قارنيلي بين النهار والليلة", ["1680", "7240"], None, None, False),
            ("الإقامة النهارية دى فيها إفطار؟", ["breakfast", "إفطار"], None, None, False),
            ("فين الفندق ده بالضبط؟", ["zamalek"], None, None, False),
            ("عندكم فنادق أخرى في الإسكندرية؟", ["alexandria"], None, None, False),
            ("كام أغلى فندق عندكم؟", ["2044", "steigenberger"], None, None, False),
            ("شكراً، هتفكر في الأمر", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 3: KFC offers ---
    {
        "name": "KFC Offers",
        "messages": [
            ("عندي شغف كنتاكي، عندكم إيه؟", ["kfc", "kentucky"], None, None, False),
            ("عايز أعرف التفاصيل", ["250", "189", "discount"], None, None, False),
            ("كام كان سعره قبل الخصم؟", ["485", "old", "was"], None, None, False),
            ("العرض ده لسه شغال؟", ["صالح", "valid"], None, None, False),
            ("إمتى بيخلص العرض؟", ["2026", "expiry"], None, None, False),
            ("في عروض تانية من كنتاكي؟", ["4", "other"], None, None, False),
            ("لو عايز أכול عيلتي، أنصحني بأيه؟", ["family", "وجبة"], None, None, False),
            ("عندكم خدمة توصيل؟", ["delivery", "يوصل"], None, None, False),
            ("مشوار تمام، شكراً", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 4: FAQ - How to use ---
    {
        "name": "FAQ - App Usage",
        "messages": [
            ("أنا جديد على التطبيق، ازاي أبدأ؟", ["register", "كود"], None, None, False),
            ("إزاي أشتري كوبون من التطبيق؟", ["cart", "عربة"], None, None, False),
            ("طرق الدفع المتاحة إيه؟", ["visa", "apple pay"], None, None, False),
            ("أنا اشتريت كوبون، إزاي أستخدمه؟", ["orders", "كوبون"], None, None, False),
            ("حالة الطلب 'مستعمل' معناه إيه؟", ["used", "مستعمل"], None, None, False),
            ("أنا عايز أرجع فلوسي، إزاي؟", ["refund", "استرداد"], None, None, False),
            ("الكاش باك بيتصرف إزاي؟", ["30", "cashback"], None, None, False),
            ("إيه فائدة تطبيق وفرها بالضبط؟", ["group", "negotiate"], None, None, False),
            ("شكراً على التوضيح", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 5: Superlatives ---
    {
        "name": "Superlative Queries",
        "messages": [
            ("أرخص عرض عندكم قد إيه؟", ["35", "cheapest"], None, None, False),
            ("أغلى عرض فين وكام؟", ["9100", "yacht"], None, None, False),
            ("أكبر خصم فين؟", ["85", "discount"], None, None, False),
            ("في عروض تحت 100 جنيه؟", ["100", "under"], None, None, False),
            ("في عروض من 300 لحد 800؟", ["300", "800"], None, None, False),
            ("أنصحوني بأحسن عرض حاليا", ["best", "recommend"], None, None, False),
            ("عندي ميزانية 200 جنيه، إيه المناسب؟", ["200", "budget"], None, None, False),
            ("شكرا على التوصيات", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 6: Entertainment ---
    {
        "name": "Entertainment & Activities",
        "messages": [
            ("عايز أفكر إيه في نهاية الأسبوع؟", ["entertainment", "activity"], None, None, False),
            ("في عروض cinema؟", ["cinema", "سينما"], None, None, False),
            ("كام سعر تذكرة السينما؟", ["21", "cinema"], None, None, False),
            ("عروض فون قديم كام؟", ["143", "fun kingdom"], None, None, False),
            ("أنيمانيا زوو بكام؟", ["32", "animania"], None, None, False),
            ("القرية الفرعونية فيها عروض قد إيه؟", ["63", "pharaonic"], None, None, False),
            ("عروض للأطفال فين؟", ["kids", "children"], None, None, False),
            ("شكرا، هروح مع العيلة", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 7: Food & beverages ---
    {
        "name": "Food & Beverages",
        "messages": [
            ("عايز أشرب قهوة، عندكم إيه؟", ["coffee", "قهوة"], None, None, False),
            ("كام سعر قهوة كوفي شوب؟", ["88", "coffee"], None, None, False),
            ("في شاورما؟ كام سعرها؟", ["shawerma", "شاورما", "47"], None, None, False),
            ("ويفليشوس عندهم عرض بكام؟", ["10", "wafflicious", "waffle"], None, None, False),
            ("زادنا عندها حلويات كام؟", ["70", "zadna"], None, None, False),
            ("حندرد ديجريز عندها سحور بكام؟", ["85", "ramadan"], None, None, False),
            ("أتلانتس كورنرز فيه كام عرض؟", ["130", "atlantis"], None, None, False),
            ("شكرا، أكل الكترونات", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 8: Wellness & beauty ---
    {
        "name": "Wellness & Beauty",
        "messages": [
            ("عايز أعمل سبا ومساج", ["spa", "مساج"], None, None, False),
            ("كام سعر الـ spa؟", ["price", "سعر"], None, None, False),
            ("عروض تجميل وشعر فين؟", ["beauty", "تجميل", "hair"], None, None, False),
            ("كام teeth whitening عند Dental Boss؟", ["750", "dental", "teeth"], None, None, False),
            ("أرخص عرض في Dental Boss كام؟", ["75", "dental", "cheapest"], None, None, False),
            ("بامبو نيل سبا فيه عروض كام؟", ["nail", "bamboo"], None, None, False),
            ("في عروض gym؟", ["gym", "fitness"], None, None, False),
            ("شكرا على العروض", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 9: Franco-Arabic ---
    {
        "name": "Franco-Arabic Queries",
        "messages": [
            ("3ayez a3raf kam offer el KFC?", ["kfc", "189"], None, None, False),
            ("discount McDonald's be kam ya som3a?", ["mcdonald", "79"], None, None, False),
            ("waffle wafflicious be kam ya basha?", ["waffle", "10", "wafflicious"], None, None, False),
            ("hilton zamalek 3afya kam?", ["hilton", "1680"], None, None, False),
            ("fun kingdom 3afyat kam?", ["fun kingdom", "143"], None, None, False),
            ("arabizi offer kam?", ["price", "kam"], None, None, False),
            ("shukran ya mu3allem", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 10: Out-of-scope tests ---
    {
        "name": "Out-of-Scope Tests",
        "messages": [
            ("عاملين إيه الجو في القاهرة النهاردة؟", [], ["weather", "temperature", "طقس"], None, True),
            ("عندي وجع راس، آخذ إيه دواء؟", [], ["ibuprofen", "medicine", "دواء"], None, True),
            ("اعمليلي نكتة", [], ["joke", "نكتة"], None, True),
            ("سويتشي من ستياربكس", ["starbucks", "EGP"], None, None, True),
            ("عندكم بيتزا هت؟", ["pizza", "hut", "EGP"], None, None, True),
            ("الطقس عامل ايه", [], ["weather"], None, True),
            ("مين رئيس مصر دلوقتي؟", [], ["president"], None, True),
            ("في عرض بيليني؟", ["bellini", "EGP"], None, None, True),
            ("شكرا على الشفافية", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 11: Price range ---
    {
        "name": "Price Range Filtering",
        "messages": [
            ("عايز عروض تحت 50 جنيه", ["50", "under"], None, None, False),
            ("في حاجات اغلى من كده؟", ["100", "200"], None, None, False),
            ("عايز عروض من 100 لحد 300", ["100", "300"], None, None, False),
            ("في حاجة فوق 500؟", ["500", "above"], None, None, False),
            ("أنا طالب وبقتي ضيقة، في عروض تحت 30 جنيه؟", ["30", "student"], None, None, False),
            ("أحسن عرض تحت 100 جنيه هو إيه؟", ["100", "best"], None, None, False),
            ("شكرا على المساعدة", ["shukran", "thanks"], None, None, False),
        ]
    },
    # --- Conv 12: Delivery ---
    {
        "name": "Delivery & Availability",
        "messages": [
            ("كشري التحرير بيوصل للدار؟", ["koshary", "delivery"], None, None, False),
            ("الحواوشي الرفاعي فيه توصيل؟", ["hawawshy", "delivery"], None, None, False),
            ("ويفليشوس بيوصل للبيت؟", ["wafflicious", "delivery"], None, None, False),
            ("العرض ده بيوصل؟", ["delivery", "يوصل"], None, None, False),
            ("فاضل كام كوبون من كنتاكي؟", ["stock", "remaining"], None, None, False),
            ("أنيمانيا زوو فاضي ولا فيه كوبونات؟", ["animania", "stock"], None, None, False),
            ("شكرا، هطلب دلوقتي", ["shukran", "thanks"], None, None, False),
        ]
    },
]

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
        conv_result["passed"] += 1 if passed else 0

        conv_result["messages"].append({
            "question": query,
            "answer": answer,
            "score": score,
            "status": status,
            "reason": reason
        })
        print(f"      Status: {status}")

        if answer:
            history.append({"role": "user", "content": query})
            history.append({"role": "assistant", "content": answer})

        time.sleep(1.5)

    conv_result["score"] = conv_result["passed"] / max(conv_result["total"], 1)
    results["conversations"].append(conv_result)
    print(f"\n  Conversation Score: {conv_result['passed']}/{conv_result['total']} passed")

print(f"\n{'='*60}")
print("EVALUATION SUMMARY")
print(f"{'='*60}")
print(f"Total Conversations: {len(conversations)}")
print(f"Total Questions: {total_questions}")
print(f"Questions Passed: {total_passed}")
overall = (total_score / total_questions * 100) if total_questions > 0 else 0
print(f"Overall Score: {overall:.1f}%")
print(f"\nBreakdown by Conversation:")
for conv in results["conversations"]:
    pct = (conv["passed"] / conv["total"] * 100) if conv["total"] > 0 else 0
    print(f"  - {conv['name']}: {conv['passed']}/{conv['total']} ({pct:.0f}%)")

output_path = f"eval/manual_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\nResults saved to: {output_path}")
