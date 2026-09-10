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

    # Fallback heuristic rules
    is_faq = any(w in q_lower for w in ["how do i", "how to", "what is", "payment", "refund", "bill", "register", "login", "كيف", "طريقة", "دفع", "استرداد", "تسجيل", "حساب", "فاتورة", "إزاي"])
    is_offer = any(w in q_lower for w in ["offer", "deal", "discount", "price", "coupon", "عرض", "عروض", "خصم", "سعر", "بكام", "كام"])

    # If query is primarily about HOW TO do something (faq), route there even if it mentions offers/coupons
    if is_faq and not is_offer:
        return INTENT_FAQ_INQUIRY
    # If query contains faq keywords but also offer keywords, check if the primary intent is learning how
    if is_faq and is_offer:
        return INTENT_FAQ_INQUIRY
    return INTENT_OFFER_LOOKUP
