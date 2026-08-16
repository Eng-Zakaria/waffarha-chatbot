"""
Full project evaluation harness for the Waffarha Assistant.

Runs four independent test suites and rolls everything into one timestamped
report (JSON + CSV + Markdown):

  1. INFRASTRUCTURE  -- can we even load the embedding model, the FAISS
     index, and reach Ollama / Redis? (fail fast with a clear reason instead
     of every later suite mysteriously erroring out)
  2. RAG CORRECTNESS  -- runs queries.json straight through RagEngine
     in-process (same mechanism as the existing run_eval.py / common.py),
     scoring retrieval accuracy, keyword checks, scaffolding leaks, and
     per-query latency. No server needed for this part.
  3. SERVER SMOKE + CONCURRENCY  -- HTTP tests against a running
     `uvicorn app:app` (or the docker-compose `app` service): health check,
     a basic end-to-end /api/chat call, input validation (400 on empty
     query), then a concurrent burst of requests to measure latency
     percentiles and confirm the MAX_CONCURRENT_GENERATIONS /
     GENERATION_QUEUE_TIMEOUT semaphore behaves (no request hangs forever,
     overflow gets a clean 503 + Retry-After instead of a timeout).
  4. SESSION MEMORY  -- HTTP multi-turn conversation against a fixed
     session_id: ask about a specific offer, then ask a pronoun follow-up
     ("how much was it before the discount") and confirm the answer
     resolves back to the SAME offer (validates memory.py's Redis-backed
     SessionMemory round trip end to end, not just unit-level).

Suites 3 and 4 need the server (and therefore Ollama + Redis) actually
running; if they can't connect, they're recorded as SKIPPED with the
connection error rather than failing the whole run -- so `--skip-http` (or
simply not having the server up) still gives you a full RAG-correctness +
latency report from suite 2 alone.

Usage:
    # Everything, against a locally running server:
    python run_full_eval.py

    # Only the in-process RAG suite (no server required):
    python run_full_eval.py --skip-http

    # Point at a different host, different queries file, heavier load test:
    python run_full_eval.py --base-url http://localhost:8000 \
        --queries queries.json --concurrency 20 --concurrency-requests 60

    # Sweep a different embedding/LLM config for suite 2 only:
    python run_full_eval.py --embedding-model intfloat/multilingual-e5-base \
        --llm-model qwen2.5:3b-instruct --temperature 0.0

Writes to <out-dir>/<timestamp>[_<tag>]/:
    full.json      -- everything, machine-readable
    summary.csv    -- one row per RAG query (same shape as run_eval.py's)
    report.md       -- human-readable report with tables + a pass/fail summary

Exit code is 1 if anything the harness considers a hard failure occurred
(a checked RAG case failed, an infra check failed, a smoke test failed, or
the memory round-trip failed) -- SKIPPED suites (server not reachable) do
NOT fail the run, since they're opt-in via having the server up.
"""
import argparse
import csv
import json
import statistics
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# In-process pieces reused as-is from the existing eval harness -- this
# script does not reimplement retrieval/generation/grading, it orchestrates
# the same common.py machinery plus three new suites around it.
# ---------------------------------------------------------------------------
from common import get_engine, run_one  # noqa: E402
import config  # noqa: E402


# =============================================================================
# Small helpers
# =============================================================================

