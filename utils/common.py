"""
Common evaluation utilities for Waffarha Chatbot RAG eval harnesses.
Provides get_engine() and run_one() for running test queries against RagEngine.
"""
import time
from typing import Any, Dict, Optional

import config as core_config
from core import rag_engine
from core.rag_engine import RagEngine, detect_lang


_ENGINE_CACHE: Dict[tuple, RagEngine] = {}


def get_engine(
    embedding_model: Optional[str] = None,
    backend: str = "faiss",
    llm_model: Optional[str] = None,
    llm_options: Optional[dict] = None,
    force_llm_generation: bool = False,
    no_retrieval: bool = False,
) -> RagEngine:
    """Returns a cached or newly created RagEngine instance."""
    key = (
        embedding_model or core_config.EMBEDDING_MODEL,
        backend,
        llm_model or core_config.OLLAMA_MODEL,
        frozenset((llm_options or {}).items()),
        force_llm_generation,
        no_retrieval,
    )
    if key not in _ENGINE_CACHE:
        _ENGINE_CACHE[key] = RagEngine(
            embedding_model=embedding_model,
            backend=backend,
            llm_model=llm_model,
            llm_options=llm_options,
            force_llm_generation=force_llm_generation,
            no_retrieval=no_retrieval,
        )
    return _ENGINE_CACHE[key]


def run_one(engine: RagEngine, case: Dict[str, Any]) -> Dict[str, Any]:
    """Runs a single eval test case against RagEngine and verifies assertions.

    A test case can contain:
      - id: str
      - category: str
      - query: str
      - history: list (optional)
      - recent_offers: list (optional)
      - expected_source: "faq" | "offer" | list
      - expected_id: int | str | list
      - expected_keywords: list of strings that must appear in answer (case-insensitive)
      - forbidden_keywords: list of strings that must NOT appear in answer
    """
    case_id = case.get("id")
    category = case.get("category")
    query = case["query"]
    history = case.get("history")
    recent_offers = case.get("recent_offers")

    t0 = time.time()
    res = engine.answer(query, history=history, recent_offers=recent_offers)
    latency_s = round(time.time() - t0, 3)

    answer = res.get("answer", "")
    sources = res.get("sources", [])
    scaffolding_leak = res.get("scaffolding_leak_stripped", [])

    top_source = None
    top_id = None
    top_score = None
    if sources:
        top_meta = sources[0].get("metadata", {})
        top_source = top_meta.get("source")
        top_id = top_meta.get("id")
        top_score = round(sources[0].get("combined_score", sources[0].get("score", 0.0)), 4)

    checks = {}

    # 1. Expected Source Check
    if "expected_source" in case:
        exp_src = case["expected_source"]
        if isinstance(exp_src, (list, tuple, set)):
            checks["expected_source"] = top_source in exp_src
        else:
            checks["expected_source"] = top_source == exp_src

    # 2. Expected ID Check
    if "expected_id" in case:
        exp_id = case["expected_id"]
        if isinstance(exp_id, (list, tuple, set)):
            checks["expected_id"] = str(top_id) in [str(i) for i in exp_id]
        else:
            checks["expected_id"] = str(top_id) == str(exp_id)

    # 3. Expected Keywords Check
    if "expected_keywords" in case:
        ans_lower = answer.lower()
        missing = [kw for kw in case["expected_keywords"] if kw.lower() not in ans_lower]
        checks["expected_keywords"] = len(missing) == 0

    # 4. Forbidden Keywords Check
    if "forbidden_keywords" in case:
        ans_lower = answer.lower()
        forbidden_found = [kw for kw in case["forbidden_keywords"] if kw.lower() in ans_lower]
        checks["forbidden_keywords"] = len(forbidden_found) == 0

    # 5. Scaffolding leak check
    checks["no_scaffolding_leak"] = len(scaffolding_leak) == 0

    passed = all(checks.values()) if checks else None

    return {
        "id": case_id,
        "category": category,
        "query": query,
        "answer": answer,
        "top_source": top_source,
        "top_id": top_id,
        "top_score": top_score,
        "latency_s": latency_s,
        "scaffolding_leak": scaffolding_leak,
        "checks": checks,
        "passed": passed,
    }
