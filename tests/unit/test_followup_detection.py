"""
Regression tests for follow-up detection and "other offer" resolution.

These tests verify that ambiguous short queries like "فيه عرض تاني عندهم؟"
correctly anchor to the previous offer's merchant rather than free-floating
to an unrelated offer.
"""

import pytest
from unittest.mock import patch, MagicMock

from core.rag_engine import RagEngine, _looks_like_followup_text, _mentioned_merchants

pytestmark = pytest.mark.engine


class TestFollowUpDetection:
    """Test that ambiguous follow-up queries are correctly classified."""

    @pytest.fixture
    def engine(self):
        return RagEngine(require_llm=False)

    @pytest.fixture
    def mock_recent_offers(self):
        return [{
            "metadata": {
                "source": "offer",
                "id": "koshary_001",
                "merchant": "كشري الغباشي",
                "title": "وجبة كشري لفردين بخصم 30%",
            }
        }]

    def test_arabic_other_offer_query(self, engine, mock_recent_offers):
        """'فيه عرض تاني عندهم؟' should be OTHER_OFFER_SAME_SOURCE."""
        targets, verdict = engine._resolve_followup_targets(
            "فيه عرض تاني عندهم؟", mock_recent_offers
        )
        assert verdict == "OTHER_OFFER_SAME_SOURCE"
        assert len(targets) == 1
        assert targets[0]["metadata"]["merchant"] == "كشري الغباشي"

    def test_arabic_vague_other_query(self, engine, mock_recent_offers):
        """'في حاجة تانية من نفس المكان؟' should be OTHER_OFFER_SAME_SOURCE."""
        targets, verdict = engine._resolve_followup_targets(
            "في حاجة تانية من نفس المكان؟", mock_recent_offers
        )
        assert verdict == "OTHER_OFFER_SAME_SOURCE"
        assert len(targets) == 1

    def test_arabic_short_other_query(self, engine, mock_recent_offers):
        """'عندهم حاجة تانية؟' should be OTHER_OFFER_SAME_SOURCE."""
        targets, verdict = engine._resolve_followup_targets(
            "عندهم حاجة تانية؟", mock_recent_offers
        )
        assert verdict == "OTHER_OFFER_SAME_SOURCE"
        assert len(targets) == 1

    def test_arabic_franco_arabic_other_query(self, engine, mock_recent_offers):
        """'feh eh tany 3ndhm?' should be OTHER_OFFER_SAME_SOURCE."""
        targets, verdict = engine._resolve_followup_targets(
            "feh eh tany 3ndhm?", mock_recent_offers
        )
        assert verdict == "OTHER_OFFER_SAME_SOURCE"
        assert len(targets) == 1

    def test_franco_arabic_other_query(self, engine, mock_recent_offers):
        """'3ayez 3rd tany 3ndhom' should be recognized as follow-up."""
        targets, verdict = engine._resolve_followup_targets(
            "3ayez 3rd tany 3ndhom", mock_recent_offers
        )
        # Should be anchored (either SAME or OTHER)
        assert verdict in ("SAME_OFFER", "OTHER_OFFER_SAME_SOURCE")
        assert len(targets) == 1

    def test_same_offer_price_query(self, engine, mock_recent_offers):
        """'بكام العرض ده؟' should be SAME_OFFER."""
        targets, verdict = engine._resolve_followup_targets(
            "بكام العرض ده؟", mock_recent_offers
        )
        assert verdict == "SAME_OFFER"
        assert len(targets) == 1

    def test_new_topic_query(self, engine, mock_recent_offers):
        """A clearly unrelated long query should be NEW_TOPIC."""
        targets, verdict = engine._resolve_followup_targets(
            "شو هي اسعار الشاورما في مصر؟", mock_recent_offers
        )
        # Long query with no follow-up signals -> NEW_TOPIC
        assert verdict == "NEW_TOPIC"
        assert len(targets) == 0

    def test_empty_recent_offers(self, engine):
        """No recent offers -> always NEW_TOPIC."""
        targets, verdict = engine._resolve_followup_targets(
            "فيه عرض تاني عندهم؟", []
        )
        assert verdict == "NEW_TOPIC"
        assert len(targets) == 0


class TestRetrievalExclusion:
    """Test that 'other offer' queries exclude the previous offer from results."""

    @pytest.fixture
    def real_engine(self):
        return RagEngine(require_llm=False)

    def test_retrieve_excludes_previous_offer(self, real_engine):
        """When query is 'other offer', the previously shown offer should not be rank-1."""
        # Pick an actual merchant from the index that has more than one offer,
        # so the "other offer" follow-up can resolve to a different doc.
        anchor = next(m for m in real_engine._offer_merchants if "Espressolab" in m)
        anchor_doc = next(
            d for d in real_engine.docs
            if d["metadata"].get("merchant") == anchor
            and d["metadata"].get("source") == "offer"
        )
        mock_recent = [{
            "metadata": {
                "source": "offer",
                "id": anchor_doc["metadata"]["id"],
                "merchant": anchor,
                "title": anchor_doc["metadata"].get("title", ""),
            }
        }]

        retrieved = real_engine.retrieve(
            "فيه عرض تاني عندهم؟",
            history=[],
            recent_offers=mock_recent
        )

        assert len(retrieved) > 0
        # Top result should be from the same merchant
        top_merchant = retrieved[0]["metadata"].get("merchant", "")
        assert top_merchant == anchor

        # Top result should NOT be the exact same offer ID
        top_id = str(retrieved[0]["metadata"].get("id", ""))
        assert top_id != str(anchor_doc["metadata"]["id"])


class TestClassifierDirectCall:
    """Test the _classify_followup_verdict method directly."""

    def test_classify_arabic_other_offer(self):
        """Arabic 'other offer' phrases should return OTHER_OFFER_SAME_SOURCE."""
        engine = RagEngine(require_llm=False)
        anchor = {
            "metadata": {
                "source": "offer",
                "id": "123",
                "merchant": "كشري الغباشي",
                "title": "وجبة كشري",
            }
        }

        verdict = engine._classify_followup_verdict("فيه عرض تاني عندهم؟", anchor)
        assert verdict == "OTHER_OFFER_SAME_SOURCE"

    def test_classify_same_offer_price(self):
        """Price inquiry should return SAME_OFFER."""
        engine = RagEngine(require_llm=False)
        anchor = {"metadata": {"source": "offer", "id": "1", "merchant": "X", "title": "Y"}}

        verdict = engine._classify_followup_verdict("بكام العرض ده؟", anchor)
        assert verdict == "SAME_OFFER"

    def test_classify_new_topic_long_query(self):
        """Long self-contained query should return NEW_TOPIC."""
        engine = RagEngine(require_llm=False)
        anchor = {"metadata": {"source": "offer", "id": "1", "merchant": "X", "title": "Y"}}

        # Long query with distinct topic
        verdict = engine._classify_followup_verdict(
            "شو هي افضل مواصلات للذهاب الى مطار القاهرة الدولي؟",
            anchor
        )
        # Should be NEW_TOPIC because it's long and unrelated
        assert verdict == "NEW_TOPIC"

    def test_classify_franco_arabic_other(self):
        """Franco-Arabic 'other offer' should be detected."""
        engine = RagEngine(require_llm=False)
        anchor = {"metadata": {"source": "offer", "id": "1", "merchant": "كشري", "title": "وجبة"}}

        verdict = engine._classify_followup_verdict("feh eh tany 3ndhm?", anchor)
        assert verdict == "OTHER_OFFER_SAME_SOURCE"