def _pct(values, p):
    """Nearest-rank percentile, no numpy dependency."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


def _stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "p95": round(_pct(values, 95), 3),
        "p99": round(_pct(values, 99), 3),
    }


def _badge(ok):
    return "✅ PASS" if ok else "❌ FAIL"


# =============================================================================
# Suite 1: infrastructure checks
# =============================================================================

def run_infra_checks(embedding_model, backend, llm_model):
    """Cheap, ordered checks so a failure points at the actual broken piece
    (missing index vs. Ollama down vs. Redis down) instead of a stack trace
    from three layers deep in RagEngine.answer()."""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": ok, "detail": detail})

    # 1a. Index files on disk, before we even try to load anything.
    try:
        from ingest.build_index import index_dir as _index_dir_fn
        idx_dir = _index_dir_fn(embedding_model or config.EMBEDDING_MODEL, backend)
        docs_path = Path(idx_dir) / "docs.pkl"
        add("index files present", docs_path.exists(),
            str(docs_path) if docs_path.exists() else f"missing: {docs_path}")
    except Exception as e:
        add("index files present", False, f"{type(e).__name__}: {e}")

    # 1b. Full engine load: embedding model + FAISS/backend + Ollama ping.
    # This is the same lazy-load path app.py's get_engine() uses.
    engine = None
    t0 = time.time()
    try:
        engine = get_engine(embedding_model=embedding_model, backend=backend, llm_model=llm_model)
        add("engine load (embedding model + index + Ollama reachable)", True,
            f"{round(time.time() - t0, 2)}s, {len(engine.docs)} docs, "
            f"{len(engine._offer_merchants)} known merchants")
    except FileNotFoundError as e:
        add("engine load (embedding model + index + Ollama reachable)", False, f"Index not found: {e}")
    except RuntimeError as e:
        add("engine load (embedding model + index + Ollama reachable)", False, f"Ollama unreachable: {e}")
    except Exception as e:
        add("engine load (embedding model + index + Ollama reachable)", False, f"{type(e).__name__}: {e}")

    # 1c. Redis, independently of the engine (memory.py's own connection).
    try:
        from memory import MemoryStore
        t0 = time.time()
        MemoryStore()
        add("redis reachable", True, f"{round(time.time() - t0, 2)}s via {config.REDIS_URL}")
    except Exception as e:
        add("redis reachable", False, f"{type(e).__name__}: {e} (REDIS_URL={config.REDIS_URL})")

    return {
        "checks": checks,
        "all_ok": all(c["ok"] for c in checks),
        "engine": engine,
    }


# =============================================================================
# Suite 2: RAG correctness + latency (in-process, via common.run_one)
# =============================================================================

def run_rag_suite(engine, queries_path):
    queries_path = Path(queries_path)
    if not queries_path.exists():
        return {"skipped": True, "reason": f"{queries_path} not found", "records": []}

    with open(queries_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    records = []
    for case in cases:
        try:
            rec = run_one(engine, case)
        except Exception as e:
            rec = {
                "id": case.get("id"), "category": case.get("category"),
                "query": case.get("query"), "error": str(e),
                "traceback": traceback.format_exc(limit=3), "passed": False,
            }
        records.append(rec)

    n_total = len(records)
    n_checked = sum(1 for r in records if r.get("passed") is not None)
    n_passed = sum(1 for r in records if r.get("passed") is True)
    n_errors = sum(1 for r in records if "error" in r)
    n_leaks = sum(1 for r in records if r.get("scaffolding_leak"))
    latencies = [r.get("latency_s") for r in records if "latency_s" in r]

    by_category = {}
    for r in records:
        cat = r.get("category") or "uncategorized"
        by_category.setdefault(cat, {"total": 0, "checked": 0, "passed": 0})
        by_category[cat]["total"] += 1
        if r.get("passed") is not None:
            by_category[cat]["checked"] += 1
            if r.get("passed"):
                by_category[cat]["passed"] += 1

    return {
        "skipped": False,
        "records": records,
        "n_total": n_total,
        "n_checked": n_checked,
        "n_passed": n_passed,
        "n_errors": n_errors,
        "n_leaks": n_leaks,
        "latency_stats": _stats(latencies),
        "by_category": by_category,
        "all_ok": n_errors == 0 and n_passed == n_checked,
    }


# =============================================================================
# Suite 3: server smoke test + concurrency load test (HTTP)
# =============================================================================

def _http_session():
    import requests
    return requests.Session()


def run_smoke_suite(base_url, session):
    results = []

    def check(name, fn):
        t0 = time.time()
        try:
            ok, detail = fn()
            results.append({"name": name, "ok": ok, "detail": detail,
                             "latency_s": round(time.time() - t0, 3)})
        except Exception as e:
            results.append({"name": name, "ok": False, "detail": f"{type(e).__name__}: {e}",
                             "latency_s": round(time.time() - t0, 3)})

    def _health():
        r = session.get(f"{base_url}/api/health", timeout=10)
        ok = r.status_code == 200 and r.json().get("status") == "ok"
        return ok, r.text[:300]

    def _empty_query_rejected():
        r = session.post(f"{base_url}/api/chat", json={"query": "", "session_id": "eval-smoke"},
                          timeout=10)
        return r.status_code == 400, f"status={r.status_code} body={r.text[:200]}"

    def _basic_chat():
        r = session.post(f"{base_url}/api/chat",
                          json={"query": "How do I redeem a coupon?", "session_id": "eval-smoke"},
                          timeout=90)
        if r.status_code != 200:
            return False, f"status={r.status_code} body={r.text[:300]}"
        body = r.json()
        ok = bool(body.get("answer")) and "sources" in body and "suggestions" in body
        return ok, f"answer_len={len(body.get('answer', ''))} sources={len(body.get('sources', []))}"

    check("GET /api/health", _health)
    check("POST /api/chat rejects empty query (400)", _empty_query_rejected)
    check("POST /api/chat basic end-to-end answer", _basic_chat)

    return {
        "results": results,
        "all_ok": all(r["ok"] for r in results),
    }


def run_concurrency_suite(base_url, session, n_requests, concurrency, query_pool):
    """Fires `n_requests` /api/chat calls using up to `concurrency` worker
    threads and measures latency + outcome distribution. This exercises the
    same asyncio.Semaphore path in app.py that a real traffic burst would --
    including the 503+Retry-After overflow behavior once
    MAX_CONCURRENT_GENERATIONS is exceeded, which a single-request smoke
    test can't observe at all."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(i):
        query = query_pool[i % len(query_pool)]
        session_id = f"eval-concurrency-{i}"  # distinct sessions: isolates memory.py writes per request
        t0 = time.time()
        try:
            r = session.post(f"{base_url}/api/chat",
                              json={"query": query, "session_id": session_id},
                              timeout=120)
            latency = time.time() - t0
            return {
                "i": i, "query": query, "status": r.status_code, "latency_s": round(latency, 3),
                "retry_after": r.headers.get("Retry-After") if r.status_code == 503 else None,
                "ok": r.status_code == 200,
                "error": None,
            }
        except Exception as e:
            return {
                "i": i, "query": query, "status": None, "latency_s": round(time.time() - t0, 3),
                "retry_after": None, "ok": False, "error": f"{type(e).__name__}: {e}",
            }

    t_start = time.time()
    outcomes = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(_one, i) for i in range(n_requests)]
        for fut in as_completed(futures):
            outcomes.append(fut.result())
    wall_s = time.time() - t_start

    outcomes.sort(key=lambda o: o["i"])
    n_ok = sum(1 for o in outcomes if o["ok"])
    n_503 = sum(1 for o in outcomes if o["status"] == 503)
    n_other_error = sum(1 for o in outcomes if not o["ok"] and o["status"] != 503)
    latencies = [o["latency_s"] for o in outcomes if o["ok"]]

    return {
        "n_requests": n_requests,
        "concurrency": concurrency,
        "wall_s": round(wall_s, 3),
        "throughput_rps": round(n_requests / wall_s, 3) if wall_s > 0 else None,
        "n_ok": n_ok,
        "n_503_backpressure": n_503,
        "n_other_error": n_other_error,
        "latency_stats_successful": _stats(latencies),
        "outcomes": outcomes,
        # A healthy result: no unhandled errors (other than a clean 503 with
        # Retry-After, which is the *intended* overflow behavior, not a bug).
        "all_ok": n_other_error == 0 and (n_503 == 0 or all(
            o["retry_after"] for o in outcomes if o["status"] == 503
        )),
    }


