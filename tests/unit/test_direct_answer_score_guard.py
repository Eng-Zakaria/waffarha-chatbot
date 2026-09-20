"""Unit tests for the Phase-3 score-inflation accounting guard on direct-answer
shortcuts (config.DIRECT_ANSWER_MIN_EMBEDDING_SCORE).

No RagEngine index load -- tests the methods on a __new__ instance with a
synthetic retrieved list, exercising only the embedding-floor + combined-score
threshold logic without hitting Qdrant or an embedding model.
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import core.config as config
from core.rag_engine import RagEngine


def _faq_candidate(*, embedding_score: float, combined_score: float,
                   faq_id: str = "faq1", lang: str = "en") -> dict:
    return {
        "score": embedding_score,
        "combined_score": combined_score,
        "embedding_score": embedding_score,
        "bonus_total": max(0.0, combined_score - embedding_score),
        "lexical_hits": 1,
        "metadata": {
            "source": "faq",
            "id": faq_id,
            "lang": lang,
            "question": "Test question?",
            "answer": "Test answer.",
        },
        "text": "Test question? Test answer.",
    }


def _offer_candidate(*, embedding_score: float, combined_score: float,
                     oid: str = "o1", lang: str = "en",
                     merchant: str = "Acme", price: float = 100) -> dict:
    return {
        "score": embedding_score,
        "combined_score": combined_score,
        "embedding_score": embedding_score,
        "bonus_total": max(0.0, combined_score - embedding_score),
        "lexical_hits": 1,
        "metadata": {
            "source": "offer",
            "id": oid,
            "lang": lang,
            "merchant": merchant,
            "title": "Great deal",
            "section_id": 1,
            "price": price,
            "old_price": None,
            "discount": None,
            "expiry": "2099-01-01",
            "url": "https://x.test/",
        },
        "text": f"{merchant} Great deal",
    }


def _mk_engine():
    eng = RagEngine.__new__(RagEngine)
    eng.docs = []   # sibling lookup not needed for these cases
    return eng


# ---------- FAQ direct-answer guard ----------

class TestFaqDirectAnswerEmbeddingFloor:
    def test_blocked_when_embedding_below_floor(self):
        """High combined_score but embedding below DIRECT_ANSWER_MIN_EMBEDDING_SCORE
        must NOT fire FAQ direct answer -- the confidence came from bonuses alone."""
        retrieved = [_faq_candidate(embedding_score=0.40, combined_score=0.95)]
        result = _mk_engine()._get_faq_direct_answer(retrieved, "en", "query",
                                                     multi_item=False)
        assert result is None

    def test_allowed_when_both_above_thresholds(self):
        """Both embedding floor AND combined threshold cleared -> answer returned."""
        retrieved = [_faq_candidate(embedding_score=0.60, combined_score=0.90)]
        result = _mk_engine()._get_faq_direct_answer(retrieved, "en", "query",
                                                     multi_item=False)
        assert result == "Test answer."

    def test_allows_when_combined_low_but_embedding_high(self):
        """Embedding is high enough but combined below the 0.85 threshold
        must still return None -- the floor gate is necessary but not
        sufficient; both must pass."""
        retrieved = [_faq_candidate(embedding_score=0.65, combined_score=0.70)]
        result = _mk_engine()._get_faq_direct_answer(retrieved, "en", "query",
                                                     multi_item=False)
        assert result is None


# ---------- Offer direct-answer guard (disabled in answer_stream, but
# method still exists and must be guarded) ----------

class TestOfferDirectAnswerEmbeddingFloor:
    def test_blocked_when_embedding_below_floor(self):
        """Massive combined inflation (embedding 0.35 -> combined 1.20) blocked."""
        retrieved = [_offer_candidate(embedding_score=0.35, combined_score=1.20)]
        result = _mk_engine()._get_offer_direct_answer(retrieved, "en", "query",
                                                       multi_item=False)
        assert result is None

    def test_allowed_when_above_floor(self):
        """Genuine match (embedding 0.60, combined 1.00) allowed."""
        retrieved = [_offer_candidate(embedding_score=0.60, combined_score=1.00)]
        result = _mk_engine()._get_offer_direct_answer(retrieved, "en", "query",
                                                       multi_item=False)
        assert result is not None  # returns the formatted offer card line


# ---------- Stock direct-answer guard ----------

class TestStockDirectAnswerEmbeddingFloor:
    def test_blocked_when_embedding_below_floor(self):
        retrieved = [_offer_candidate(embedding_score=0.40, combined_score=1.10)]
        result = _mk_engine()._get_stock_direct_answer(retrieved, "en",
                                                       "is this sold out")
        assert result is None

    def test_allowed_when_above_floor(self):
        """Uses "sold out" stock wording + high embedding + high combined."""
        retrieved = [_offer_candidate(embedding_score=0.65, combined_score=1.10)]
        result = _mk_engine()._get_stock_direct_answer(retrieved, "en",
                                                       "is this sold out")
        assert result is not None
