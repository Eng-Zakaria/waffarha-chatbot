"""
Core enhancements implementing the 5 Perfection Pillars for Waffarha RAG Chatbot:
1. Robust Zero-Shot Intent Router
2. Advanced Superlative & Multi-Merchant Handling
3. Pre-retrieval Out-of-Scope / Hallucination Guardrails
4. Robust Franco-Arabic (Arabizi) Normalization
5. Multi-Turn Coreference & Memory Pinning
"""

import re
import logging
from typing import List, Tuple, Dict, Any, Optional

log = logging.getLogger("waffarha-app")

# ==============================================================================
# PILLAR 4: Robust Franco-Arabic (Arabizi) Normalization Layer
# ==============================================================================
# Build a translation table: Latin/Arabizi chars -> Arabic chars
# Process all single-char replacements in one pass to avoid conflicts.
_TRANS_TABLE = str.maketrans({
    # Digits
    '0': '٠', '1': '١', '2': '٢', '3': 'ع', '4': 'ذ',
    '5': 'خ', '6': 'ط', '7': 'ح', '8': 'غ', '9': 'ص',
    # Common letter substitutions
    'a': 'ا', 'e': 'a', 'i': 'ي', 'o': 'و', 'u': 'u',
    'k': 'ك', 'q': 'ق', 'w': 'و', 's': 'س',
    'y': 'ي', 'z': 'ز', 'r': 'ر', 'd': 'د',
    'b': 'ب', 't': 'ط', 'n': 'ن', 'm': 'م',
    'h': 'ح', 'j': 'ج', 'g': 'غ', 'f': 'ف',
    # Multi-char patterns (applied separately after single-char pass)
})

_MULTI_CHAR_PATTERNS = [
    ('sh', 'ش'),
    ('th', 'ث'),
    ('dh', 'ذ'),
    ('gh', 'غ'),
    ('kh', 'خ'),
    ('ch', 'چ'),
    ('ff', 'ف'),
    ('ll', 'ل'),
    ('nn', 'ن'),
    ('ss', 'س'),
    ('tt', 'ط'),
    ('bb', 'ب'),
    ('mm', 'م'),
    ('hh', 'ح'),
    ('jj', 'ج'),
    ('dd', 'د'),
    ('rr', 'ر'),
    ('ww', 'و'),
    ('zz', 'ز'),
    ('aa', 'ا'),
    ('oo', 'و'),
]


def _apply_multi_char(text: str) -> str:
    """Apply multi-character Arabizi patterns (longest first)."""
    for pat, ar in _MULTI_CHAR_PATTERNS:
        text = text.replace(pat, ar)
    return text


def normalize_arabizi_and_arabic(text: str) -> str:
    """
    Normalizes both Arabizi (Latin-script Arabic with numbers) and standard Arabic
    variants (Hamza forms, Alef Maksura, diacritics).
    Example: '3ayez a3raf kam offer el KFC?' -> '3ayez عاييز a3raf اعرف kam offer el KFC?'
    Keeps original Latin words alongside transliterated Arabic versions for hybrid BM25/Dense matching.
    """
    if not text:
        return ""

    # 1. Standard Arabic unicode normalization (Hamza, Alef Maksura)
    norm = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "a").replace("ى", "ي")
    norm = re.sub(r"[ً-ْٰـ]", "", norm)  # Strip diacritics

    # 2. Convert Arabizi to Arabic (keep original for BM25 matching)
    words = norm.split()
    converted_words = []
    for w in words:
        # Check if word contains Arabizi characters
        has_arabizi = any(chr(c) in w for c in _TRANS_TABLE) or \
                      any(pattern in w for pattern, _ in _MULTI_CHAR_PATTERNS)
        if has_arabizi:
            # Apply single-char translation table first, then multi-char patterns
            translated = w.translate(_TRANS_TABLE)
            translated = _apply_multi_char(translated)
            converted_words.append(w)       # keep original Latin for exact match
            converted_words.append(translated)  # keep Arabic-transliterated
        else:
            converted_words.append(w)

    return " ".join(converted_words)


# ==============================================================================
# PILLAR 3: Strict Pre-retrieval Out-of-Scope & Hallucination Guardrails
# ==============================================================================
_OUT_OF_SCOPE_PHRASES = [
    r"weather|temperature|forecast|rain|sunny|cloudy|الطقس|الحرارة|توقعات|مطر|الجو",
    r"headache|medicine|pill|treatment|symptom|doctor|pain|صداع|دواء|علاج|ألم",
    r"joke|funny|نكتة|اضحك|نكت",
    r"president|prime minister|capital|عاصمة|رئيس",
    r"python|programming|برمجة|كود برنامج|كتابة كود|اكتب كود|كود بايثون",
    r"better than groupon|better than cobone|أفضل من جروبات",
]

_REFUSAL_RESPONSES = {
    "en": "I am the Waffarha customer support assistant. I can only help you with Waffarha offers, orders, cashback, bills, and account FAQs. How can I assist you with those today?",
    "ar": "أنا مساعد خدمة عملاء وفرها. أقدر أساعدك فقط في عروض وفرها، الطلبات، الكاش باك، الفواتير، والأسئلة الشائعة للحساب. تحب أساعدك في إيه النهاردة؟"
}