# =============================================================================
# Suite 4: session memory round-trip (HTTP, multi-turn)
# =============================================================================

def run_memory_suite(base_url, session, engine):
    """Verifies memory.py end to end: ask about a specific offer by name,
    then send a pronoun follow-up with NO merchant name in it, and confirm
    the follow-up's top source resolves back to the SAME offer id rather
    than free-floating to whatever's semantically closest in the whole
    corpus. This is exactly the failure mode memory.py's docstring and
    rag_engine.py's _resolve_followup_targets exist to prevent."""
    offer_docs = [d for d in engine.docs if d["metadata"].get("source") == "offer"
                  and d["metadata"].get("merchant") and d["metadata"].get("title")]
    if not offer_docs:
        return {"skipped": True, "reason": "no offer documents with merchant+title in the index"}

    target = offer_docs[0]["metadata"]
    merchant = target["merchant"]
    offer_id = target.get("id")
    session_id = f"eval-memory-{uuid.uuid4().hex[:8]}"

    steps = []

    def post(query):
        t0 = time.time()
        r = session.post(f"{base_url}/api/chat", json={"query": query, "session_id": session_id},
                          timeout=90)
        latency = round(time.time() - t0, 3)
        body = r.json() if r.status_code == 200 else {}
        return r.status_code, body, latency

    turn1_query = f"Tell me about the {merchant} offer"
    status1, body1, lat1 = post(turn1_query)
    steps.append({"turn": 1, "query": turn1_query, "status": status1, "latency_s": lat1,
                  "answer": body1.get("answer"), "sources": body1.get("sources")})

    turn1_ok = status1 == 200 and bool(body1.get("sources"))

    turn2_query = "How much was it before the discount?"
    status2, body2, lat2 = post(turn2_query)
    steps.append({"turn": 2, "query": turn2_query, "status": status2, "latency_s": lat2,
                  "answer": body2.get("answer"), "sources": body2.get("sources")})

    # Success condition: turn 2's answer text still names the same merchant
    # (via _source_card's title/merchant, which is all the HTTP layer
    # exposes -- we don't have raw metadata ids over HTTP) even though the
    # follow-up query itself never mentioned it.
    turn2_titles = " ".join(s.get("title", "") for s in body2.get("sources", []))
    turn2_answer = body2.get("answer", "")
    resolved = merchant.lower() in (turn2_titles + " " + turn2_answer).lower()

    return {
        "skipped": False,
        "target_merchant": merchant,
        "target_offer_id": offer_id,
        "session_id": session_id,
        "steps": steps,
        "turn1_ok": turn1_ok,
        "followup_resolved_to_same_offer": resolved,
        "all_ok": turn1_ok and status2 == 200 and resolved,
    }


