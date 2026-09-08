"""Compare Waffarha Chatbot vs Claude Sonnet 5 on the same questions.
Claude has NO access to Waffarha catalog data."""
import json, os, sys, time, httpx
from datetime import datetime

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

os.environ['NO_PROXY'] = '*'
from dotenv import load_dotenv
load_dotenv()

CLAUDE_KEY = os.getenv('CLAUDE_API_KEY')

# Load chatbot results
with open('eval/manual_eval_20260906_115141.json', 'r', encoding='utf-8') as f:
    cb_results = json.load(f)

def call_claude(query):
    transport = httpx.HTTPTransport(proxy=None)
    client = httpx.Client(transport=transport, timeout=60)
    headers = {'Authorization': f'Bearer {CLAUDE_KEY}', 'Anthropic-Version': '2023-06-01', 'Content-Type': 'application/json'}
    data = {'model': 'claude-sonnet-5', 'max_tokens': 300, 'messages': [{'role': 'user', 'content': query}]}
    try:
        r = client.post('https://api.anthropic.com/v1/messages', json=data, headers=headers)
        if r.status_code == 200:
            blocks = r.json().get('content', [])
            return '\n'.join(b.get('text','') for b in blocks if isinstance(b, dict)).strip()
        return f'[ERR:{r.status_code}]'
    except Exception as e:
        return f'[EXC:{str(e)[:60]}]'

# Same questions as original eval - format: (query, keywords, out_of_scope)
questions = [
    # Restaurant (10)
    ("السلام عليكم، شوية عروض المطاعم؟", ["ahlan","welcome"], False),
    ("عايز أكل برجر، فين أحسن عرض؟", ["burger","عرض"], False),
    ("كام سعر البرجر ده؟", ["35","جنيه"], False),
    ("طيب في عرض تشيكن أيضا؟", ["chicken","تشيكن"], False),
    ("قارنيلي بين البرجر والتشيكن", ["burger","chicken"], False),
    ("الشيكين عرضه كام بالضبط؟", ["29","جنيه"], False),
    ("عايز أعرف عرض كشري", ["koshary","كشري"], False),
    ("كام سعر الكشري؟", ["90","جنيه"], False),
    ("الكشري ده بيوصل للبيت ولا لا؟", ["يوصل","delivery"], False),
    ("شكراً ليكم على المعلومات", ["shukran","thanks"], False),
    # Hotel (10)
    ("مرحبا، عندكم عروض فنادق؟", ["hotel","فند"], False),
    ("عايز إقامة نهار في هيلتون الزمالك", ["hilton","1680"], False),
    ("كام سعر الإقامة النهارية؟", ["1680","جنيه"], False),
    ("في إقامة ليلية معاهم؟", ["7240","night"], False),
    ("قارنيلي بين النهار والليلة", ["1680","7240"], False),
    ("الإقامة النهارية دى فيها إفطار؟", ["breakfast","إفطار"], False),
    ("فين الفندق ده بالضبط؟", ["zamalek"], False),
    ("عندكم فنادق أخرى في الإسكندرية؟", ["alexandria"], False),
    ("كام أغلى فندق عندكم؟", ["2044","steigenberger"], False),
    ("شكراً، هتفكر في الأمر", ["shukran","thanks"], False),
    # FAQ (9)
    ("أنا جديد على التطبيق، ازاي أبدأ؟", ["register","كود"], False),
    ("إزاي أشتري كوبون من التطبيق؟", ["cart","عربة"], False),
    ("طرق الدفع المتاحة إيه؟", ["visa","apple pay"], False),
    ("أنا اشتريت كوبون، إزاي أستخدمه؟", ["orders","كوبون"], False),
    ("حالة الطلب 'مستعمل' معناه إيه؟", ["used","مستعمل"], False),
    ("أنا عايز أرجع فلوسي، إزاي؟", ["refund","استرداد"], False),
    ("الكاش باك بيتصرف إزاي؟", ["30","cashback"], False),
    ("إيه فائدة تطبيق وفرها بالضبط؟", ["group","negotiate"], False),
    ("شكراً على التوضيح", ["shukran","thanks"], False),
    # Out-of-scope (9)
    ("عاملين إيه الجو في القاهرة النهاردة؟", [], True),
    ("عندي وجع راس، آخذ إيه دواء؟", [], True),
    ("اعمليلي نكتة", [], True),
    ("سويتشي من ستياربكس", ["starbucks","EGP"], True),
    ("عندكم بيتزا هت؟", ["pizza","hut","EGP"], True),
    ("الطقس عامل ايه", [], True),
    ("مين رئيس مصر دلوقتي؟", [], True),
    ("في عرض بيليني؟", ["bellini","EGP"], True),
    ("شكرا على الشفافية", ["shukran","thanks"], False),
    # Franco-Arabic (7)
    ("3ayez a3raf kam offer el KFC?", ["kfc","189"], False),
    ("discount McDonald's be kam ya som3a?", ["mcdonald","79"], False),
    ("waffle wafflicious be kam ya basha?", ["waffle","10","wafflicious"], False),
    ("hilton zamalek 3afya kam?", ["hilton","1680"], False),
    ("fun kingdom 3afyat kam?", ["fun kingdom","143"], False),
    ("arabizi offer kam?", ["price","kam"], False),
    ("shukran ya mu3allem", ["shukran","thanks"], False),
]

