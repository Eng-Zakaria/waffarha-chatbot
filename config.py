import os
from dotenv import load_dotenv
import clickhouse_connect

load_dotenv()
# CHANGED: was hardcoded "cuda", which crashes on any machine without an
# NVIDIA GPU (most local/dev setups). Defaults to CPU now; set
# EMBEDDING_DEVICE=cuda in your .env if you do have a GPU available.
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

OFFERS_API_URL = "https://api-test.waffarha.tech/api/sectionOffers"

_security_key = os.getenv("WAFFARHA_SECURITY_KEY")
def get_security_key() -> str:
    if not _security_key:
        raise RuntimeError(
            "WAFFARHA_SECURITY_KEY is not set. "
            "This key is required to fetch fresh offers from the Waffarha API. "
            "If you are only running the chatbot with a prebuilt index, you can ignore this error "
            "by not running fetch_offers.py."
        )
    return _security_key

OFFERS_API_BASE_BODY = {
    "security_key": _security_key,
    "app_version": "9.1.06",
    "platform": "website",
    "device_token": "6B0D864C-865B-410D-B1BE-E9A43507762F",
    "brand": "Apple",
    "model": "iPhone16,1",
    "store": "AppStore",
    "ip": "196.202.14.131",
}

CATEGORY_IDS = [1, 5, 6, 7, 8, 9, 11, 12, 15, 17, 18, 20, 10, 21, 22, 24, 25, 146, 147, 148]

# NEW: connection settings for ingest/fetch_offers_clickhouse.py, an
# alternative offer source to fetch_offers.py's mobile-API scrape. Same
# lazy-error pattern as get_security_key() above -- only raises if something
# actually tries to connect, so importing config.py (e.g. from rag_engine.py,
# which never touches ClickHouse) never requires these to be set.
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))  # clickhouse-connect default HTTP port
# CHANGED: matches the actual .env var name in use (CLICKHOUSE_USERNAME),
# not the CLICKHOUSE_USER this originally assumed.
CLICKHOUSE_USERNAME = os.getenv("CLICKHOUSE_USERNAME", "default")
CLICKHOUSE_DATABASE = os.getenv("CLICKHOUSE_DATABASE", "main")
# CHANGED: was a flat "false" default. Port 443 is the standard HTTPS port
# for ClickHouse's HTTP interface (e.g. clickhouse-test.waffarha.tech runs
# on 443) -- defaulting secure=False against a 443 host would just fail the
# handshake. Still fully overridable via CLICKHOUSE_SECURE if you ever point
# this at a plain-HTTP host on port 443 for some reason.
CLICKHOUSE_SECURE = os.getenv("CLICKHOUSE_SECURE", "true" if CLICKHOUSE_PORT == 443 else "false").lower() == "true"
_clickhouse_password = os.getenv("CLICKHOUSE_PASSWORD")


def get_clickhouse_client():
    """Returns a connected clickhouse_connect client. Deferred import (like
    get_security_key()'s deferred requirement) so nothing else in this repo
    needs the clickhouse-connect package installed to import config.py.

    IMPORTANT: this should be a READ-ONLY database user. fetch_offers_clickhouse.py
    only issues SELECTs, but there is no code-level enforcement of that --
    the enforcement belongs at the DB-user/grant level, same as any other
    service account with query access to production data.
    """

    if not _clickhouse_password and os.getenv("CLICKHOUSE_REQUIRE_PASSWORD", "true").lower() == "true":
        raise RuntimeError(
            "CLICKHOUSE_PASSWORD is not set. Set it in .env, or set "
            "CLICKHOUSE_REQUIRE_PASSWORD=false explicitly if your ClickHouse "
            "user genuinely has no password (e.g. local dev)."
        )
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USERNAME,
        password=_clickhouse_password or "",
        database=CLICKHOUSE_DATABASE,
        secure=CLICKHOUSE_SECURE,
    )

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

