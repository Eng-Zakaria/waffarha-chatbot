"""
Runs eval/queries.json against RagEngine and logs results.

    python eval/run_eval.py
    python eval/run_eval.py --queries eval/queries.json --embedding-model intfloat/multilingual-e5-base
    python eval/run_eval.py --llm-model qwen2.5:3b-instruct --temperature 0.0

Writes two files per run to eval/results/<timestamp>/:
  - full.json     every field from common.run_one(), per query
  - summary.csv    one row per query, spreadsheet-friendly

Exit code is 1 if any case with explicit checks (expected_source,
expected_id, expected_keywords, forbidden_keywords) failed -- so this can
be wired into CI or a pre-push hook without extra scripting.

Doesn't touch app.py, Redis, or the FastAPI server -- calls RagEngine
directly, so this runs against whatever index is on disk without the
server needing to be up.
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

from common import get_engine, run_one  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", default=str(Path(__file__).parent / "queries.json"))
    parser.add_argument("--embedding-model", default=None, help="Defaults to config.EMBEDDING_MODEL")
    parser.add_argument("--backend", default="faiss")
    parser.add_argument("--llm-model", default=None, help="Defaults to config.OLLAMA_MODEL")
    parser.add_argument("--temperature", type=float, default=None,
                         help="Override generation temperature for this run only")
    parser.add_argument("--out-dir", default=str(Path(__file__).parent / "results"))
    parser.add_argument("--tag", default=None, help="Extra label folded into the results folder name")
    args = parser.parse_args()

    with open(args.queries, "r", encoding="utf-8") as f:
        cases = json.load(f)

    llm_options = {"temperature": args.temperature} if args.temperature is not None else None
    engine = get_engine(embedding_model=args.embedding_model, backend=args.backend,
                         llm_model=args.llm_model, llm_options=llm_options)

    records = []
    for case in cases:
        print(f"  [{case.get('id')}] {case['query'][:60]!r} ...", end=" ", flush=True)
        try:
            rec = run_one(engine, case)
        except Exception as e:
            rec = {"id": case.get("id"), "category": case.get("category"), "query": case["query"],
                   "error": str(e), "passed": False}
        records.append(rec)
        status = "ERROR" if "error" in rec else (
            "PASS" if rec["passed"] else "FAIL" if rec["passed"] is False else "n/a"
        )
        print(f"{status}  ({rec.get('latency_s', '-')}s)")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{ts}_{args.tag}" if args.tag else ts
    out_dir = Path(args.out_dir) / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "full.json", "w", encoding="utf-8") as f:
        json.dump({
            "run_at": ts,
            "embedding_model": engine.embedding_model_name,
            "backend": engine.backend,
            "llm_model": engine.llm_model,
            "results": records,
        }, f, ensure_ascii=False, indent=2)

    csv_fields = ["id", "category", "passed", "top_source", "top_id", "top_score",
                  "scaffolding_leak", "latency_s", "answer", "error"]
    with open(out_dir / "summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    n_total = len(records)
    n_checked = sum(1 for r in records if r.get("passed") is not None)
    n_passed = sum(1 for r in records if r.get("passed") is True)
    n_errors = sum(1 for r in records if "error" in r)
    n_leaks = sum(1 for r in records if r.get("scaffolding_leak"))
    avg_latency = sum(r.get("latency_s", 0) for r in records if "latency_s" in r) / max(
        1, sum(1 for r in records if "latency_s" in r))

    print(f"\n{n_passed}/{n_checked} checked cases passed ({n_total} total, {n_errors} errors, "
          f"{n_leaks} scaffolding leaks, avg {avg_latency:.2f}s/query)")
    print(f"Results written to {out_dir}/")

    sys.exit(1 if n_passed < n_checked or n_errors else 0)


if __name__ == "__main__":
    main()