def check_out_of_scope_guardrail(query: str, lang: str = "ar") -> Optional[str]:
    """
    Instantly returns a polite deflection message if the query is outside Waffarha's domain,
    preventing embedding noise from matching unrelated offers.
    """
    q_lower = (query or "").lower()
    for pattern in _OUT_OF_SCOPE_PHRASES:
        if re.search(pattern, q_lower, re.IGNORECASE):
            return _REFUSAL_RESPONSES.get(lang, _REFUSAL_RESPONSES["ar"])
    return None


# ==============================================================================
# PILLAR 1: Semantic Intent & Query Type Router
# ==============================================================================
INTENT_OFFER_LOOKUP = "OFFER_LOOKUP"
INTENT_FAQ_INQUIRY = "FAQ_INQUIRY"
INTENT_PERSONAL_ACCOUNT = "PERSONAL_ACCOUNT"
INTENT_GREETING = "GREETING"
INTENT_OUT_OF_SCOPE = "OUT_OF_SCOPE"
INTENT_PROMPT_INJECTION = "PROMPT_INJECTION"
INTENT_SUPERLATIVE = "SUPERLATIVE"
INTENT_MULTI_MERCHANT = "MULTI_MERCHANT"


def classify_intent_robust(query: str, client: Any = None, llm_model: str = "qwen2.5:3b-instruct") -> str:
    """
    Robust intent router combining fast rule heuristics with zero-shot LLM classification
    to eliminate category misroutings (e.g. discount/price words triggering false offer routes on FAQs).
    """
    q = (query or "").strip()
    if not q:
        return INTENT_OUT_OF_SCOPE

    # Fast deterministic guards
    guardrail_response = check_out_of_scope_guardrail(q)
    if guardrail_response:
        return INTENT_OUT_OF_SCOPE

    q_lower = q.lower()

    # Check greetings
    greetings_lower = [g.lower() for g in ["hi", "hello", "hey", "مرحبا", "اهلا", "السلام عليكم", "صباح الخير", "مساء الخير", "شكرا"]]
    is_greeting = q_lower in greetings_lower or len(q.split()) <= 2 and any(g in q_lower for g in greetings_lower)
    if is_greeting:
        return INTENT_GREETING

    # Check Superlatives
    if any(w in q_lower for w in ["cheapest", "lowest", "ارخص", "أرخص", "أغلى", "اغلى", "أعلى خصم", "اعلى خصم"]):
        return INTENT_SUPERLATIVE

    # Check Multi-Merchant
    if any(w in q_lower for w in ["compare", "vs", "versus", "قارن", "الفرق بين", "أيهما", "ايهما"]) or q_lower.count(" و ") >= 2:
        return INTENT_MULTI_MERCHANT

    # If client and llm_model provided, use zero-shot prompt classification for ambiguous queries
    if client is not None:
        try:
            resp = client.chat(
                model=llm_model,
                messages=[
                    {"role": "system", "content": (
                        "Classify the user query into exactly one intent category:\n"
                        "- OFFER_LOOKUP: finding deals, discounts, prices, or merchants.\n"
                        "- FAQ_INQUIRY: how to use the app, payment methods, refund policy, registration, bills.\n"
                        "- PERSONAL_ACCOUNT: user's personal orders, wallet, coupons, or spending.\n"
                        "- GREETING: casual greeting or thank you.\n"
                        "- OUT_OF_SCOPE: general knowledge, weather, medical, jokes, competitor comparisons.\n"
                        "Reply with ONLY the exact category name. Nothing else."
                    )},
                    {"role": "user", "content": f"Query: {q}"}
                ],
                stream=False,
                options={"num_predict": 10, "temperature": 0.0}
            )
            intent = (resp.get("message", {}).get("content") or "").strip().upper()
            if intent in {INTENT_OFFER_LOOKUP, INTENT_FAQ_INQUIRY, INTENT_PERSONAL_ACCOUNT, INTENT_GREETING, INTENT_OUT_OF_SCOPE}:
                return intent
        except Exception as e:
            log.warning(f"Zero-shot intent classification failed: {e}, falling back to heuristics.")

    # Fallback heuristic rules (normalized: hamza/ya/ta-marbuta folded so
    # one-letter spelling variants can't flip the verdict by themselves)
    nq = _fold_keyword_text(q_lower)
    is_faq = any(w in nq for w in _FAQ_HINT_WORDS)
    is_offer = any(w in nq for w in _OFFER_HINT_WORDS)

    # If query is primarily about HOW TO do something (faq), route there even if it mentions offers/coupons
    if is_faq and not is_offer:
        return INTENT_FAQ_INQUIRY
    # If query contains faq keywords but also offer keywords, check if the primary intent is learning how
    if is_faq and is_offer:
        return INTENT_FAQ_INQUIRY
    return INTENT_OFFER_LOOKUP


