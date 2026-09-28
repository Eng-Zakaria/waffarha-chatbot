"""Local-vs-cloud side-by-side comparison (branch: exp/free-cloud-llm).

Runs the same queries through up to THREE generation backends over the SAME
index/embeddings and writes a JSON report with each answer, per-query
latencies, errors, and the trimmed retrieval sources:

- local        -- Ollama model on this machine (default config.OLLAMA_MODEL)
- gemini       -- Google Gemini free tier (needs GEMINI_API_KEY in env/.env;
                  skipped with a recorded note when the key is missing)
- pollinations -- pollinations.ai anonymous tier: free, NO signup, NO key
                  (best-effort: anonymous rate limits / outages are recorded
                  per query instead of aborting the run)

Providers run SEQUENTIALLY (one engine alive at a time) so peak RAM stays at
a single embedding model, not three. Retrieval uses local embeddings only,
so the `sources` snapshot is backend-independent and captured once.

Usage:
    python eval/compare_local_vs_cloud.py
    python eval/compare_local_vs_cloud.py --limit 10 --out eval/my_run.json
    python eval/compare_local_vs_cloud.py --queries-file eval/my_queries.json
    python eval/compare_local_vs_cloud.py --no-retrieval   # pure LLM-vs-LLM, no RAG
"""

import argparse
import datetime as _dt
import gc
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

# Sources snapshot: first N docs, small identifying fields + truncated text.
_SOURCES_KEPT = 6
_SOURCE_META_KEYS = ("title", "merchant", "price", "old_price", "source",
                     "faq_id", "offer_id", "id", "category", "url")
_SOURCE_TEXT_KEYS = ("text", "content", "page_content", "chunk")
_SOURCE_TEXT_CHARS = 300


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


def _trim_sources(sources) -> list:
    out = []
    for s in (sources or [])[:_SOURCES_KEPT]:
        if not isinstance(s, dict):
            out.append({"value": str(s)[:_SOURCE_TEXT_CHARS]})
            continue
        keep = {k: s[k] for k in
                ("score", "embedding_score", "combined_score", "lexical_hits")
                if k in s}
        meta = s.get("metadata")
        if isinstance(meta, dict):
            keep["metadata"] = {k: v for k, v in meta.items()
                                if k in _SOURCE_META_KEYS}
        for k in _SOURCE_TEXT_KEYS:
            if isinstance(s.get(k), str):
                keep["text"] = s[k][:_SOURCE_TEXT_CHARS]
                break
        out.append(keep)
    return out


def _run_one(engine, query: str) -> dict:
    started = time.perf_counter()
    try:
        result = engine.answer(query, [], [], None)
        return {
            "answer": result.get("answer", ""),
            "seconds": round(time.perf_counter() - started, 2),
            "sources": _trim_sources(result.get("sources")),
        }
    except Exception as e:  # noqa: BLE001 -- record, don't stop the run
        return {
            "answer": "",
            "error": f"{type(e).__name__}: {str(e)[:300]}",
            "seconds": round(time.perf_counter() - started, 2),
            "sources": [],
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
    ap.add_argument("--pollinations-model", default=None,
                    help="Pollinations alias (default: config.POLLINATIONS_MODEL)")
    ap.add_argument("--skip", default="",
                    help="comma-separated providers to skip, e.g. 'pollinations'")
    ap.add_argument("--no-retrieval", action="store_true",
                    help="skip RAG retrieval: pure pre-trained LLM vs LLM")
    ap.add_argument("--force-llm", action="store_true",
                    help="skip direct-answer shortcuts, always generate")
    ap.add_argument("--backend", default=None,
                    help="vector backend (default: config.VECTOR_STORE_BACKEND); "
                         "use 'faiss' when the qdrant folder is locked")
    ap.add_argument("--embedding-model", default=None,
                    help="embedding model (default: config.EMBEDDING_MODEL)")
    args = ap.parse_args()

    from core import config
    from core.rag_engine import RagEngine

    queries = _load_queries(args.queries_file, args.limit)
    if not queries:
        print("ERROR: no queries to run.", file=sys.stderr)
        return 2
    skip = {s.strip().lower() for s in args.skip.split(",") if s.strip()}

    specs = [("local", "ollama", args.local_model or config.OLLAMA_MODEL)]
    if os.getenv("GEMINI_API_KEY"):
        specs.append(("gemini", "gemini",
                      args.gemini_model or config.GEMINI_MODEL))
    else:
        print("NOTE: GEMINI_API_KEY not set -- gemini column will record "
              "'skipped'. Get a free key at "
              "https://aistudio.google.com/apikey.")
    specs.append(("pollinations", "pollinations",
                  args.pollinations_model or config.POLLINATIONS_MODEL))
    specs = [s for s in specs if s[0] not in skip]

    common = dict(no_retrieval=args.no_retrieval,
                  force_llm_generation=args.force_llm)
    if args.backend:
        common["backend"] = args.backend
    if args.embedding_model:
        common["embedding_model"] = args.embedding_model
    rows = [{"query": q} for q in queries]
    meta_models = {}

    for name, provider, model in specs:
        print(f"--- provider: {name} ({provider}:{model}) ---")
        try:
            engine = RagEngine(llm_provider=provider, llm_model=model,
                               **common)
        except Exception as e:  # noqa: BLE001 -- e.g. missing key/service
            print(f"  engine build failed: {e}")
            for row in rows:
                row[name] = {"answer": "",
                             "error": f"engine build failed: {str(e)[:200]}",
                             "seconds": 0, "sources": []}
            meta_models[name] = f"{model} (BUILD FAILED)"
            continue
        meta_models[name] = engine.llm_model
        for i, (q, row) in enumerate(zip(queries, rows), 1):
            res = _run_one(engine, q)
            row[name] = {k: v for k, v in res.items() if k != "sources"}
            if i == 1 or not row.get("sources"):
                row["sources"] = res["sources"]
            flag = "ERR " if res.get("error") else "ok  "
            print(f"  [{i}/{len(queries)}] {flag} {res['seconds']}s -- "
                  f"{q[:60]}")
            if res.get("error"):
                print(f"           {res['error'][:160]}")
        del engine
        gc.collect()

    # One shared sources snapshot per query (retrieval is backend-independent).
    for row in rows:
        row.setdefault("sources", [])

    out = args.out or os.path.join(
        "eval", "local_vs_cloud_"
        + _dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
    report = {
        "ts": _dt.datetime.now().isoformat(timespec="seconds"),
        "models": meta_models,
        "backend": args.backend or config.VECTOR_STORE_BACKEND,
        "embedding_model": args.embedding_model or config.EMBEDDING_MODEL,
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