# =============================================================================
# Report writing
# =============================================================================

def write_csv(records, path):
    fields = ["id", "category", "passed", "top_source", "top_id", "top_score",
              "scaffolding_leak", "latency_s", "answer", "error"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)


def _fmt_stats_row(label, s):
    if not s or s.get("count", 0) == 0:
        return f"| {label} | – | – | – | – | – | – |"
    return (f"| {label} | {s['count']} | {s['min']}s | {s['mean']}s | "
            f"{s['median']}s | {s['p95']}s | {s['max']}s |")


def write_markdown(report, path):
    lines = []
    a = lines.append
    ts = report["run_at"]
    a(f"# Waffarha Assistant -- Evaluation Report")
    a("")
    a(f"**Run:** {ts}  ")
    a(f"**Embedding model:** {report['env']['embedding_model']}  ")
    a(f"**Backend:** {report['env']['backend']}  ")
    a(f"**LLM model:** {report['env']['llm_model']}  ")
    a(f"**Base URL (HTTP suites):** {report['env']['base_url'] or 'n/a (--skip-http)'}  ")
    a("")

    overall_ok = report["overall_ok"]
    a(f"## Overall result: {_badge(overall_ok)}")
    a("")
    a("| Suite | Status |")
    a("|---|---|")
    for name, s in report["suites_status"].items():
        a(f"| {name} | {s} |")
    a("")

    # --- Infra ---
    infra = report["infra"]
    a("## 1. Infrastructure checks")
    a("")
    a("| Check | Result | Detail |")
    a("|---|---|---|")
    for c in infra["checks"]:
        a(f"| {c['name']} | {_badge(c['ok'])} | {c['detail']} |")
    a("")

    # --- RAG suite ---
    rag = report["rag"]
    a("## 2. RAG correctness & latency (in-process)")
    a("")
    if rag.get("skipped"):
        a(f"_Skipped: {rag['reason']}_")
        a("")
    else:
        a(f"- **Cases run:** {rag['n_total']} (**{rag['n_checked']}** had explicit checks)")
        a(f"- **Passed:** {rag['n_passed']}/{rag['n_checked']}")
        a(f"- **Errors:** {rag['n_errors']}")
        a(f"- **Scaffolding leaks:** {rag['n_leaks']}")
        a("")
        a("**Latency**")
        a("")
        a("| Metric | N | Min | Mean | Median | p95 | Max |")
        a("|---|---|---|---|---|---|---|")
        a(_fmt_stats_row("Per-query latency", rag["latency_stats"]))
        a("")
        a("**By category**")
        a("")
        a("| Category | Total | Checked | Passed |")
        a("|---|---|---|---|")
        for cat, s in sorted(rag["by_category"].items()):
            a(f"| {cat} | {s['total']} | {s['checked']} | {s['passed']} |")
        a("")
        failed = [r for r in rag["records"] if r.get("passed") is False or "error" in r]
        if failed:
            a("**Failed / errored cases**")
            a("")
            a("| id | category | issue |")
            a("|---|---|---|")
            for r in failed[:30]:
                if "error" in r:
                    issue = f"ERROR: {r['error']}"
                else:
                    failed_checks = [k for k, v in r.get("checks", {}).items() if not v]
                    issue = f"failed checks: {', '.join(failed_checks)}"
                a(f"| {r.get('id')} | {r.get('category')} | {issue} |")
            if len(failed) > 30:
                a(f"| … | | +{len(failed) - 30} more, see full.json |")
            a("")

    # --- Smoke suite ---
    smoke = report.get("smoke")
    a("## 3a. Server smoke test")
    a("")
    if smoke is None:
        a("_Skipped (--skip-http)._")
        a("")
    elif smoke.get("skipped"):
        a(f"_Skipped: {smoke['reason']}_")
        a("")
    else:
        a("| Check | Result | Latency | Detail |")
        a("|---|---|---|---|")
        for r in smoke["results"]:
            a(f"| {r['name']} | {_badge(r['ok'])} | {r['latency_s']}s | {r['detail']} |")
        a("")

    # --- Concurrency suite ---
    conc = report.get("concurrency")
    a("## 3b. Concurrency / load test")
    a("")
    if conc is None:
        a("_Skipped (--skip-http)._")
        a("")
    elif conc.get("skipped"):
        a(f"_Skipped: {conc['reason']}_")
        a("")
    else:
        a(f"- **Requests:** {conc['n_requests']} at concurrency {conc['concurrency']}")
        a(f"- **Wall time:** {conc['wall_s']}s ({conc['throughput_rps']} req/s)")
        a(f"- **Succeeded (200):** {conc['n_ok']}")
        a(f"- **Backpressure (503, expected under overload):** {conc['n_503_backpressure']}")
        a(f"- **Unexpected errors:** {conc['n_other_error']}")
        a(f"- **Configured capacity:** MAX_CONCURRENT_GENERATIONS={config.MAX_CONCURRENT_GENERATIONS}, "
          f"GENERATION_QUEUE_TIMEOUT={config.GENERATION_QUEUE_TIMEOUT}s")
        a("")
        a("| Metric | N | Min | Mean | Median | p95 | Max |")
        a("|---|---|---|---|---|---|---|")
        a(_fmt_stats_row("Successful request latency", conc["latency_stats_successful"]))
        a("")

    # --- Memory suite ---
    mem = report.get("memory")
    a("## 4. Session memory round-trip")
    a("")
    if mem is None:
        a("_Skipped (--skip-http)._")
        a("")
    elif mem.get("skipped"):
        a(f"_Skipped: {mem['reason']}_")
        a("")
    else:
        a(f"- **Target offer:** {mem['target_merchant']} (id={mem['target_offer_id']})")
        a(f"- **Session id:** `{mem['session_id']}`")
        a(f"- **Turn 1 (\"{mem['steps'][0]['query']}\"):** "
          f"{_badge(mem['turn1_ok'])} ({mem['steps'][0]['latency_s']}s)")
        a(f"- **Turn 2 follow-up (\"{mem['steps'][1]['query']}\"):** "
          f"resolved back to {mem['target_merchant']}: {_badge(mem['followup_resolved_to_same_offer'])} "
          f"({mem['steps'][1]['latency_s']}s)")
        a("")
        a("<details><summary>Full transcript</summary>")
        a("")
        for step in mem["steps"]:
            a(f"**Turn {step['turn']}:** {step['query']}")
            a("")
            a(f"> {step['answer']}")
            a("")
        a("</details>")
        a("")

    a("---")
    a(f"_Report generated by run_full_eval.py at {ts}._")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries", default="queries.json")
    parser.add_argument("--embedding-model", default=None, help="Defaults to config.EMBEDDING_MODEL")
    parser.add_argument("--backend", default="faiss")
    parser.add_argument("--llm-model", default=None, help="Defaults to config.OLLAMA_MODEL")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--skip-http", action="store_true",
                         help="Skip suites 3/3b/4 entirely (no server required)")
    parser.add_argument("--concurrency", type=int, default=8,
                         help="Max simultaneous /api/chat requests in the load test")
    parser.add_argument("--concurrency-requests", type=int, default=24,
                         help="Total requests fired in the load test")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{ts}_{args.tag}" if args.tag else ts
    out_dir = Path(args.out_dir) / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Waffarha Assistant full evaluation -- {ts} ===")
    print(f"Output: {out_dir}/\n")

    # ---- Suite 1: infra ----
    print("[1/4] Infrastructure checks...")
    infra = run_infra_checks(args.embedding_model, args.backend, args.llm_model)
    for c in infra["checks"]:
        print(f"      {_badge(c['ok'])}  {c['name']}  -- {c['detail']}")
    engine = infra.pop("engine")

    report = {
        "run_at": ts,
        "env": {
            "embedding_model": (engine.embedding_model_name if engine else
                                 (args.embedding_model or config.EMBEDDING_MODEL)),
            "backend": args.backend,
            "llm_model": (engine.llm_model if engine else (args.llm_model or config.OLLAMA_MODEL)),
            "base_url": None if args.skip_http else args.base_url,
        },
        "infra": infra,
    }

    # ---- Suite 2: RAG correctness (needs the engine from suite 1) ----
    if engine is not None:
        print("\n[2/4] RAG correctness + latency (queries.json, in-process)...")
        if args.temperature is not None:
            engine.llm_options = {**engine.llm_options, "temperature": args.temperature}
        rag = run_rag_suite(engine, args.queries)
        if rag.get("skipped"):
            print(f"      skipped: {rag['reason']}")
        else:
            print(f"      {rag['n_passed']}/{rag['n_checked']} checked cases passed "
                  f"({rag['n_total']} total, {rag['n_errors']} errors, {rag['n_leaks']} leaks)")
    else:
        rag = {"skipped": True, "reason": "engine failed to load, see infra checks above", "records": []}
        print("\n[2/4] RAG correctness -- SKIPPED (engine did not load)")
    report["rag"] = rag

    # ---- Suites 3/3b/4: HTTP ----
    smoke = conc = mem = None
    if not args.skip_http:
        print(f"\n[3/4] Server smoke test against {args.base_url} ...")
        try:
            session = _http_session()
            smoke = run_smoke_suite(args.base_url, session)
            for r in smoke["results"]:
                print(f"      {_badge(r['ok'])}  {r['name']}  ({r['latency_s']}s)  {r['detail']}")
        except Exception as e:
            smoke = {"skipped": True, "reason": f"{type(e).__name__}: {e}"}
            print(f"      skipped: {smoke['reason']}")

        if smoke and not smoke.get("skipped"):
            print(f"\n      Concurrency load test "
                  f"({args.concurrency_requests} requests @ concurrency={args.concurrency}) ...")
            query_pool = [c["query"] for c in json.load(open(args.queries, encoding="utf-8"))] \
                if Path(args.queries).exists() else ["What offers do you have?", "How do I redeem a coupon?"]
            conc = run_concurrency_suite(args.base_url, session, args.concurrency_requests,
                                          args.concurrency, query_pool)
            print(f"      {conc['n_ok']} ok, {conc['n_503_backpressure']} backpressure (503), "
                  f"{conc['n_other_error']} unexpected errors, wall={conc['wall_s']}s")

            print("\n[4/4] Session memory round-trip test ...")
            if engine is not None:
                mem = run_memory_suite(args.base_url, session, engine)
                if mem.get("skipped"):
                    print(f"      skipped: {mem['reason']}")
                else:
                    print(f"      turn1 ok={mem['turn1_ok']}  "
                          f"followup resolved to same offer={mem['followup_resolved_to_same_offer']}")
            else:
                mem = {"skipped": True, "reason": "engine did not load (needed to pick a target offer)"}
                print(f"      skipped: {mem['reason']}")
        else:
            conc = {"skipped": True, "reason": "server not reachable, see 3a"}
            mem = {"skipped": True, "reason": "server not reachable, see 3a"}
            print("\n[4/4] Session memory round-trip -- SKIPPED (server not reachable)")
    else:
        print("\n[3/4] Server smoke test -- SKIPPED (--skip-http)")
        print("[4/4] Session memory round-trip -- SKIPPED (--skip-http)")

    report["smoke"] = smoke
    report["concurrency"] = conc
    report["memory"] = mem

    # ---- Roll up pass/fail ----
    def suite_status(s, ok_key="all_ok"):
        if s is None:
            return "⏭️ skipped (--skip-http)"
        if s.get("skipped"):
            return f"⏭️ skipped ({s.get('reason', '')})"
        return _badge(s.get(ok_key, False))

    suites_status = {
        "1. Infrastructure": _badge(infra["all_ok"]),
        "2. RAG correctness": ("⏭️ skipped" if rag.get("skipped") else _badge(rag["all_ok"])),
        "3a. Server smoke": suite_status(smoke),
        "3b. Concurrency": suite_status(conc),
        "4. Session memory": suite_status(mem),
    }
    report["suites_status"] = suites_status

    hard_failures = [infra["all_ok"] is False]
    if not rag.get("skipped"):
        hard_failures.append(rag["all_ok"] is False)
    if smoke and not smoke.get("skipped"):
        hard_failures.append(smoke["all_ok"] is False)
    if conc and not conc.get("skipped"):
        hard_failures.append(conc["all_ok"] is False)
    if mem and not mem.get("skipped"):
        hard_failures.append(mem["all_ok"] is False)
    overall_ok = not any(hard_failures)
    report["overall_ok"] = overall_ok

    # ---- Write outputs ----
    with open(out_dir / "full.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    if not rag.get("skipped"):
        write_csv(rag["records"], out_dir / "summary.csv")

    write_markdown(report, out_dir / "report.md")

    print(f"\n=== Overall: {_badge(overall_ok)} ===")
    print(f"Results written to {out_dir}/")
    print(f"  - {out_dir / 'full.json'}")
    if not rag.get("skipped"):
        print(f"  - {out_dir / 'summary.csv'}")
    print(f"  - {out_dir / 'report.md'}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
