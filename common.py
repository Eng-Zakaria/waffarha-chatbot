"""
Shared helpers for the eval/ scripts. Nothing in here talks to FastAPI,
Redis, or the chat widget -- eval calls RagEngine directly so a run doesn't
need the server (or a Redis instance) up, and so timing reflects just
retrieval+generation, not HTTP/session overhead.

Kept deliberately thin: this is a harness around the untouched business
logic in rag_engine.py, not a reimplementation of it.
"""
import sys
import time
from pathlib import Path

# Let `python eval/run_eval.py` find rag_engine/config/ingest from the repo root
# regardless of the caller's cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rag_engine import RagEngine, _SCAFFOLDING_MARKERS  # noqa: E402


_engine_cache = {}


def get_engine(embedding_model: str = None, backend: str = "faiss",
                llm_model: str = None, llm_options: dict = None) -> RagEngine:
    """Cached by (embedding_model, backend, llm_model) so a run over many
    queries only pays the SentenceTransformer load + Ollama handshake once,
    not per query. llm_options is intentionally excluded from the cache key
    -- see run_eval.py's --temperature flag."""
    key = (embedding_model, backend, llm_model)
    if key not in _engine_cache:
        _engine_cache[key] = RagEngine(
            embedding_model=embedding_model, backend=backend,
            llm_model=llm_model, llm_options=llm_options,
        )
    return _engine_cache[key]


def run_one(engine: RagEngine, case: dict) -> dict:
    """Runs a single test case dict (see queries.json's shape) through the
    engine and returns a result record ready to be dumped to JSON/CSV.
    Grading here is intentionally simple substring/id matching -- good
    enough to flag regressions for a human to look at, not a claim of being
    a full correctness oracle. Treat `passed=False` as "go read the answer",
    not as ground truth on its own.
    """
    t0 = time.time()
    result = engine.answer(case["query"], history=case.get("history"),
                            recent_offers=case.get("recent_offers"))
    latency = time.time() - t0

    answer = result["answer"]
    sources = result.get("sources", [])
    top_meta = sources[0]["metadata"] if sources else {}

    checks = {}
    if "expected_source" in case:
        checks["expected_source"] = top_meta.get("source") == case["expected_source"]
    if "expected_id" in case:
        checks["expected_id"] = str(top_meta.get("id")) == str(case["expected_id"])
    if "expected_keywords" in case:
        low = answer.lower()
        checks["expected_keywords"] = all(kw.lower() in low for kw in case["expected_keywords"])
    if "forbidden_keywords" in case:
        low = answer.lower()
        checks["forbidden_keywords"] = all(kw.lower() not in low for kw in case["forbidden_keywords"])

    leaked_markers = [m for m in _SCAFFOLDING_MARKERS if m in answer]

    return {
        "id": case.get("id"),
        "category": case.get("category"),
        "query": case["query"],
        "answer": answer,
        "top_source": top_meta.get("source"),
        "top_id": top_meta.get("id"),
        "top_title": top_meta.get("title") or top_meta.get("question"),
        "top_score": sources[0].get("combined_score") if sources else None,
        "num_sources": len(sources),
        "scaffolding_leak": bool(leaked_markers) or bool(result.get("scaffolding_leak_stripped")),
        "latency_s": round(latency, 3),
        "checks": checks,
        "passed": all(checks.values()) if checks else None,
    }
