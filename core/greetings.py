"""Single source of truth for the greeting/thanks/smalltalk phrase tables and
the unified text normalizer every Stage-1 router signal runs through.

Design: reports/20260919_stage1_router_design.md sections 2 + Gap 3.

Rules enforced here:
- ONE table set, ONE normalizer. Consumers (core.rag_engine, agent.engine)
  import from this module instead of keeping local copies.
- Tables are stored with their historical literal spellings (hamza variants
  included) so legacy exact-match call sites keep behaving; the normalizer
  additionally folds hamza/diacritics/case/punctuation for the router path.
- Import-light: `re` only. Never import rag_engine / planner here.
"""

import re

# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

GREETING_PHRASES = frozenset({
    # English openers
    "hi", "hello", "hey", "hey there", "hello there", "hi there", "hiya",
    "yo", "yoo", "heya", "hy", "hallo", "halo", "hallow", "helo", "helow",
    "hay", "hii", "weshakhtar", "wassup", "whats up", "what's up", "sup",
    "good morning", "good afternoon", "good evening", "good night",
    # Arabic (both hamza and non-hamza literal forms kept for legacy matching)
    "مرحبا", "مرحباً", "مرحبًا", "يا مرحب", "يا مرحبا", "اهلا", "أهلا",
    "أهلاً", "اهلا بك", "أهلا بك", "أهلاً بك", "اهلا بيك", "أهلا بيك",
    "أهلاً بيك", "هاي", "هلا", "هلا", "صباح الخير", "مساء الخير",
    "السلام عليكم", "سلام عليكم", "سلام",
    "مرحبا بك", "مرحباً بك", "مرحبًا بك", "مرحبا بيك", "مرحباً بيك",
    "مرحبًا بيك", "أهلاً بك", "أهلاً بيك", "أهلا وسهلا", "أهلاً وسهلاً",
    "اهلا وسهلا", "أهلا وسهلا بيك", "أهلا بيك يا باشا", "أهلا بيك يا معلم",
    "أهلا بيك يا بيه",
    # Dialectal greeting combos (prefix + صباح الخير core)
    "مرحبا شلونك", "بونا صباح الخير", "بوني صباح الخير", "بوني صباحو",
    # Franco / Arabizi openers
    "salam", "salamu", "salam 3aleikom", "salam 3alekom", "salam 3lykom",
    "salam 3alaykom", "3aleikom salam", "assalamu alaikum", "assalamo alaikom",
    "ahlan", "ahlan bik", "ahlan biki", "ahlan wa sahlan", "ahlan ya",
    "marhaba", "marhaban", "mar7aba", "3arramba", "sabah el kheir",
    "sabah el 5er", "masa2 el kheir", "masa el kheir",
})

THANKS_PHRASES = frozenset({
    # English
    "thanks", "thank you", "thanks!", "thanks a lot", "thank you very much",
    "thanks so much", "cheers", "appreciated", "ok thanks", "okay thanks",
    "thank u", "thanks", "thx",
    # Arabic (tanween/hamza literal variants preserved)
    "شكرا", "شكراً", "شكرًا", "شكرا لك", "شكرا ليك", "شكرا ليكم",
    "شكرا لكم", "شكراً لك", "شكراً ليك", "شكراً ليكم", "شكراً لكم",
    "شكرًا لك", "شكرًا ليك", "شكرًا ليكم", "شكرًا لكم", "شكر ليكم",
    "شكر لكم", "تسلم", "تسلملي", "متشكر", "متشكرين",
    # Franco
    "shukran",
})

SMALL_TALK_PHRASES = frozenset({
    # Arabic dialectal check-ins
    "ازيك", "ازيكي", "ايزيك", "كيفك", "كيف حضرتك", "كيف حالك", "شلونك",
    "كلمني", "كلمنى", "عامل ايه", "عاملين ايه", "اخبارك", "أخبارك",
    # Founder/Arabizi check-in spellings (fold case/punct via normalizer)
    "ezayak", "ezayek", "ezayk", "ezzayak", "ezayyek", "izayak", "azayak",
    "izayk", "izay", "kefak", "kifak", "kifik", "shlonak", "shlonik",
    "3amel eh", "3amal eh", "39amel eh", "3amelen eh",
})