# NEW: intent classification (_classify_intent) used to be a HARD filter in
# retrieve() -- any doc whose source didn't match the classified intent was
# excluded from candidates entirely, before scoring. That's what was
# silently dropping real FAQ answers: _OFFER_INTENT_WORDS contains generic
# words like "coupon"/"discount"/"price" that show up constantly in FAQ
# questions too ("How do I use my purchased coupon?" -> classified "offer"
# -> every FAQ doc excluded, faq_4 never had a chance to score). Now used as
# a soft additive bonus instead -- nudges ranking toward the classified
# source without ever making the other source unreachable. Kept smaller
# than LEXICAL_BONUS_WEIGHT since intent classification is a much cruder
# signal (keyword-list guess) than an actual literal word match.
INTENT_BONUS_WEIGHT = 0.15

# NEW: used instead of TOP_K / CANDIDATE_K when a query is detected as
# "multi-offer" -- either comparison-phrased ("compare X and Y") or naming
# 2+ known merchants by name. A single-offer TOP_K=3 / CANDIDATE_K=15 is
# often too tight to guarantee both named offers survive dedup + ranking,
# especially if one merchant scores lower than unrelated but closer-matching
# candidates. See rag_engine._detect_multi_item.
TOP_K_MULTI = 6
CANDIDATE_K_MULTI = 30


MIN_RELEVANCE_SCORE = 0.35

# NEW: hybrid BM25 + embedding retrieval switches.
# Turn hybrid retrieval on and tune how much lexical BM25 evidence should
# contribute versus the existing lexical overlap bonus.
ENABLE_HYBRID_RETRIEVAL = os.getenv("ENABLE_HYBRID_RETRIEVAL", "true").lower() == "true"
BM25_WEIGHT = float(os.getenv("BM25_WEIGHT", "0.35"))
RRF_K = int(os.getenv("RRF_K", "60"))

# NEW: minimum similarity ratio (difflib SequenceMatcher, 0-1) for a
# capitalized brand-like token in the query to count as "close enough" to a
# known merchant name. This is deliberately strict -- it exists to catch
# the offer_hallucination_check failure mode (a query naming a merchant
# that isn't in the catalog at all, e.g. "Starbucks Egypt", still scoring
# 0.95+ on embedding similarity against some unrelated real offer and
# getting answered as if it were real). Raise this if legitimate merchant
# names with minor spelling variants start getting incorrectly rejected;
# lower it (cautiously) if real merchants are getting flagged as unknown.
MERCHANT_FUZZY_MATCH_CUTOFF = 0.8

# NEW: known abbreviation/alternate-name overrides for merchants whose
# common short form doesn't fuzzy-match their catalog name well (e.g. an
# acronym vs. the full brand name). Add entries here as they're found in
# real traffic instead of relying purely on fuzzy string similarity -- this
# is the safer fix for the mixed_language_query_kfc failure mode (a Latin-
# script brand abbreviation embedded in an Arabic sentence matching the
# wrong merchant) since a wrong fuzzy guess is worse than no guess. Keys
# are lowercase; values must match a real merchant string from the index.
# Populate from your actual merchant list -- left empty here since this repo
# snapshot doesn't include the merchant catalog.
MERCHANT_ALIASES = {
    "kfc": "KFC",
    "كنتاكي": "دجاج كنتاكي",
    "كنتاكي فرايد تشيكن": "دجاج كنتاكي",
    "kentucky fried chicken": "KFC",
    "asian wok": "Asian Wok",
    "اسيان ووك": "Asian Wok",
}



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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b-instruct")
MAX_TOKENS = 500         
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "1536"))  

HISTORY_TURNS_KEPT = 3

