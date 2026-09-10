#!/usr/bin/env python3
"""
Benchmark Arabic-specialized embedding models against the current baseline
using Qdrant as the vector store backend.

Compares retrieval accuracy (Recall@K, MRR), source classification accuracy,
and indexing/query latency across multiple embedding models.

Usage:
    python eval/bench_embeddings.py
    python eval/bench_embeddings.py --models e5-large atm-v2 bge-m3
    python eval/bench_embeddings.py --force-build   # rebuild indexes from scratch
    python eval/bench_embeddings.py --k 5
"""
import argparse
import json
import os
import pickle
import sys
import time

import numpy as np

# Ensure UTF-8 output on Windows
if sys.stdout.reconfigure:
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sentence_transformers import SentenceTransformer
from core import config
from ingestion.loaders.build_index import load_faqs, load_offers

# ---------------------------------------------------------------------------
# Model registry: short alias -> (huggingface model id, display name)
# ---------------------------------------------------------------------------
MODELS = {
    "e5-large": (
        "intfloat/multilingual-e5-large",
        "e5-large (1024d, current)",
        {},
    ),
    "atm-v2": (
        "Omartificial-Intelligence-Space/Arabic-Triplet-Matryoshka-V2",
        "ATM-V2 (768d, Arabic STS)",
        {},
    ),
    "bge-m3": (
        "BAAI/bge-m3",
        "BGE-M3 (1024d, multilingual)",
        {},
    ),
    "minilm": (
        "sentence-transformers/all-MiniLM-L6-v2",
        "MiniLM-L6 (384d, latency ref)",
        {},
    ),
}

BENCH_DIR = os.path.join(config.INDEX_DIR, "bench")
QUERIES_PATH = os.path.join(os.path.dirname(__file__), "queries.json")
RESULTS_PATH = os.path.join(os.path.dirname(__file__), "embedding_benchmark_results.json")


def _safe_name(model_id: str) -> str:
    return model_id.replace("/", "__").replace(":", "_")