# ==============================================================================
# FAQ-vs-OFFER understanding judge (meaning, not keywords)
# ==============================================================================
# Keyword lists alone can never "understand" a question: Egyptian-dialect
# spelling variants (سياسة/سياسه، ازاي/ازاى، طريقة/طريقه) and generic words
# (coupon, عايز, ازاي) flip a pure keyword verdict with a single letter.
# So keywords are only the FAST PATH for clear cases; anything mixed or
# signal-less goes to a tiny LLM judge that reads MEANING (with few-shot
# Egyptian examples), cached per normalized query so repeats are free and a
# failure/timeout falls back to the keyword verdict instead of failing.

# Matched against _fold_keyword_text() output, so each entry needs only ONE
# spelling -- variants fold to the same form before matching.
_FAQ_HINT_WORDS = [
    "how do i", "how to", "what is", "what are", "how does",
    "payment", "refund", "bill", "register", "login", "cashback",
    "policy", "policies", "status", "account",
    "كيف", "طريقه", "دفع", "استرداد", "استرجاع", "تسجيل", "حساب",
    "فاتوره", "ازاي", "سياسه", "كاش باك", "كاشباك", "يعني ايه",
    "حاله", "مستعمل", "استخدام", "معني", "شراء", "اشتري",
    "فودافون", "فوري", "تقسيط", "محفظه",
]
_OFFER_HINT_WORDS = [
    "offer", "deal", "discount", "price", "cheapest", "compare",
    "عرض", "عروض", "خصم", "سعر", "بكام", "كام", "ارخص", "قارن",
    "مطعم", "شاورما", "بيتزا", "برجر",
]


def _fold_keyword_text(text: str) -> str:
    """Fold Arabic spelling variants for keyword matching: hamza forms -> ا,
    ى -> ي, ة -> ه, diacritics stripped, lowercase. Applied to BOTH the query
    and (implicitly, via single-spelling lists) the keywords, so سياسة/سياسه,
    ازاي/ازاى, طريقة/طريقه all match the same entry."""
    s = (text or "").lower()
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"[ً-ْٰـ]", "", s)
    return s


_JUDGE_SYSTEM_PROMPT = (
    "You classify customer messages for the Waffarha deals assistant. "
    "Reply with EXACTLY one word: FAQ, OFFER, or MIXED.\n"
    "- FAQ: the customer asks HOW to do something or about policies: buying steps, "
    "paying, using or refunding coupons, order status meaning, cashback, account, bills.\n"
    "- OFFER: the customer wants to FIND, SHOW, or COMPARE deals, merchants, products, or prices.\n"
    "- MIXED: asks for both at once.\n"
    "Decide by MEANING, ignoring spelling mistakes and Egyptian dialect variants.\n"
    "Examples:\n"
    "'ازاي اشتري من وفرها؟' -> FAQ\n"
    "'عايز عرض شاورما' -> OFFER\n"
    "'ايه سياسه الاسترجاع؟' -> FAQ\n"
    "'What payment method are available?' -> FAQ\n"
    "'compare kfc and pizza hut' -> OFFER\n"
    "'عايز اعرف طريقة الدفع' -> FAQ\n"
    "'what is cheapest offer?' -> OFFER\n"
    "'يعني ايه حالة مستعمل؟' -> FAQ\n"
    "Reply with only the word."
)

_JUDGE_CACHE: dict = {}
_JUDGE_CACHE_SIZE = 500


def _judge_cache_key(query: str) -> str:
    return _fold_keyword_text(query).strip()


def judge_faq_vs_offer(query: str, client: Any = None,
                       llm_model: str = "qwen2.5:3b-instruct",
                       timeout: int = 12) -> str:
    """Return 'faq', 'offer', or 'mixed' by MEANING.

    Clear keyword cases answer instantly; mixed/signal-less queries go to the
    small LLM judge (cached). Any failure falls back to the keyword verdict --
    the judge can only ever upgrade understanding, never break retrieval.
    """
    q = (query or "").strip()
    if not q:
        return "mixed"
    key = _judge_cache_key(q)
    if key in _JUDGE_CACHE:
        return _JUDGE_CACHE[key]

    nq = _fold_keyword_text(q.lower())
    is_faq = any(w in nq for w in _FAQ_HINT_WORDS)
    is_offer = any(w in nq for w in _OFFER_HINT_WORDS)
    fast = "faq" if (is_faq and not is_offer) else ("offer" if (is_offer and not is_faq) else "mixed")

    verdict = fast
    if fast == "mixed" and client is not None:
        try:
            resp = client.chat(
                model=llm_model,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Query: {q}"},
                ],
                stream=False,
                options={"num_predict": 8, "temperature": 0.0},
            )
            raw = (resp.get("message", {}).get("content") or "").strip().upper().split()
            word = raw[0] if raw else ""
            if word in ("FAQ", "OFFER", "MIXED"):
                verdict = word.lower()
        except Exception as e:  # noqa: BLE001 -- judge is advisory; fall back silently
            log.warning(f"FAQ/OFFER judge failed, keeping keyword verdict: {e}")

    _JUDGE_CACHE[key] = verdict
    while len(_JUDGE_CACHE) > _JUDGE_CACHE_SIZE:
        _JUDGE_CACHE.pop(next(iter(_JUDGE_CACHE)))
    return verdict