# NEW: fallback for follow-up phrasings the hardcoded word/phrase lists in
# rag_engine.py (_ANAPHORA_WORDS, _FOLLOWUP_SIGNAL_PHRASES, ...) don't
# recognize -- e.g. the "بكام" gap. Rather than only growing those lists
# forever as new dialectal phrasings turn up in production, a short,
# low-content query that the rule-based checks say is NOT a follow-up gets
# one cheap classification call to the LLM ("is this about the same offer
# I just showed, or something new?") before being treated as fresh. Set
# to False to disable entirely and rely only on the rule-based lists.
FOLLOWUP_LLM_FALLBACK_ENABLED = os.getenv("FOLLOWUP_LLM_FALLBACK_ENABLED", "true").lower() == "true"

# NEW: the LLM fallback above only fires for SHORT queries with this many
# or fewer non-stopword words -- e.g. "بكام" (1 word) or "لسه شغال ولا لأ"
# (a few words) qualify, but "do you have any pizza offers under 200 EGP"
# (clearly self-contained, unambiguous, and not vague) does not, so the
# fallback isn't spent on queries the rule-based checks were already right
# to call "fresh". Keep this low -- it's a filter for AMBIGUOUS short
# questions, not a general follow-up detector.
FOLLOWUP_LLM_FALLBACK_MAX_CONTENT_WORDS = 4

# NEW: caps how many /api/chat requests this process will have actively
# generating with Ollama at once (see app.py's _generation_semaphore for
# why). Should match (or sit at/under) Ollama's own OLLAMA_NUM_PARALLEL --
# set higher than Ollama's real parallelism and requests just queue
# invisibly inside Ollama instead of here, defeating the point. A request
# that can't get a slot within GENERATION_QUEUE_TIMEOUT seconds gets a fast
# 503 + Retry-After instead of hanging.
MAX_CONCURRENT_GENERATIONS = int(os.getenv("MAX_CONCURRENT_GENERATIONS", "4"))
GENERATION_QUEUE_TIMEOUT = float(os.getenv("GENERATION_QUEUE_TIMEOUT", "30"))

# NEW: backs memory.py's session memory (which offers/FAQs were actually
# shown to each session_id -- see memory.py for why this exists). Defaults
# to a local Redis for non-Docker dev (`redis-server` or
# `docker run -p 6379:6379 redis:alpine`); docker-compose.yml overrides
# this to the `redis` service's in-network address.
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# NEW: which backend memory.MemoryStore uses to store session memory.
#   "redis" (default): shared across workers/replicas, survives restarts.
#     Requires a reachable Redis server (see REDIS_URL above).
#   "local": plain in-process dict, no server involved at all. Use this to
#     run/test the app without installing or starting Redis -- set
#     MEMORY_BACKEND=local in your .env. NOT shared across workers/replicas
#     and NOT persisted across restarts, so it's single-process/dev-only;
#     production (multi-worker/replica) should stay on "redis".
MEMORY_BACKEND = os.getenv("MEMORY_BACKEND", "redis").lower()

# NEW: per-user ("my coupons / my orders") queries. These hit ClickHouse
# live (fct_coupons) rather than the static RAG index, and are gated behind
# identity resolution -- see identity.py. Off by default until a real auth
# backend exists.
PERSONAL_QUERIES_ENABLED = os.getenv("PERSONAL_QUERIES_ENABLED", "true").lower() == "true"
IDENTITY_BACKEND = os.getenv("IDENTITY_BACKEND", "static").lower()
STATIC_TEST_USER_ID = int(os.getenv("STATIC_TEST_USER_ID", "0") or "0")

# NEW: live catalog queries (offers, merchants, prices, locations, tags).
# These hit ClickHouse live (dim_offers, dim_partners, dim_type_price)
# instead of the static FAISS index. Enabled by default since no auth
# is required for public catalog data.
CATALOG_QUERIES_ENABLED = os.getenv("CATALOG_QUERIES_ENABLED", "true").lower() == "true"

# NEW: identity backend configuration
# IDENTITY_BACKEND=header: reads user_id from HTTP header (set by auth proxy/gateway)
IDENTITY_HEADER = os.getenv("IDENTITY_HEADER", "X-User-ID")
# IDENTITY_BACKEND=session: reads user_id from Redis session store
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "session_id")
