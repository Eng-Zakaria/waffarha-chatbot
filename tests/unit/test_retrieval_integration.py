"""Integration tests against the real BGE-M3 + Qdrant index.

Run ONLY with ``pytest -m engine`` -- each test builds a full RagEngine
(embedding model + 9 k docs, ~50 s cold) which is far too heavy for the
default fast suite.
"""
import os, sys, json, pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.rag_engine import RagEngine

_REPO = Path(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
_QUERIES = {q["id"]: q for q in json.load(open(_REPO / "eval" / "queries.json", encoding="utf-8"))}

pytestmark = pytest.mark.engine


@pytest.fixture(scope="module")
def engine():
    return RagEngine(require_llm=False)


def _eval_query(qid: str) -> dict:
    q = _QUERIES.get(qid)
    assert q is not None, f"eval query {qid!r} missing from queries.json"
    return q


# --- Real-index golden hit assertions ---

@pytest.mark.parametrize("qid", [
    "faq_register_ar",
    "payment_fawry_ar",
    "offer_amani_beauty_ar",
    "offer_dentalboss_price_ar",
    "stock_check_kfc_ar",
    "same_merchant_oasis_nightstay",
])
def test_golden_top_source_in_top3(engine, qid):
    q = _eval_query(qid)
    top3 = engine.retrieve(q["query"], top_k=3)
    sources = [r["metadata"]["source"] for r in top3]
    assert q["expected_source"] in sources, (
        f"{qid}: expected {q['expected_source']!r} not in top-3 sources {sources}"
    )


# --- Dual-accounting invariant (embedding_score / bonus_total) ---

@pytest.mark.parametrize("qid", [
    "faq_register_ar",
    "offer_amani_beauty_ar",
    "stock_check_kfc_ar",
])
def test_candidates_carry_valid_dual_scores(engine, qid):
    q = _eval_query(qid)
    results = engine.retrieve(q["query"], top_k=10)
    assert results, f"retrieve() returned empty for {qid}"
    for i, r in enumerate(results):
        emb = r["embedding_score"]
        comb = r["combined_score"]
        bonus = r["bonus_total"]
        assert 0.0 <= emb <= 1.0, f"candidate {i}: emb={emb} out of [0,1]"
        assert bonus >= 0.0, f"candidate {i}: bonus={bonus} negative"
        assert abs(comb - (emb + bonus)) < 1e-6, (
            f"candidate {i}: combined {comb} != emb {emb} + bonus {bonus}"
        )
