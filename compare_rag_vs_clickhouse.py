#!/usr/bin/env python
"""
Compare RAG (embedding-based) vs ClickHouse (direct SQL) for catalog queries.

This script evaluates both approaches on the same test queries to understand:
1. Which queries are better handled by ClickHouse (structured, fresh data)
2. Which queries are better handled by RAG (semantic, fuzzy, FAQ)
3. Performance differences
4. Accuracy differences

Usage:
    python compare_rag_vs_clickhouse.py
    python compare_rag_vs_clickhouse.py --queries eval/queries.json
    python compare_rag_vs_clickhouse.py --output comparison_results.json
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime
from typing import Dict, List, Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rag_engine import RagEngine
from ingest.catalog_queries import CatalogQueryService, is_catalog_query
import config


def detect_lang(text: str) -> str:
    return "ar" if any("؀" <= ch <= "ۿ" for ch in text) else "en"


def run_rag_query(engine: RagEngine, query: str, lang: str = None) -> Dict:
    """Run a query through the RAG engine."""
    if lang is None:
        lang = detect_lang(query)

    t0 = time.time()
    result = engine.answer(query)
    latency = time.time() - t0

    sources = result.get("sources", [])
    top_source = None
    top_id = None
    top_score = None
    if sources:
        top_meta = sources[0].get("metadata", {})
        top_source = top_meta.get("source")
        top_id = top_meta.get("id")
        top_score = sources[0].get("combined_score", sources[0].get("score", 0.0))

    return {
        "answer": result.get("answer", ""),
        "latency_s": round(latency, 3),
        "top_source": top_source,
        "top_id": top_id,
        "top_score": round(top_score, 4) if top_score else None,
        "num_sources": len(sources),
        "sources": [{"source": s["metadata"]["source"], "id": s["metadata"].get("id"),
                     "score": s.get("combined_score", s.get("score"))} for s in sources[:3]]
    }


def run_clickhouse_query(service: CatalogQueryService, query: str, lang: str = None, session_id: str = None) -> Dict:
    """Run a query through the ClickHouse catalog service."""
    if lang is None:
        lang = detect_lang(query)

    t0 = time.time()
    result = service.handle(query, lang, session_id=session_id)
    latency = time.time() - t0

    return {
        "answer": result.get("answer", ""),
        "latency_s": round(latency, 3),
        "error": None
    }


def compare_answers(rag_answer: str, ch_answer: str, query: str) -> Dict:
    """Compare two answers qualitatively."""
    rag_len = len(rag_answer)
    ch_len = len(ch_answer)

    # Check for specific patterns
    rag_has_price = any(c.isdigit() for c in rag_answer)
    ch_has_price = any(c.isdigit() for c in ch_answer)

    rag_has_merchant = any(word in rag_answer.lower() for word in ["kfc", "mcdonald", "pizza", "burger"])
    ch_has_merchant = any(word in ch_answer.lower() for word in ["kfc", "mcdonald", "pizza", "burger"])

    # Check for "I don't know" type responses
    rag_uncertain = any(phrase in rag_answer.lower() for phrase in [
        "don't have", "doesn't have", "not have", "can't find", "unable to",
        "مفيش", "لا أملك", "لا يوجد", "لا يمكنني", "لست متأكد"
    ])
    ch_uncertain = any(phrase in ch_answer.lower() for phrase in [
        "don't have", "doesn't have", "not have", "can't find", "unable to",
        "مفيش", "لا أملك", "لا يوجد", "لا يمكنني", "لست متأكد"
    ])

    return {
        "rag_length": rag_len,
        "ch_length": ch_len,
        "rag_has_numbers": rag_has_price,
        "ch_has_numbers": ch_has_price,
        "rag_has_merchant": rag_has_merchant,
        "ch_has_merchant": ch_has_merchant,
        "rag_uncertain": rag_uncertain,
        "ch_uncertain": ch_uncertain,
    }


def load_test_queries(queries_file: str) -> List[Dict]:
    """Load test queries from JSON file."""
    with open(queries_file, "r", encoding="utf-8") as f:
        return json.load(f)


def filter_catalog_queries(queries: List[Dict]) -> List[Dict]:
    """Filter to only catalog-type queries."""
    catalog_queries = []
    for q in queries:
        if is_catalog_query(q["query"]):
            catalog_queries.append(q)
    return catalog_queries


def main():
    parser = argparse.ArgumentParser(description="Compare RAG vs ClickHouse for catalog queries")
    parser.add_argument("--queries", default="queries.json", help="Path to queries JSON file")
    parser.add_argument("--output", default=None, help="Output file for results")
    parser.add_argument("--embedding-model", default=None, help="Embedding model for RAG")
    parser.add_argument("--backend", default="faiss", help="Vector backend for RAG")
    parser.add_argument("--llm-model", default=None, help="LLM model for RAG")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of queries to test")
    args = parser.parse_args()

    # Resolve queries file path
    if not os.path.isabs(args.queries):
        queries_file = os.path.join(os.path.dirname(__file__), args.queries)
    else:
        queries_file = args.queries

    print("=" * 80)
    print("RAG vs ClickHouse Comparison")
    print("=" * 80)
    print(f"Queries file: {queries_file}")
    print(f"Time: {datetime.now().isoformat()}")
    print()

    # Load queries
    all_queries = load_test_queries(queries_file)
    print(f"Loaded {len(all_queries)} total queries")

    # Filter to catalog queries
    catalog_queries = filter_catalog_queries(all_queries)
    print(f"Catalog queries: {len(catalog_queries)}")

    if args.limit:
        catalog_queries = catalog_queries[:args.limit]
        print(f"Limited to: {len(catalog_queries)} queries")

    # Initialize engines
    print("\nInitializing RAG engine...")
    rag_engine = RagEngine(
        embedding_model=args.embedding_model,
        backend=args.backend,
        llm_model=args.llm_model,
    )

    print("Initializing ClickHouse catalog service...")
    ch_service = CatalogQueryService()

    # Run comparisons
    results = []
    for i, case in enumerate(catalog_queries):
        query = case["query"]
        lang = case.get("lang", detect_lang(query))
        expected_id = case.get("expected_id")
        expected_source = case.get("expected_source")
        category = case.get("category", "unknown")

        print(f"\n[{i+1}/{len(catalog_queries)}] {query[:60]}... ({lang}) [{category}]")

        # Run RAG
        rag_result = run_rag_query(rag_engine, query, lang)

        # Run ClickHouse
        ch_result = run_clickhouse_query(ch_service, query, lang)

        # Compare
        comparison = compare_answers(rag_result["answer"], ch_result["answer"], query)

        result = {
            "query": query,
            "lang": lang,
            "category": category,
            "expected_id": expected_id,
            "expected_source": expected_source,
            "rag": rag_result,
            "clickhouse": ch_result,
            "comparison": comparison,
        }
        results.append(result)

        # Print summary
        print(f"  RAG:      {rag_result['latency_s']:.3f}s | src={rag_result['top_source']} id={rag_result['top_id']} score={rag_result['top_score']}")
        print(f"  ClickHouse: {ch_result['latency_s']:.3f}s")
        print(f"  RAG answer: {rag_result['answer'][:100]}...")
        print(f"  CH answer:  {ch_result['answer'][:100]}...")

    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)

    rag_latencies = [r["rag"]["latency_s"] for r in results]
    ch_latencies = [r["clickhouse"]["latency_s"] for r in results]

    print(f"Average RAG latency:      {sum(rag_latencies)/len(rag_latencies):.3f}s")
    print(f"Average ClickHouse latency: {sum(ch_latencies)/len(ch_latencies):.3f}s")
    print(f"RAG faster:               {sum(1 for r in results if r['rag']['latency_s'] < r['clickhouse']['latency_s'])} queries")
    print(f"ClickHouse faster:        {sum(1 for r in results if r['clickhouse']['latency_s'] < r['rag']['latency_s'])} queries")

    # By category
    categories = {}
    for r in results:
        cat = r["category"]
        if cat not in categories:
            categories[cat] = {"rag": [], "ch": [], "count": 0}
        categories[cat]["rag"].append(r["rag"]["latency_s"])
        categories[cat]["ch"].append(r["clickhouse"]["latency_s"])
        categories[cat]["count"] += 1

    print("\nBy category:")
    for cat, data in sorted(categories.items()):
        avg_rag = sum(data["rag"])/len(data["rag"])
        avg_ch = sum(data["ch"])/len(data["ch"])
        print(f"  {cat}: {data['count']} queries | RAG: {avg_rag:.3f}s | CH: {avg_ch:.3f}s")

    # Answer quality indicators
    rag_uncertain = sum(1 for r in results if r["comparison"]["rag_uncertain"])
    ch_uncertain = sum(1 for r in results if r["comparison"]["ch_uncertain"])
    rag_has_numbers = sum(1 for r in results if r["comparison"]["rag_has_numbers"])
    ch_has_numbers = sum(1 for r in results if r["comparison"]["ch_has_numbers"])

    print(f"\nAnswer quality indicators:")
    print(f"  RAG uncertain responses:     {rag_uncertain}/{len(results)}")
    print(f"  ClickHouse uncertain:        {ch_uncertain}/{len(results)}")
    print(f"  RAG answers with numbers:    {rag_has_numbers}/{len(results)}")
    print(f"  ClickHouse answers with nums: {ch_has_numbers}/{len(results)}")

    # Expected ID match rate (for queries that have expected_id)
    expected_id_cases = [r for r in results if r["expected_id"] is not None]
    if expected_id_cases:
        rag_id_match = sum(1 for r in expected_id_cases
                          if str(r["rag"]["top_id"]) == str(r["expected_id"]))
        print(f"\nExpected ID match rate (RAG): {rag_id_match}/{len(expected_id_cases)} = {rag_id_match/len(expected_id_cases)*100:.1f}%")

    # Save results
    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "embedding_model": args.embedding_model or config.EMBEDDING_MODEL,
                "backend": args.backend,
                "llm_model": args.llm_model or config.OLLAMA_MODEL,
            },
            "summary": {
                "total_queries": len(results),
                "avg_rag_latency": sum(rag_latencies)/len(rag_latencies),
                "avg_ch_latency": sum(ch_latencies)/len(ch_latencies),
            },
            "results": results
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\nResults saved to: {args.output}")

    print("\nDone!")


if __name__ == "__main__":
    main()