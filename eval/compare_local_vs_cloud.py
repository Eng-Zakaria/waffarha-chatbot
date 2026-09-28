"""Local-vs-cloud side-by-side comparison (branch: exp/free-cloud-llm).

Builds TWO RagEngine instances over the SAME index/embeddings -- one
generating with the local Ollama model, one with free-tier Gemini -- runs
the same queries through ``engine.answer()``, and writes a JSON report with
both answers, latencies, and any errors.

Usage:
    set GEMINI_API_KEY=...            # or put it in .env (see .env.example)
    python eval/compare_local_vs_cloud.py
    python eval/compare_local_vs_cloud.py --queries-file eval/my_queries.json --limit 10
    python eval/compare_local_vs_cloud.py --no-retrieval   # pure LLM-vs-LLM, no RAG context

NOTE: both engines load their own embedding model instance, so startup takes
~2x the RAM/time of a single engine. The intent-judge singleton stays on the
default (Ollama) backend for both runs on purpose -- one less variable, so
answer differences come from generation, not judging.
"""

import argparse
import datetime as _dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_QUERIES = [
    "What breakfast offers do you have under 200 EGP?",
    "Show me KFC offers",
    "How do I use my purchased coupon?",
    "What payment methods do you accept?",
    "عندك عروض بيتزا إيه؟",
    "إزاي أستلم الأوردر بتاعي؟",
]


def _load_queries(path: str | None, limit: int) -> list:
    if path:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "queries" in data:
            data = data["queries"]
        queries = [q if isinstance(q, str) else q.get("query", "") for q in data]
        queries = [q for q in queries if q]
    else:
        queries = list(DEFAULT_QUERIES)
    return queries[:limit] if limit else queries


def _run_one(engine, query: str) -> dict:
    started = time.perf_counter()
    try:
        result = engine.answer(query, [], [], None)
        return {
            "answer": result.get("answer", ""),
            "seconds": round(time.perf_counter() - started, 2),
        }
    except Exception as e:  # noqa: BLE001 -- record, don't stop the run
        return {
            "answer": "",
            "error": f"{type(e).__name__}: {e}",
            "seconds": round(time.perf_counter() - started, 2),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queries-file", default=None,
                    help="JSON list of query strings (or {queries: [...]})")
    ap.add_argument("--limit", type=int, default=0,
                    help="max queries to run (0 = all)")
    ap.add_argument("--out", default=None,
                    help="report path (default eval/local_vs_cloud_<ts>.json)")
    ap.add_argument("--local-model", default=None,
                    help="Ollama tag (default: config.OLLAMA_MODEL)")
    ap.add_argument("--gemini-model", default=None,
                    help="Gemini model (default: config.GEMINI_MODEL)")
    ap.add_argument("--no-retrieval", action="store_true",
                    help="skip RAG retrieval: pure pre-trained LLM vs LLM")
    ap.add_argument("--force-llm", action="store_true",
                    help="skip direct-answer shortcuts, always generate")
    args = ap.parse_args()

    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY is not set. Get a free key at "
              "https://aistudio.google.com/apikey and set it in your .env, "
              "then retry.", file=sys.stderr)
        return 2

    from core import config
    from core.rag_engine import RagEngine

    queries = _load_queries(args.queries_file, args.limit)
    if not queries:
        print("ERROR: no queries to run.", file=sys.stderr)
        return 2

    common = dict(no_retrieval=args.no_retrieval,
                  force_llm_generation=args.force_llm)
    print(f"Loading local engine (ollama:{args.local_model or config.OLLAMA_MODEL}) ...")
    local = RagEngine(llm_provider="ollama", llm_model=args.local_model, **common)
    print(f"Loading cloud engine (gemini:{args.gemini_model or config.GEMINI_MODEL}) ...")
    cloud = RagEngine(llm_provider="gemini", llm_model=args.gemini_model, **common)

    rows = []
    for i, q in enumerate(queries, 1):
        print(f"[{i}/{len(queries)}] {q}")
        local_res = _run_one(local, q)
        cloud_res = _run_one(cloud, q)
        rows.append({"query": q, "local": local_res, "cloud": cloud_res})
        print(f"  local {local_res.get('seconds')}s "
              f"| cloud {cloud_res.get('seconds')}s")

    out = args.out or os.path.join(
        "eval", "local_vs_cloud_"
        + _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    report = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "local_model": local.llm_model,
        "cloud_model": cloud.llm_model,
        "no_retrieval": args.no_retrieval,
        "force_llm": args.force_llm,
        "results": rows,
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {len(rows)} comparisons -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
