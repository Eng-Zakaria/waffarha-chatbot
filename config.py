import os
from dotenv import load_dotenv

load_dotenv()
# CHANGED: was hardcoded "cuda", which crashes on any machine without an
# NVIDIA GPU (most local/dev setups). Defaults to CPU now; set
# EMBEDDING_DEVICE=cuda in your .env if you do have a GPU available.
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

OFFERS_API_URL = "https://api-test.waffarha.tech/api/sectionOffers"

OFFERS_API_BASE_BODY = {
    "security_key": os.getenv("WAFFARHA_SECURITY_KEY", "4be8e2a72ca744d2da36782adec01cd9"),
    "app_version": "9.1.06",
    "platform": "website",
    "device_token": "6B0D864C-865B-410D-B1BE-E9A43507762F",
    "brand": "Apple",
    "model": "iPhone16,1",
    "store": "AppStore",
    "ip": "196.202.14.131",
}

CATEGORY_IDS = [1, 5, 6, 7, 8, 9, 11, 12, 15, 17, 18, 20, 10, 21, 22, 24, 25, 146, 147, 148]

LANGS = ["en", "ar"]

PAGE_LIMIT = 50          
MAX_PAGES_PER_SECTION = 50  


REQUEST_TIMEOUT = (10, 45)
MAX_RETRIES = 3
RETRY_BACKOFF = 2  

SLEEP_BETWEEN_REQUESTS = 0.15  

OFFER_FIELD_CANDIDATES = {
    "id": ["offer_id", "id"],
    "title": ["mobile_offer_title_en", "mobile_offer_title_ar", "offer_name", "title", "name"],
    "description": ["offer_brief", "description", "desc"],
    "price": ["actual_value", "price"],
    "old_price": ["offer_value", "old_price", "original_price"],
    "discount": ["offer_discount", "discount"],
    "expiry": ["offer_expire_date", "end_date", "expiry"],
}

# CHANGED: was a single string "EGP" used verbatim in every reply, including
# Arabic ones -- e.g. "كانت 300 EGP", English inside an Arabic sentence. Now
# keyed by reply language so Arabic replies show جنيه instead. English replies
# are unaffected (config.CURRENCY["en"] == the old "EGP" value).
CURRENCY = {"en": "EGP", "ar": "جنيه"}

OFFER_STATUS_FIELD = "offer_status"
OFFER_ACTIVE_VALUES = ["active"]

OFFERS_LIST_CANDIDATES = ["data", "result", "offers", "items", "sectionOffers"]

EMBEDDING_MODEL = "intfloat/multilingual-e5-base"

TOP_K = 3
CANDIDATE_K = 15 
LEXICAL_BONUS_WEIGHT = 0.4 

# NEW: used instead of TOP_K / CANDIDATE_K when a query is detected as
# "multi-offer" -- either comparison-phrased ("compare X and Y") or naming
# 2+ known merchants by name. A single-offer TOP_K=3 / CANDIDATE_K=15 is
# often too tight to guarantee both named offers survive dedup + ranking,
# especially if one merchant scores lower than unrelated but closer-matching
# candidates. See rag_engine._detect_multi_item.
TOP_K_MULTI = 6
CANDIDATE_K_MULTI = 30


MIN_RELEVANCE_SCORE = 0.35  



FAQ_DIRECT_ANSWER_SCORE = 0.75
# CHANGED: was 0.15. The eval run showed top-1 scores consistently well above
# FAQ/OFFER_DIRECT_ANSWER_SCORE, but the gap to the second-best candidate was
# almost always under the old 0.15 margin -- because the corpus legitimately
# has many similar items (multiple iftar deals, multiple pizza places), so a
# close top-2 gap is normal, not a sign the top match is wrong. Lowered to
# 0.06 so genuinely ambiguous ties (two near-identical top scores) still fall
# through to the LLM, but "close because the corpus is dense" no longer does.
FAQ_DIRECT_ANSWER_MARGIN = 0.06
# NEW: if the top score is this high, skip the margin check entirely --
# a near-perfect match is a near-perfect match regardless of what else is
# nearby in the corpus.
FAQ_DIRECT_ANSWER_HIGH_CONFIDENCE = 0.90
# NEW: the conservative margin for same-merchant/same-category ties, where a
# wrong pick is a much worse failure than falling through to the LLM. This
# is the ORIGINAL 0.15 default, kept for exactly the case it was protecting
# (a follow-up eval run showed the lenient margin+bypass above picking the
# WRONG one of two same-merchant offers, or the wrong FAQ in the same topic
# cluster, when applied uniformly -- see rag_engine._same_entity_family).
FAQ_DIRECT_ANSWER_SAME_ENTITY_MARGIN = 0.15


OFFER_DIRECT_ANSWER_SCORE = 0.75
OFFER_DIRECT_ANSWER_MARGIN = 0.06  # CHANGED: was 0.15, see comment above
OFFER_DIRECT_ANSWER_HIGH_CONFIDENCE = 0.90  # NEW -- different-merchant case only, see _same_entity_family
OFFER_DIRECT_ANSWER_SAME_ENTITY_MARGIN = 0.15  # NEW -- same-merchant ties, see FAQ comment above

# NEW: the eval run showed a broad, no-merchant-named query ("hotel with
# breakfast offer") get shortcut-answered as one specific hotel (the WRONG
# one -- a different, correct hotel was sitting right behind it in the
# candidate list) even though the two hotels are different merchants, so
# _same_entity_family's protection never applied. The common thread: the
# top candidate had lexical_hits == 0 -- the query didn't literally mention
# that merchant, dish, or any identifying detail, so its high combined_score
# came entirely from embedding proximity to a broad category/concept. A
# high score against a broad category doesn't mean "this exact merchant is
# what the user meant" the way a high score DOES when the query names
# something specific -- several category-mates can legitimately score
# close together. Require the same conservative margin used for same-
# merchant ties whenever the top pick has zero literal grounding in the
# query, regardless of whether the runner-up is the same merchant or not.
FAQ_DIRECT_ANSWER_NO_GROUNDING_MARGIN = 0.15
OFFER_DIRECT_ANSWER_NO_GROUNDING_MARGIN = 0.15


INDEX_DIR = os.path.join(os.path.dirname(__file__), "data")
FAISS_INDEX_PATH = os.path.join(INDEX_DIR, "index.faiss")
DOCS_PATH = os.path.join(INDEX_DIR, "docs.pkl")


OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct")
MAX_TOKENS = 500         
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "1536"))  

HISTORY_TURNS_KEPT = 3