def load_queries() -> list:
    with open(QUERIES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_qdrant_index(model_id, model_display, docs, force=False, extra_kwargs=None):
    """Encode docs with the given model and build a Qdrant collection.
    Returns (qdrant_store, encode_time_s, index_time_s)."""
    from vectorstores.vectorstores import QdrantStore

    safe = _safe_name(model_id)
    persist_path = os.path.join(BENCH_DIR, safe, "qdrant")
    emb_path = os.path.join(BENCH_DIR, safe, "embeddings.npy")

    # Reuse cached embeddings if available and not forced
    if not force and os.path.exists(emb_path):
        print(f"  [{model_display}] Reusing cached embeddings from {emb_path}")
        store = QdrantStore(persist_path, collection_name=safe)
        store.load()
        return store, 0.0, 0.0

    print(f"  [{model_display}] Loading model ...")
    model = SentenceTransformer(model_id, device=config.EMBEDDING_DEVICE, **(extra_kwargs or {}))

    texts = [d["text"] for d in docs]
    print(f"  [{model_display}] Encoding {len(texts)} chunks ...")
    t0 = time.perf_counter()
    embeddings = model.encode(
        texts,
        batch_size=64,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    ).astype("float32")
    encode_time = time.perf_counter() - t0

    print(f"  [{model_display}] Building Qdrant index ...")
    os.makedirs(persist_path, exist_ok=True)
    store = QdrantStore(persist_path, collection_name=safe)
    t1 = time.perf_counter()
    store.build(embeddings, docs)
    index_time = time.perf_counter() - t1

    # Cache embeddings for future runs
    os.makedirs(os.path.dirname(emb_path), exist_ok=True)
    np.save(emb_path, embeddings)

    print(f"  [{model_display}] Encode: {encode_time:.1f}s  Index: {index_time:.1f}s")
    return store, encode_time, index_time


# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------

def recall_at_k(ranked_ids, expected_id, k):
    """1.0 if expected_id appears in the top-k results, else 0.0."""
    return 1.0 if expected_id in ranked_ids[:k] else 0.0


def mrr(ranked_ids, expected_id):
    """Reciprocal rank of expected_id in the ranked list."""
    for i, doc_id in enumerate(ranked_ids):
        if doc_id == expected_id:
            return 1.0 / (i + 1)
    return 0.0


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(args):
    print("=" * 60)
    print("  Arabic Embedding Benchmark (Qdrant backend)")
    print("=" * 60)

    # --- Load documents ---
    print("\n[1/4] Loading documents ...")
    docs = load_faqs() + load_offers()
    if not docs:
        print("ERROR: No documents found. Run ingestion first.")
        sys.exit(1)
    n_faqs = sum(1 for d in docs if d["metadata"]["source"] == "faq")
    n_offers = sum(1 for d in docs if d["metadata"]["source"] == "offer")
    print(f"  {n_faqs} FAQ + {n_offers} offer = {len(docs)} chunks")

    # --- Load queries ---
    print("\n[2/4] Loading queries ...")
    all_queries = load_queries()
    retrieval_queries = [q for q in all_queries if q.get("expected_id") is not None]
    source_queries = [q for q in all_queries if q.get("expected_source") is not None]
    print(f"  {len(retrieval_queries)} queries with expected_id (retrieval accuracy)")
    print(f"  {len(source_queries)} queries with expected_source (source classification)")

    # --- Select models ---
    if args.models:
        selected = {k: v for k, v in MODELS.items() if k in args.models}
        if not selected:
            print(f"ERROR: Unknown model alias. Choose from: {', '.join(MODELS.keys())}")
            sys.exit(1)
    else:
        selected = MODELS

    # --- Build / load indexes ---
    print(f"\n[3/4] Building Qdrant indexes ({len(selected)} models) ...")
    stores = {}
    encode_times = {}
    index_times = {}
    for alias, (model_id, display, extra_kw) in selected.items():
        print(f"\n  --- {display} ---")
        store, et, it = build_qdrant_index(model_id, display, docs, force=args.force_build, extra_kwargs=extra_kw)
        stores[alias] = store
        encode_times[alias] = et
        index_times[alias] = it

    # --- Build ID -> doc-index mapping ---
    # Qdrant stores docs by sequential index (0..N-1). The eval queries
    # use the actual offer/faq ID (e.g. 8119, "faq_4"). Build a mapping
    # from expected_id -> list of doc indices so we can compare.
    id_to_indices = {}
    for idx, d in enumerate(docs):
        doc_id = d["metadata"].get("id")
        if doc_id is not None:
            id_to_indices.setdefault(doc_id, []).append(idx)

    # --- Evaluate ---
    print(f"\n[4/4] Running retrieval benchmark (k={args.k}) ...")
    results = {}
    for alias, (model_id, display, extra_kw) in selected.items():
        store = stores[alias]
        print(f"\n  Evaluating {display} ...")

        # Encode retrieval queries with this model
        model = SentenceTransformer(model_id, device=config.EMBEDDING_DEVICE, **extra_kw)
        ret_texts = [q["query"] for q in retrieval_queries]
        ret_embs = model.encode(
            ret_texts, batch_size=128, show_progress_bar=False,
            normalize_embeddings=True, convert_to_numpy=True,
        ).astype("float32")

        # Retrieval accuracy: Recall@K, MRR
        # expected_id is the real doc ID; we check if any of its indices
        # appear in the top-k returned indices.
        recalls = {1: [], 3: [], 5: []}
        mrrs = []
        for i, q in enumerate(retrieval_queries):
            hits = store.search(ret_embs[i : i + 1], k=args.k)[0]
            ranked_indices = [doc_idx for _, doc_idx in hits]
            expected_id = q["expected_id"]
            expected_indices = set(id_to_indices.get(expected_id, []))
            # Check if any of the expected doc's indices are in top-k
            hit = any(idx in expected_indices for idx in ranked_indices[:args.k])
            for k_val in [1, 3, 5]:
                hit_k = any(idx in expected_indices for idx in ranked_indices[:k_val])
                recalls[k_val].append(1.0 if hit_k else 0.0)
            # MRR: reciprocal rank of first matching index
            rr = 0.0
            for rank, idx in enumerate(ranked_indices, 1):
                if idx in expected_indices:
                    rr = 1.0 / rank
                    break
            mrrs.append(rr)

        # Source classification accuracy
        src_texts = [q["query"] for q in source_queries]
        src_embs = model.encode(
            src_texts, batch_size=128, show_progress_bar=False,
            normalize_embeddings=True, convert_to_numpy=True,
        ).astype("float32")

        source_correct = 0
        source_total = 0
        for i, q in enumerate(source_queries):
            hits = store.search(src_embs[i : i + 1], k=1)[0]
            if not hits:
                continue
            top_doc_idx = hits[0][1]
            predicted_source = docs[top_doc_idx]["metadata"]["source"]
            if predicted_source == q["expected_source"]:
                source_correct += 1
            source_total += 1

        source_acc = source_correct / source_total if source_total else 0

        # Latency benchmark (on retrieval queries)
        latencies = []
        n_latency = min(50, len(ret_embs))
        for i in range(n_latency):
            t0 = time.perf_counter()
            store.search(ret_embs[i : i + 1], k=args.k)
            latencies.append((time.perf_counter() - t0) * 1000)

        avg_latency = float(np.mean(latencies)) if latencies else 0
        p95_latency = float(np.percentile(latencies, 95)) if latencies else 0

        results[alias] = {
            "model_id": model_id,
            "display": display,
            "encode_time_s": encode_times[alias],
            "index_time_s": index_times[alias],
            "recall@1": float(np.mean(recalls[1])),
            "recall@3": float(np.mean(recalls[3])),
            "recall@5": float(np.mean(recalls[5])),
            "mrr": float(np.mean(mrrs)),
            "source_accuracy": source_acc,
            "avg_latency_ms": avg_latency,
            "p95_latency_ms": p95_latency,
        }
        print(f"    Recall@1={results[alias]['recall@1']:.3f}  "
              f"MRR={results[alias]['mrr']:.3f}  "
              f"Source={source_acc:.3f}  "
              f"Latency={avg_latency:.1f}ms")

    # --- Print comparison table ---
    print("\n" + "=" * 90)
    print("  RESULTS")
    print("=" * 90)
    header = (
        f"{'Model':<35} {'R@1':>6} {'R@3':>6} {'R@5':>6} "
        f"{'MRR':>6} {'Source':>7} {'Lat(ms)':>8} {'P95(ms)':>8}"
    )
    print(header)
    print("-" * 90)
    for alias in selected:
        r = results[alias]
        print(
            f"{r['display']:<35} "
            f"{r['recall@1']:>6.3f} "
            f"{r['recall@3']:>6.3f} "
            f"{r['recall@5']:>6.3f} "
            f"{r['mrr']:>6.3f} "
            f"{r['source_accuracy']:>7.3f} "
            f"{r['avg_latency_ms']:>8.1f} "
            f"{r['p95_latency_ms']:>8.1f}"
        )

    # --- Best per metric ---
    print("\n--- Best per metric ---")
    metrics_to_check = ["recall@1", "recall@3", "recall@5", "mrr", "source_accuracy"]
    for metric in metrics_to_check:
        best_alias = max(results, key=lambda a: results[a][metric])
        print(f"  {metric:<18}: {results[best_alias]['display']} ({results[best_alias][metric]:.3f})")
    best_lat = min(results, key=lambda a: results[a]["avg_latency_ms"])
    print(f"  {'latency':<18}: {results[best_lat]['display']} ({results[best_lat]['avg_latency_ms']:.1f}ms)")

    # --- Improvement over baseline ---
    if "e5-large" in results:
        baseline = results["e5-large"]
        print("\n--- Improvement over baseline (e5-large) ---")
        for alias in selected:
            if alias == "e5-large":
                continue
            r = results[alias]
            delta_r1 = r["recall@1"] - baseline["recall@1"]
            delta_mrr = r["mrr"] - baseline["mrr"]
            delta_src = r["source_accuracy"] - baseline["source_accuracy"]
            print(
                f"  {r['display']:<35} "
                f"R@1: {delta_r1:+.3f}  "
                f"MRR: {delta_mrr:+.3f}  "
                f"Source: {delta_src:+.3f}"
            )

    # --- Save results ---
    output = {
        "benchmark_config": {
            "k": args.k,
            "num_retrieval_queries": len(retrieval_queries),
            "num_source_queries": len(source_queries),
            "num_docs": len(docs),
            "embedding_device": config.EMBEDDING_DEVICE,
        },
        "models": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved to {RESULTS_PATH}")


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Arabic embedding models with Qdrant"
    )
    parser.add_argument(
        "--models", nargs="*", choices=list(MODELS.keys()),
        help="Model aliases to benchmark (default: all). "
             "Choices: " + ", ".join(MODELS.keys()),
    )
    parser.add_argument(
        "--k", type=int, default=5,
        help="Top-K for retrieval metrics (default: 5)",
    )
    parser.add_argument(
        "--force-build", action="store_true",
        help="Force rebuild indexes even if cached",
    )
    args = parser.parse_args()
    run_benchmark(args)


if __name__ == "__main__":
    main()