# Greeting cores that may be preceded/followed by a dialectal prefix or
# suffix two-word greeting ("بونا صباح الخير", "السلام عليكم يسطا"). Matched
# at the START or END of the normalized text, never mid-question.
_GREETING_CORES = ("السلام عليكم", "سلام عليكم", "صباح الخير", "مساء الخير",
                   "اهلا وسهلا", "شلونك", "كيفك", "ازيك", "مرحبا", "مرحب",
                   "سلام", "thank you", "thanks a lot")

# Closing / farewell words. Deliberately EXCLUDES "السلام عليكم" (a greeting,
# see finding 1) and the thanks words (routed by THANKS_PHRASES, not closing).
FAREWELL_PHRASES = frozenset({
    "باي", "با bye", "مع السلامة", "وداعا", "وداعاً", "تصبح على خير",
    "خلاص", "مش محتاج", "لا داعي", "bye", "goodbye", "bay", "beyee",
    "bye bye",
})


# --------------------------------------------------------------------------- #
# Unified normalizer (Gap 3): every router signal runs through this.
# --------------------------------------------------------------------------- #

# Arabic LETTERS are \u0621-\u064A plus Arabic-Indic digits \u0660-\u0669.
# Deliberately excludes Arabic punctuation (؟ \u061F, ، \u060C) so `؟` etc.
# get bounded to whitespace the same way Latin punctuation does.
_NON_WORD_RE = re.compile(r"[^\w\s\u0621-\u064A\u0660-\u0669]")


def normalize_router_text(text: str) -> str:
    """One normalization for all router signals: lowercase, strip Arabic
    diacritics, fold hamza/alef-maksura variants, bound punctuation (Arabic
    and Latin) and collapse whitespace. Pure, deterministic, import-light.

    >>> normalize_router_text("أهلاً يا باشا !!")
    'اهلا يا باشا'
    """
    s = (text or "").strip().lower()
    # Arabic diacritics + tashkeel
    s = re.sub(r"[\u064B-\u0652\u06D6-\u06ED]", "", s)
    # Hamza / alef-maksura folding (NOT ة->ه: farewell words like
    # "مع السلامة" keep their تاء مربوطة, and table entries match verbatim)
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي")
    # Bound any non-word/non-Arabic punctuation to whitespace
    s = _NON_WORD_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# --------------------------------------------------------------------------- #
# Matchers
# --------------------------------------------------------------------------- #

def social_turn_kind(text: str):
    """Classify a pure social line. Returns one of
    ('greeting' | 'thanks' | 'smalltalk' | 'farewell') or None.

    Greeting core matching uses START/END anchoring, so"السلام عليكم يسطا"
    -> 'greeting' (never farewell, see finding 1) while a trailing suffix
    like "بونا صباح الخير" also resolves to 'greeting'."""
    cleaned = normalize_router_text(text)
    if not cleaned:
        return None
    if cleaned in GREETING_PHRASES:
        return "greeting"
    if cleaned in THANKS_PHRASES:
        return "thanks"
    if cleaned in SMALL_TALK_PHRASES:
        return "smalltalk"
    if _starts_or_ends_with_core(cleaned, _GREETING_CORES):
        return "greeting"
    if _any_substring(cleaned, FAREWELL_PHRASES):
        return "farewell"
    return None


def is_greeting_turn(text: str) -> bool:
    """True when the message is a PURE greeting/thanks/smalltalk line -- no
    offer topic signal -- so the catalog must not be touched. The caller
    (router) checks offer-topic wording first when it must not win here."""
    return social_turn_kind(text) is not None


def _starts_or_ends_with_core(cleaned: str, cores) -> bool:
    return any(cleaned.startswith(g) or cleaned.endswith(g) for g in cores)


def _any_substring(text: str, words) -> bool:
    return any(w in text for w in words)