def eval_chatbot(query, keywords, oos):
    for c in cb_results['conversations']:
        for m in c['messages']:
            if m['question'] == query:
                return m['score'] > 0, m['reason'], m['score'], m['answer']
    return False, "not found", 0.0, ""

def eval_claude(query, keywords, oos, answer):
    if not answer or answer in ('...',''):
        return False, "Empty response", 0.0
    text = answer.lower()
    # Out-of-scope: must NOT give prices
    if oos:
        if any(p in text for p in ['جنيه','egp','le ','le$']):
            return False, "Hallucinated price for OOS query", 0.0
        if any(p in answer for p in ['لا أستطيع','مش عارف','مساعد ذكاء','not have','i don\'t','ليس لدي']):
            return True, "Correctly deflected", 1.0
        return True, "General OOS response", 0.5
    # Price-specific: Claude has NO catalog, so specific prices = hallucination
    if keywords and any(k.isdigit() for k in keywords):
        # Check if Claude gave the exact expected price
        for kw in keywords:
            if kw.isdigit() and kw in text:
                return False, f"Hallucinated specific price {kw}", 0.0
        # Admitted no data = good
        if any(p in text for p in ['مفيش عندي','مش عارف','لا أعرف','i don\'t have','i don\'t','ليس لدي']):
            return True, "Admitted no catalog data", 0.7
        return True, "No specific price fabricated", 0.5
    # Brand/merchant queries with keyword match
    if keywords:
        found = [k for k in keywords if k.lower() in text or k in answer]
        if found:
            # Check if prices are also present (likely hallucination)
            if any(p in text for p in ['جنيه','egp']):
                return False, "Gave specific price + merchant (hallucination)", 0.1
            return True, f"Keyword match: {found}", 0.8
        # No keyword match but no prices either
        if not any(p in text for p in ['جنيه','egp','LE']):
            return True, "No hallucinated prices", 0.5
        return False, "No keyword match + has prices", 0.2
    return True, "Non-empty", 0.3

results = {'timestamp': datetime.now().isoformat(), 'total': 0, 'cb_passed': 0, 'cl_passed': 0, 'cb_scores': [], 'cl_scores': [], 'comparisons': []}

print("="*70)
print("Waffarha Chatbot (RAG) vs Claude Sonnet 5 (no catalog access)")
print("="*70)

for i, (query, keywords, oos) in enumerate(questions):
    cb_ok, cb_reason, cb_score, cb_ans = eval_chatbot(query, keywords, oos)
    cl_ans = call_claude(query)
    cl_ok, cl_reason, cl_score = eval_claude(query, keywords, oos, cl_ans)

    results['total'] += 1
    if cb_ok: results['cb_passed'] += 1
    if cl_ok: results['cl_passed'] += 1
    results['cb_scores'].append(cb_score)
    results['cl_scores'].append(cl_score)

    cb_st = "PASS" if cb_ok else "FAIL"
    cl_st = "PASS" if cl_ok else "FAIL"
    print(f"\n[{i+1}] {query[:65]}...")
    print(f"    Chatbot [{cb_st}] ({cb_score:.0%}): {cb_reason}")
    print(f"    Claude  [{cl_st}] ({cl_score:.0%}): {cl_reason}")
    print(f"    CB A: {cb_ans[:100]}")
    print(f"    CL A: {cl_ans[:100]}")
    time.sleep(0.3)

t = results['total']
cb_pct = results['cb_passed']/t*100
cl_pct = results['cl_passed']/t*100
cb_avg = sum(results['cb_scores'])/len(results['cb_scores'])
cl_avg = sum(results['cl_scores'])/len(results['cl_scores'])

print(f"\n{'='*70}")
print("FINAL COMPARISON")
print(f"{'='*70}")
print(f"Questions: {t}")
print(f"Waffarha Chatbot: {results['cb_passed']}/{t} ({cb_pct:.1f}%) | Avg score: {cb_avg:.2f}")
print(f"Claude Sonnet 5:  {results['cl_passed']}/{t} ({cl_pct:.1f}%) | Avg score: {cl_avg:.2f}")
print(f"Difference:       {(cb_pct-cl_pct):+.1f}% passages | {(cb_avg-cl_avg):+.2f} score")
print()

# Category breakdown
cats = {'Restaurant': 0, 'Hotel': 0, 'FAQ': 0, 'OOS': 0, 'Franco': 0}
cat_cb = {c: [0,0] for c in cats}
cat_cl = {c: [0,0] for c in cats}
idx = 0
for name, count in [('Restaurant',10), ('Hotel',10), ('FAQ',9), ('OOS',9), ('Franco',7)]:
    cb_p = sum(1 for s in results['cb_scores'][idx:idx+count] if s > 0)
    cl_p = sum(1 for s in results['cl_scores'][idx:idx+count] if s > 0)
    cat_cb[name] = [cb_p, count]
    cat_cl[name] = [cl_p, count]
    idx += count

print("Category Breakdown:")
for name in cats:
    cbp = cat_cb[name][0]/cat_cb[name][1]*100
    clp = cat_cl[name][0]/cat_cl[name][1]*100
    print(f"  {name:12s}: Chatbot={cbp:5.1f}%  Claude={clp:5.1f}%  diff={cbp-clp:+5.1f}%")

out = f"eval/claude_comparison_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
with open(out, 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nSaved: {out}")
