"""ingestion/refresh.py -- the single, canonical refresh entry point.

Collapses the whole ingestion chain into one command. This is the pipeline the
rest of the repo should reference (build_index.py / build_index_incremental.py
stay usable directly; the individual fetch scripts are now orchestration steps,
not separate recipes):

    python ingestion/refresh.py                    # fetch from ClickHouse + rebuild index
    python ingestion/refresh.py --skip-fetch       # rebuild from existing data/ files
    python ingestion/refresh.py --plan             # lineage report, NO ClickHouse, NO writes
    python ingestion/refresh.py --steps sources,faqs --backend faiss

Pipeline (default `--steps`):
  1. `sources`      -- fetch_offers_clickhouse.py      -> data/offers_raw.json
  2. `faqs`         -- fetch_payment_methods_clickhouse.py -> data/faqs_payment_methods.json
                      fetch_purchasing_status_clickhouse.py -> data/faqs_purchasing_status.json
  3. `type-prices`  -- fetch_type_price_clickhouse.py  -> data/type_prices.json   (status=1 tiers)
  4. `partners`     -- fetch_partners_clickhouse.py --snapshot -> data/partners/partners.json
  5. `build`        -- build_index.py -> data/index/<model>/<backend>/ (+ index_manifest.json)
                  `incremental`      -> build_index_incremental.py (FAISS-only speedup)

Every step is a subprocess of the SAME venv python, so each script keeps its
own argparse/entry point and none of the fetch/write behaviors changed. The
orchestrator records lineage: per-step duration, source-file sha256 + bytes,
and the resulting index manifest's corpus hash + doc counts, all appended to
`data/refresh_log.json` (rolling, capped).

Refresh cadence (see docs/INGESTION.md for the rationale):
  - offers / type-prices / partners: daily (coupon expiry + price freshness is
    the dominant hallucination risk -- build after each fetch)
  - faqs (payment methods / purchasing status): on change, they're slow-moving
  - an index build (step 5) should run after any of the above changes; the
    manifest's per-source hashes make "is this index stale?" a one-lookup check.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import config
from ingestion.loaders.index_manifest import (
    index_dir,
    manifest_path_for_dir,
    source_file_fingerprints,
)

PY = sys.executable
_ENV = dict(os.environ)
_ENV.setdefault("PYTHONIOENCODING", "utf-8")

FETCH_STEPS = ["sources", "faqs", "type-prices", "partners"]
ALL_STEPS = FETCH_STEPS + ["build"]
DEFAULT_LOG = os.path.join(config.INDEX_DIR, "refresh_log.json")
LOG_MAX_HISTORY = 50

STEP_SCRIPTS = {
    "sources": [("ingestion/sources/fetch_offers_clickhouse.py", ["--output", "offers_raw.json"])],
    "faqs": [
        ("ingestion/sources/fetch_payment_methods_clickhouse.py", ["--write"]),
        ("ingestion/sources/fetch_purchasing_status_clickhouse.py", ["--write"]),
    ],
    "type-prices": [("ingestion/sources/fetch_type_price_clickhouse.py", ["--write"])],
    "partners": [("ingestion/sources/fetch_partners_clickhouse.py", ["--snapshot"])],
}


def _read_log(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _append_log(path: str, entry: dict):
    log = _read_log(path)
    history = log.get("history") or []
    history = (history + [entry])[-LOG_MAX_HISTORY:]
    log["schema_version"] = 1
    log["history"] = history
    log["latest"] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    print(f"\nRefresh log updated: {path}")


def _target_dir(model: str, backend: str, out_dir: str | None = None) -> str:
    return out_dir or index_dir(model, backend)


def _run_steps(steps, args) -> dict:
    """Run fetch steps sequentially; returns {step: seconds}."""
    timings = {}
    for step in steps:
        if step == "build" or step == "incremental":
            continue
        if args.skip_fetch:
            print(f"[refresh] --skip-fetch: skipping ClickHouse step '{step}'")
            continue
        for script, script_args in STEP_SCRIPTS[step]:
            cmd = [PY, os.path.join(ROOT, script)] + list(script_args)
            print(f"\n[refresh] step '{step}' -> {os.path.relpath(os.path.join(ROOT, script))}")
            t0 = time.time()
            proc = subprocess.run(cmd, env=_ENV, check=False)
            elapsed = time.time() - t0
            if proc.returncode != 0:
                raise SystemExit(
                    f"[refresh] step '{step}' FAILED (exit {proc.returncode}) after {elapsed:.1f}s -- aborting "
                    f"(fix the failing step or re-run with --skip-fetch to build from existing data)."
                )
            timings[step] = timings.get(step, 0.0) + elapsed
            print(f"[refresh] step '{step}' ok in {elapsed:.1f}s")
    return timings


def _build_cmd(builder: str, args) -> list:
    cmd = [PY, os.path.join(ROOT, builder),
           "--backend", args.backend,
           "--embedding-model", args.embedding_model]
    if args.batch_size:
        cmd += ["--batch-size", str(args.batch_size)]
    if args.max_rows:
        cmd += ["--max-rows", str(args.max_rows)]
    if args.out_dir:
        cmd += ["--out-dir", args.out_dir]
    if builder.endswith("build_index_incremental.py") and args.force_full:
        cmd += ["--force-full"]
    return cmd


def _run_build(args, timings: dict) -> dict:
    builder = ("ingestion/loaders/build_index_incremental.py"
               if args.step == "incremental" else "ingestion/loaders/build_index.py")
    cmd = _build_cmd(builder, args)
    print(f"\n[refresh] step '{args.step}' -> {builder}  ({' '.join(cmd[len(PY):])})")
    t0 = time.time()
    proc = subprocess.run(cmd, env=_ENV, check=False)
    elapsed = time.time() - t0
    timings[args.step] = elapsed
    if proc.returncode != 0:
        raise SystemExit(f"[refresh] step '{args.step}' FAILED (exit {proc.returncode}) after {elapsed:.1f}s.")
    print(f"[refresh] step '{args.step}' ok in {elapsed:.1f}s")

    manifest = {}
    d = _target_dir(args.embedding_model, args.backend, args.out_dir)
    mp = manifest_path_for_dir(d)
    try:
        with open(mp, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[refresh] WARNING: could not read {mp}: {e}")
    return {"manifest_path": mp, "manifest": manifest}


def do_plan(args) -> int:
    """Lineage report: current data files, target indexes, manifest freshness."""
    print("=== Ingest PLAN (no writes, no ClickHouse) ===\n")
    print(f"repo root: {ROOT}")
    print(f"INDEX_DIR: {config.INDEX_DIR}")
    print(f"embedding_model: {args.embedding_model}  backend: {args.backend}")
    print(f"corpus flags: INCLUDE_EXPIRED_OFFERS={config.INCLUDE_EXPIRED_OFFERS} "
          f"OFFER_ACTIVE_VALUES={config.OFFER_ACTIVE_VALUES}")

    print("\n-- source files (data/) --")
    fps = source_file_fingerprints()
    if not fps:
        print("  (none found -- run 'python ingestion/refresh.py' to fetch from ClickHouse)")
    for name, info in sorted(fps.items()):
        print(f"  {name:32s} {info['bytes']:>9,d}B  sha256={info['sha256']}")

    print(f"\n-- target index ({args.embedding_model} / {args.backend}) --")
    d = _target_dir(args.embedding_model, args.backend)
    mp = manifest_path_for_dir(d)
    print(f"  dir:      {d}")
    if not os.path.exists(mp):
        print("  manifest: ABSENT (index missing or pre-manifest build)")
        print("  next:     run 'python ingestion/refresh.py' (fetch + build) or "
              "'python ingestion/refresh.py --skip-fetch' (build only)")
        return 0

    with open(mp, "r", encoding="utf-8") as f:
        man = json.load(f)
    files = man.get("source", {}).get("files", {})
    stale = [n for n in fps if (n not in files) or (files[n].get("sha256") != fps[n]["sha256"])]
    print(f"  manifest: {mp}")
    print(f"    built:      {man.get('updated_at')}  by {man.get('built_by')}")
    print(f"    schema:     {man.get('schema_version')}  corpus_hash={man.get('source', {}).get('corpus_hash')}")
    s = man.get("stats", {})
    print(f"    docs:       {s.get('total_docs')} total "
          f"({s.get('faq_count')} faq / {s.get('offer_count')} offer)")
    print(f"    stale data: {stale if stale else 'NO -- in sync with data/'}")
    print("  next: " + (f"data files changed under data/ ({len(stale)} file(s)) -- re-run refresh.py to rebuild"
                        if stale else "data in sync; no rebuild needed (re-run when data changes)"))
    return 0


def main():
    parser = argparse.ArgumentParser(prog="ingestion/refresh.py", description=__doc__)
    parser.add_argument("--plan", action="store_true",
                        help="Lineage + freshness report only -- no ClickHouse calls, no writes.")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Do not call ClickHouse; build from existing data/ files. Default steps -> just build.")
    parser.add_argument("--steps", nargs="+",
                        choices=FETCH_STEPS + ["build", "incremental"],
                        help="Steps to run (default: sources faqs type-prices partners build). "
                             "Multiple steps may be passed, e.g. --steps sources build.")
    parser.add_argument("--backend", default=config.VECTOR_STORE_BACKEND,
                        help="Vector backend for the build step (default: config.VECTOR_STORE_BACKEND).")
    parser.add_argument("--embedding-model", default=config.EMBEDDING_MODEL,
                        help="Embedding model for the build step (default: config.EMBEDDING_MODEL).")
    parser.add_argument("--batch-size", type=int, default=None, help="Build encode batch size.")
    parser.add_argument("--force-full", action="store_true",
                        help="Incremental step: force a full rebuild instead of a diff.")
    parser.add_argument("--max-rows", type=int, default=None,
                        help="Dev/test: cap docs for the build step (sample index).")
    parser.add_argument("--out-dir", default=None,
                        help="Dev/test: write the built index (and manifest) to an explicit directory.")
    parser.add_argument("--log", default=DEFAULT_LOG, help=f"Refresh log path (default: {DEFAULT_LOG}).")
    args = parser.parse_args()

    if args.plan:
        return do_plan(args)

    # decide step list
    if args.steps:
        steps = list(args.steps)
    elif args.skip_fetch:
        steps = ["build"]
    else:
        steps = list(ALL_STEPS)

    print(f"=== Ingestion refresh: {', '.join(steps)}  (model={args.embedding_model} backend={args.backend}) ===")
    if args.skip_fetch:
        print("[refresh] --skip-fetch: building from existing data/ files (no ClickHouse).")

    # fetch steps first are run greedily; a 'build'/'incremental' step is the last
    # build in the list (compile the explicit order minus fetch steps)
    timings = _run_steps(steps, args)

    # run the (single) build step, respecting its position in the requested order
    for step in steps:
        if step in ("build", "incremental"):
            args.step = step
            build_info = _run_build(args, timings)
            break
    else:
        build_info = {"manifest_path": None, "manifest": {}}

    # record lineage
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "embedding_model": args.embedding_model,
        "backend": args.backend,
        "steps": steps,
        "durations_seconds": timings,
        "source_files": source_file_fingerprints(),
        "build": {
            "manifest_path": build_info["manifest_path"],
            "manifest": build_info["manifest"],
        },
    }
    _append_log(args.log, entry)
    print("\n=== Refresh complete. ===")


if __name__ == "__main__":
    main()