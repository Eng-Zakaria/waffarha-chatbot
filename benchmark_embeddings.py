#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Benchmark multiple embedding models for retrieval accuracy.

Builds FAISS + BM25 indices for each candidate model and runs the
retrieval-only test suite to compare Hit@1, Hit@k, and latency.

Models to compare:
1. intfloat/multilingual-e5-base (768-dim) -- CURRENT DEFAULT
2. intfloat/multilingual-e5-large (1024-dim) -- HIGHER CAPACITY E5
3. BAAI/bge-m3 (1024-dim) -- STATE OF THE ART MULTILINGUAL
4. sentence-transformers/paraphrase-multilingual-mpnet-base-v2 (768-dim) -- MPNET MULTILINGUAL
"""
import json
import os
import sys
import time
from pathlib import Path

from sentence_transformers import SentenceTransformer

# Add root to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from ingest.build_index import load_faqs, load_offers, build_for
from common import get_engine, run_one


BENCHMARK_MODELS = [
    "intfloat/multilingual-e5-base",
    "intfloat/multilingual-e5-large",
    "BAAI/bge-m3",
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",
]


def run_model_benchmark(model_name: str, cases: list) -> dict:
    """Build index and run retrieval evaluation for a single embedding model."""
    print(f"\n==================================================")
    print(f"BENCHMARKING MODEL: {model_name}")
    print(f"==================================================")

    # 1. Build index
    start_time = time.time()
    docs = load_faqs() + load_offers()
    print(f"Loading SentenceTransformer: {model_name}...")
    model = SentenceTransformer(model_name, device=config.EMBEDDING_DEVICE)

    print(f"Encoding {len(docs)} chunks...")
    texts = [d["text"] for d in docs]
    encode_start = time.time()
    embeddings = model.encode(
        texts, batch_size=64, show_progress_bar=False,
        normalize_embeddings=True, convert_to_numpy=True
    ).astype("float32")
    encode_time = time.time() - encode_start

    print(f"Building FAISS + BM25 index...")
    build_for(model_name, "faiss", docs, embeddings)
    build_time = time.time() - start_time

    # 2. Run retrieval evaluation
    print(f"Running evaluation queries...")
    engine = get_engine(
        embedding_model=model_name,
        backend="faiss",
        force_llm_generation=False,
        no_retrieval=False,
    )

    passed_count = 0
    total_checked = 0
    hit_at_1_count = 0
    hit_at_k_count = 0
    latencies = []

    for case in cases:
        try:
            query_start = time.time()
            rec = run_one(engine, case)
            query_time = time.time() - query_start
            latencies.append(query_time)

            if rec.get("passed") is not None:
                total_checked += 1
                if rec["passed"]:
                    passed_count += 1

            # Hit@1 check
            if case.get("expected_id"):
                expected = str(case["expected_id"])
                top_id = str(rec.get("top_id")) if rec.get("top_id") is not None else None
                if top_id == expected:
                    hit_at_1_count += 1

                # Hit@k check in retrieved sources
                sources = rec.get("sources", [])
                retrieved_ids = [str(s.get("metadata", {}).get("id")) for s in sources]
                if expected in retrieved_ids:
                    hit_at_k_count += 1

        except Exception as e:
            print(f"  Error on case {case.get('id')}: {e}")

    avg_latency = (sum(latencies) / len(latencies)) if latencies else 0.0
    pass_rate = (passed_count / total_checked * 100) if total_checked > 0 else 0.0
    hit_1_rate = (hit_at_1_count / total_checked * 100) if total_checked > 0 else 0.0
    hit_k_rate = (hit_at_k_count / total_checked * 100) if total_checked > 0 else 0.0

    result = {
        "model_name": model_name,
        "embedding_dim": embeddings.shape[1],
        "num_docs": len(docs),
        "encode_time_s": round(encode_time, 2),
        "build_time_s": round(build_time, 2),
        "total_cases_checked": total_checked,
        "passed_cases": passed_count,
        "pass_rate_pct": round(pass_rate, 2),
        "hit_at_1_pct": round(hit_1_rate, 2),
        "hit_at_k_pct": round(hit_k_rate, 2),
        "avg_query_latency_s": round(avg_latency, 3),
    }

    print(f"\nRESULTS FOR {model_name}:")
    print(f"  Pass Rate:   {pass_rate:.1f}% ({passed_count}/{total_checked})")
    print(f"  Hit@1 Rate:  {hit_1_rate:.1f}%")
    print(f"  Hit@k Rate:  {hit_k_rate:.1f}%")
    print(f"  Avg Latency: {avg_latency:.3f}s")
    print(f"  Encode Time: {encode_time:.2f}s")

    return result


def main():
    queries_file = Path(__file__).parent / "queries.json"
    with open(queries_file, "r", encoding="utf-8") as f:
        cases = json.load(f)

    print(f"Loaded {len(cases)} evaluation test cases from {queries_file}")

    all_results = []
    for model_name in BENCHMARK_MODELS:
        try:
            res = run_model_benchmark(model_name, cases)
            all_results.append(res)
        except Exception as e:
            print(f"Failed to benchmark model {model_name}: {e}")

    # Write summary comparison table
    out_file = Path(__file__).parent / "benchmark_embedding_models.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print(f"\n" + "="*70)
    print("EMBEDDING MODEL BENCHMARK COMPARISON SUMMARY")
    print("="*70)
    print(f"{'Model':<40} | {'Dim':<5} | {'Pass %':<8} | {'Hit@1 %':<8} | {'Avg Lat (s)':<10}")
    print("-" * 75)
    for r in all_results:
        print(f"{r['model_name']:<40} | {r['embedding_dim']:<5} | {r['pass_rate_pct']:<8.1f} | {r['hit_at_1_pct']:<8.1f} | {r['avg_query_latency_s']:<10.3f}")
    print("="*70)
    print(f"Full results saved to {out_file}")


if __name__ == "__main__":
    main()
