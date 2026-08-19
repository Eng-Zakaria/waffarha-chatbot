"""
Full project evaluation harness for the Waffarha Assistant.

Runs five independent test suites and rolls everything into one timestamped
report (JSON + CSV + Markdown):

  1. INFRASTRUCTURE  -- can we even load the embedding model, the FAISS
     index, and reach Ollama / Redis? (fail fast with a clear reason instead
     of every later suite mysteriously erroring out)
  2. RETRIEVAL-ONLY  -- calls RagEngine.retrieve() directly (embedding +
     FAISS search + lexical/intent scoring), with NO LLM call at all.
     Scores hit@1 / hit@k / MRR against queries.json's expected_source /
     expected_id, plus embedding-only latency and a determinism sanity
     check. This is what answers "is retrieval good or bad, independent of
     the model" -- it isolates the retriever/embedding layer from
     generation. Needs the embedding model + FAISS index; does NOT need
     Ollama (see --rag-only / --retrieval-only below).
  3. RAG CORRECTNESS, FULL PIPELINE  -- runs queries.json straight through
     RagEngine.answer() in-process (same mechanism as the existing
     run_eval.py / common.py): retrieval AND generation together, scoring
     keyword checks, scaffolding leaks, and per-query latency. This is
     "what the user actually sees" -- compare its pass rate against suite
     2's to tell a retrieval regression apart from a generation regression.
     Both suites 2 and 3 report per-category AND per-language (en/ar/mixed)
     breakdowns, since queries.json now carries an explicit "lang" field.
  4. SERVER SMOKE + CONCURRENCY  -- HTTP tests against a running
     `uvicorn app:app` (or the docker-compose `app` service): health check,
     a basic end-to-end /api/chat call, input validation (400 on empty
     query), then a concurrent burst of requests to measure latency
     percentiles and confirm the MAX_CONCURRENT_GENERATIONS /
     GENERATION_QUEUE_TIMEOUT semaphore behaves (no request hangs forever,
     overflow gets a clean 503 + Retry-After instead of a timeout).
  5. SESSION MEMORY  -- HTTP multi-turn conversation against a fixed
     session_id: ask about a specific offer, then ask a pronoun follow-up
     ("how much was it before the discount") and confirm the answer
     resolves back to the SAME offer (validates memory.py's Redis-backed
     SessionMemory round trip end to end, not just unit-level).

Suites 4 and 5 need the server (and therefore Ollama + Redis) actually
running; if they can't connect, they're recorded as SKIPPED with the
connection error rather than failing the whole run -- so `--skip-http` (or
simply not having the server up) still gives you full retrieval + RAG
correctness + latency reports from suites 2 and 3 alone.

Usage:
    # Everything, against a locally running server:
    python run_full_eval.py

    # RAG only: retrieval + in-process generation, no server, no Redis,
    # and Ollama isn't required to even START the run (suite 2's pure
    # retrieval numbers work with no LLM at all; suite 3's generation
    # calls will simply error per-case if Ollama truly isn't reachable):
    python run_full_eval.py --rag-only

    # Retrieval ONLY: embedding + FAISS search, zero LLM calls, zero
    # Ollama/Redis dependency -- the fastest way to check "is retrieval
    # good or bad" on its own:
    python run_full_eval.py --retrieval-only

    # Point at a different host, different queries file, heavier load test:
    python run_full_eval.py --base-url http://localhost:8000 \
        --queries queries.json --concurrency 20 --concurrency-requests 60

    # Sweep a different embedding/LLM config for suites 2/3 only:
    python run_full_eval.py --embedding-model intfloat/multilingual-e5-base \
        --llm-model qwen2.5:3b-instruct --temperature 0.0 --rag-only

Writes to <out-dir>/<timestamp>[_<tag>]/:
    full.json       -- everything, machine-readable
    retrieval.csv   -- one row per query from suite 2 (retrieval-only)
    summary.csv     -- one row per query from suite 3 (full pipeline)
    report.md       -- human-readable report with tables + a pass/fail summary

Exit code is 1 if anything the harness considers a hard failure occurred
(a checked retrieval or RAG case failed, an infra check failed, a smoke
test failed, or the memory round-trip failed) -- SKIPPED suites (server not
reachable, or intentionally skipped via --skip-http/--rag-only/
--retrieval-only) do NOT fail the run.
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
from rag_engine import RagEngine, detect_lang  # noqa: E402


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


def _truncate(text, n):
    text = text or ""
    text = " ".join(text.split())  # collapse newlines/whitespace for table rows
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _md_escape(text):
    # Markdown table cells break on raw pipes/newlines.
    return (text or "").replace("|", "\\|").replace("\n", " ")


# =============================================================================
# Suite 1: infrastructure checks
# =============================================================================

def run_infra_checks(embedding_model, backend, llm_model, rag_only=False):
    """Cheap, ordered checks so a failure points at the actual broken piece
    (missing index vs. Ollama down vs. Redis down) instead of a stack trace
    from three layers deep in RagEngine.answer().

    rag_only -- when True, this is testing retrieval/embedding only (no
    generation, no session memory), so the engine is loaded directly via
    RagEngine(require_llm=False) instead of common.get_engine(), and the
    Redis check is skipped entirely rather than reported as a failure --
    neither Ollama nor Redis is needed for retrieval alone."""
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

    # 1b. Engine load: embedding model + FAISS/backend [+ Ollama ping].
    # Normal mode uses the same lazy-load path app.py's get_engine() uses
    # (and requires Ollama, since suite 3's generation depends on it too).
    # --rag-only constructs RagEngine directly with require_llm=False so
    # retrieval can be evaluated with no Ollama server running at all.
    engine = None
    t0 = time.time()
    label = ("engine load (embedding model + index, Ollama NOT required -- rag-only)" if rag_only
             else "engine load (embedding model + index + Ollama reachable)")
    try:
        if rag_only:
            engine = RagEngine(embedding_model=embedding_model, backend=backend,
                                llm_model=llm_model, require_llm=False)
        else:
            engine = get_engine(embedding_model=embedding_model, backend=backend, llm_model=llm_model)
        add(label, True,
            f"{round(time.time() - t0, 2)}s, {len(engine.docs)} docs, "
            f"{len(engine._offer_merchants)} known merchants")
    except FileNotFoundError as e:
        add(label, False, f"Index not found: {e}")
    except RuntimeError as e:
        add(label, False, f"Ollama unreachable: {e}")
    except Exception as e:
        add(label, False, f"{type(e).__name__}: {e}")

    # 1c. Redis, independently of the engine (memory.py's own connection).
    # Not needed for retrieval-only evaluation (no session memory involved).
    if not rag_only:
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


def _by_group(records, key_fn, ok_fn):
    """Shared helper: buckets records by key_fn(record), counting total/
    checked/passed via ok_fn(record) -> True/False/None (None = unchecked)."""
    groups = {}
    for r in records:
        g = key_fn(r) or "uncategorized"
        groups.setdefault(g, {"total": 0, "checked": 0, "passed": 0})
        groups[g]["total"] += 1
        ok = ok_fn(r)
        if ok is not None:
            groups[g]["checked"] += 1
            if ok:
                groups[g]["passed"] += 1
    return groups


# =============================================================================
# Suite 2: RETRIEVAL-ONLY correctness + latency -- embedding + FAISS
# search + lexical/intent scoring, via engine.retrieve() directly. No LLM
# call happens in this suite at all, which is the point: it isolates
# "did we find/rank the right document(s)" from "did the model write a
# good answer from them", so a retrieval regression and a generation
# regression show up as two different numbers instead of one blurred
# pass/fail. Runs whenever the engine loaded, with or without --rag-only.
# =============================================================================

# Categories that only make sense with real generation (pure refusal/safety
# behavior, or no single "correct" document to rank) are recorded for
# visibility but not scored pass/fail here -- there's no expected_id/
# expected_source ground truth to rank against.
_RETRIEVAL_UNSCORED_CATEGORIES = {
    "out_of_scope", "prompt_injection", "adversarial_input", "faq_not_in_kb",
}


def run_retrieval_suite(engine, queries_path):
    queries_path = Path(queries_path)
    if not queries_path.exists():
        return {"skipped": True, "reason": f"{queries_path} not found", "records": []}

    with open(queries_path, "r", encoding="utf-8") as f:
        cases = json.load(f)

    # Embedding-only micro-benchmark: encode a handful of real queries in
    # isolation (outside of retrieve()'s embed+search+score bundle) to get
    # a clean "how fast is the embedding model by itself" number, and a
    # determinism sanity check (same text in -> ~identical vector out).
    embed_samples = [c["query"] for c in cases if c.get("query")][:15]
    embed_latencies = []
    determinism_cos = None
    try:
        import numpy as np
        for q in embed_samples:
            t0 = time.time()
            engine.embed_model.encode([q], normalize_embeddings=True, convert_to_numpy=True)
            embed_latencies.append(time.time() - t0)
        if embed_samples:
            v1 = engine.embed_model.encode([embed_samples[0]], normalize_embeddings=True,
                                            convert_to_numpy=True).astype("float32")[0]
            v2 = engine.embed_model.encode([embed_samples[0]], normalize_embeddings=True,
                                            convert_to_numpy=True).astype("float32")[0]
            determinism_cos = float(np.dot(v1, v2) / ((np.linalg.norm(v1) * np.linalg.norm(v2)) or 1))
    except Exception as e:
        embed_latencies = []
        determinism_cos = None
        _embed_bench_error = f"{type(e).__name__}: {e}"
    else:
        _embed_bench_error = None

    records = []
    for case in cases:
        query = case.get("query", "")
        lang = case.get("lang") or detect_lang(query)
        expected_source = case.get("expected_source")
        expected_id = case.get("expected_id")
        has_ground_truth = expected_id is not None
        recent_offers = case.get("recent_offers")

        rec = {
            "id": case.get("id"), "category": case.get("category"), "lang": lang,
            "query": query, "expected_source": expected_source, "expected_id": expected_id,
        }
        try:
            t0 = time.time()
            retrieved = engine.retrieve(query, history=None, recent_offers=recent_offers)
            latency = time.time() - t0

            top = retrieved[0] if retrieved else None
            rec.update({
                "latency_s": round(latency, 4),
                "n_retrieved": len(retrieved),
                "top_source": top["metadata"].get("source") if top else None,
                "top_id": top["metadata"].get("id") if top else None,
                "top_score": round(top.get("combined_score", 0.0), 4) if top else None,
            })

            rank = None
            if has_ground_truth:
                for i, r in enumerate(retrieved, start=1):
                    same_source = (expected_source is None
                                   or r["metadata"].get("source") == expected_source)
                    if same_source and str(r["metadata"].get("id")) == str(expected_id):
                        rank = i
                        break
                rec["rank"] = rank
                rec["hit_at_1"] = rank == 1
                rec["hit_at_k"] = rank is not None
                rec["reciprocal_rank"] = (1.0 / rank) if rank else 0.0
                rec["passed"] = rank is not None
            elif expected_source is not None and case.get("category") not in _RETRIEVAL_UNSCORED_CATEGORIES:
                # No specific doc to rank against, but we know which corpus
                # (offer vs faq) the answer should be grounded in -- check
                # that source shows up somewhere in the retrieved set.
                source_hit = any(r["metadata"].get("source") == expected_source for r in retrieved)
                rec["source_hit"] = source_hit
                rec["passed"] = source_hit
            else:
                rec["passed"] = None  # no ground truth to score against (by design)
        except Exception as e:
            rec["error"] = str(e)
            rec["traceback"] = traceback.format_exc(limit=3)
            rec["passed"] = False
        records.append(rec)

    n_total = len(records)
    n_checked = sum(1 for r in records if r.get("passed") is not None)
    n_passed = sum(1 for r in records if r.get("passed") is True)
    n_errors = sum(1 for r in records if "error" in r)
    latencies = [r.get("latency_s") for r in records if "latency_s" in r]
    reciprocal_ranks = [r["reciprocal_rank"] for r in records if "reciprocal_rank" in r]
    hit1 = [r for r in records if "hit_at_1" in r]
    hitk = [r for r in records if "hit_at_k" in r]

    by_category = _by_group(records, lambda r: r.get("category"), lambda r: r.get("passed"))
    by_lang = _by_group(records, lambda r: r.get("lang"), lambda r: r.get("passed"))

    return {
        "skipped": False,
        "records": records,
        "n_total": n_total,
        "n_checked": n_checked,
        "n_passed": n_passed,
        "n_errors": n_errors,
        "hit_at_1_rate": round(sum(1 for r in hit1 if r["hit_at_1"]) / len(hit1), 3) if hit1 else None,
        "hit_at_k_rate": round(sum(1 for r in hitk if r["hit_at_k"]) / len(hitk), 3) if hitk else None,
        "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 3) if reciprocal_ranks else None,
        "retrieval_latency_stats": _stats(latencies),
        "embedding_only_latency_stats": _stats(embed_latencies),
        "embedding_determinism_cosine": round(determinism_cos, 6) if determinism_cos is not None else None,
        "embedding_bench_error": _embed_bench_error,
        "embedding_model": engine.embedding_model_name,
        "by_category": by_category,
        "by_lang": by_lang,
        "all_ok": n_errors == 0 and n_passed == n_checked,
    }


# =============================================================================
# Suite 3: RAG correctness + latency, FULL PIPELINE (in-process, via
# common.run_one) -- retrieval AND generation together, i.e. what the
# user actually sees. Compare against Suite 2's retrieval-only numbers to
# tell "retrieval found the right doc but the model wrote a bad answer
# from it" apart from "retrieval itself surfaced the wrong doc".
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
        # NEW: tag every record with the query's language (from the case
        # itself if present, else auto-detected) so pass rates and latency
        # can be broken out by language regardless of what common.run_one's
        # record shape does or doesn't carry.
        rec["lang"] = case.get("lang") or detect_lang(case.get("query", ""))
        # NEW: guarantee "query" is always present on the record regardless
        # of whether common.run_one's own return dict includes it, so
        # summary.csv and the markdown report can always show the question.
        rec.setdefault("query", case.get("query", ""))
        records.append(rec)

    n_total = len(records)
    n_checked = sum(1 for r in records if r.get("passed") is not None)
    n_passed = sum(1 for r in records if r.get("passed") is True)
    n_errors = sum(1 for r in records if "error" in r)
    n_leaks = sum(1 for r in records if r.get("scaffolding_leak"))
    latencies = [r.get("latency_s") for r in records if "latency_s" in r]

    by_category = _by_group(records, lambda r: r.get("category"), lambda r: r.get("passed"))
    by_lang = _by_group(records, lambda r: r.get("lang"), lambda r: r.get("passed"))

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
        "by_lang": by_lang,
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
    fields = ["id", "category", "lang", "query", "passed", "top_source", "top_id", "top_score",
              "scaffolding_leak", "latency_s", "answer", "error"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)


def write_retrieval_csv(records, path):
    fields = ["id", "category", "lang", "query", "expected_source", "expected_id",
              "top_source", "top_id", "top_score", "rank", "hit_at_1", "hit_at_k",
              "reciprocal_rank", "passed", "latency_s", "error"]
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
    a(f"**Mode:** {'RAG-only (retrieval, no generation, no HTTP)' if report['env'].get('rag_only') else 'Full'}  ")
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

    # --- Retrieval-only suite ---
    retrieval = report.get("retrieval")
    a("## 2. Retrieval-only correctness & latency (embedding + search, NO generation)")
    a("")
    a("_Isolates the retriever from the LLM: calls `engine.retrieve()` directly, so a low "
      "score here means the embedding/search/ranking layer itself is at fault, independent "
      "of anything the model does with what it's given._")
    a("")
    if retrieval is None:
        a("_Skipped._")
        a("")
    elif retrieval.get("skipped"):
        a(f"_Skipped: {retrieval['reason']}_")
        a("")
    else:
        a(f"- **Cases run:** {retrieval['n_total']} (**{retrieval['n_checked']}** had ground truth to score)")
        a(f"- **Passed:** {retrieval['n_passed']}/{retrieval['n_checked']}")
        a(f"- **Errors:** {retrieval['n_errors']}")
        a(f"- **Hit@1:** {retrieval['hit_at_1_rate']}  |  **Hit@k:** {retrieval['hit_at_k_rate']}  |  **MRR:** {retrieval['mrr']}")
        a(f"- **Embedding model:** `{retrieval['embedding_model']}`")
        a(f"- **Embedding determinism (cosine, same query encoded twice):** {retrieval['embedding_determinism_cosine']}")
        a("")
        a("**Latency**")
        a("")
        a("| Metric | N | Min | Mean | Median | p95 | Max |")
        a("|---|---|---|---|---|---|---|")
        a(_fmt_stats_row("Retrieval (embed + search + score)", retrieval["retrieval_latency_stats"]))
        a(_fmt_stats_row("Embedding only (encode call alone)", retrieval["embedding_only_latency_stats"]))
        a("")
        a("**By category**")
        a("")
        a("| Category | Total | Checked | Passed |")
        a("|---|---|---|---|")
        for cat, s in sorted(retrieval["by_category"].items()):
            a(f"| {cat} | {s['total']} | {s['checked']} | {s['passed']} |")
        a("")
        a("**By language**")
        a("")
        a("| Language | Total | Checked | Passed |")
        a("|---|---|---|---|")
        for lang, s in sorted(retrieval["by_lang"].items()):
            a(f"| {lang} | {s['total']} | {s['checked']} | {s['passed']} |")
        a("")
        failed = [r for r in retrieval["records"]
                  if (r.get("passed") is False) or "error" in r]
        if failed:
            a("**Failed / errored retrieval cases**")
            a("")
            a("| id | category | lang | question | expected | got (top-1) | rank | issue |")
            a("|---|---|---|---|---|---|---|---|")
            for r in failed[:30]:
                question = _md_escape(_truncate(r.get("query", ""), 100))
                expected = f"{r.get('expected_source')}:{r.get('expected_id')}"
                got = f"{r.get('top_source')}:{r.get('top_id')} (score={r.get('top_score')})"
                issue = f"ERROR: {r['error']}" if "error" in r else "not in retrieved set"
                a(f"| {r.get('id')} | {r.get('category')} | {r.get('lang')} | {question} | {expected} | {got} | {r.get('rank')} | {issue} |")
            if len(failed) > 30:
                a(f"| … | | | | | | | +{len(failed) - 30} more, see full.json |")
            a("")

    # --- RAG suite (full pipeline) ---
    rag = report["rag"]
    a("## 3. RAG correctness & latency, full pipeline (retrieval + generation, in-process)")
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
        a(_fmt_stats_row("Per-query latency (retrieval + generation)", rag["latency_stats"]))
        a("")
        a("**By category**")
        a("")
        a("| Category | Total | Checked | Passed |")
        a("|---|---|---|---|")
        for cat, s in sorted(rag["by_category"].items()):
            a(f"| {cat} | {s['total']} | {s['checked']} | {s['passed']} |")
        a("")
        a("**By language**")
        a("")
        a("| Language | Total | Checked | Passed |")
        a("|---|---|---|---|")
        for lang, s in sorted(rag["by_lang"].items()):
            a(f"| {lang} | {s['total']} | {s['checked']} | {s['passed']} |")
        a("")
        failed = [r for r in rag["records"] if r.get("passed") is False or "error" in r]
        if failed:
            a("**Failed / errored cases (question + answer)**")
            a("")
            a("| id | category | lang | question | answer | issue |")
            a("|---|---|---|---|---|---|")
            for r in failed[:30]:
                question = _md_escape(_truncate(r.get("query", ""), 120))
                if "error" in r:
                    answer = "_(no answer -- errored)_"
                    issue = f"ERROR: {r['error']}"
                else:
                    answer = _md_escape(_truncate(r.get("answer", ""), 200)) or "_(empty)_"
                    failed_checks = [k for k, v in r.get("checks", {}).items() if not v]
                    issue = f"failed checks: {', '.join(failed_checks)}"
                a(f"| {r.get('id')} | {r.get('category')} | {r.get('lang')} | {question} | {answer} | {issue} |")
            if len(failed) > 30:
                a(f"| … | | | | | +{len(failed) - 30} more, see full.json |")
            a("")

    # --- Smoke suite ---
    smoke = report.get("smoke")
    a("## 4a. Server smoke test")
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
    a("## 4b. Concurrency / load test")
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
    a("## 5. Session memory round-trip")
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
                         help="Skip suites 4a/4b/5 entirely (no server required)")
    parser.add_argument("--rag-only", action="store_true",
                         help="Test the RAG side only: retrieval (embedding + FAISS search) "
                              "always runs, and this also runs the full retrieval+generation "
                              "pipeline suite -- but skips everything server/session-related "
                              "(suites 4a/4b/5) and does NOT require Ollama or Redis to be up "
                              "at all. Use --rag-only --queries ... for a fast, dependency-free "
                              "check of the retrieval layer plus in-process generation, with no "
                              "server, no Redis, and (for the pure retrieval numbers) no Ollama "
                              "needed either. Implies --skip-http.")
    parser.add_argument("--retrieval-only", action="store_true",
                         help="Like --rag-only, but ALSO skips the full generation suite (3), "
                              "leaving just the pure retrieval/embedding suite (2). The fastest, "
                              "most isolated way to answer 'is retrieval good or bad' with zero "
                              "LLM calls and no Ollama/Redis dependency at all.")
    parser.add_argument("--concurrency", type=int, default=8,
                         help="Max simultaneous /api/chat requests in the load test")
    parser.add_argument("--concurrency-requests", type=int, default=24,
                         help="Total requests fired in the load test")
    parser.add_argument("--out-dir", default="results")
    parser.add_argument("--tag", default=None)
    args = parser.parse_args()

    # --retrieval-only implies --rag-only implies --skip-http.
    if args.retrieval_only:
        args.rag_only = True
    if args.rag_only:
        args.skip_http = True

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = f"{ts}_{args.tag}" if args.tag else ts
    out_dir = Path(args.out_dir) / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Waffarha Assistant full evaluation -- {ts} ===")
    if args.retrieval_only:
        print("Mode: --retrieval-only (embedding + search only, no LLM, no server)")
    elif args.rag_only:
        print("Mode: --rag-only (retrieval + in-process generation, no server, no Redis)")
    print(f"Output: {out_dir}/\n")

    # ---- Suite 1: infra ----
    print("[1/5] Infrastructure checks...")
    infra = run_infra_checks(args.embedding_model, args.backend, args.llm_model,
                              rag_only=args.rag_only)
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
            "rag_only": args.rag_only,
            "retrieval_only": args.retrieval_only,
        },
        "infra": infra,
    }

    # ---- Suite 2: retrieval-only (needs the engine from suite 1; no LLM call) ----
    if engine is not None:
        print("\n[2/5] Retrieval-only correctness + latency (embedding + search, no LLM)...")
        retrieval = run_retrieval_suite(engine, args.queries)
        if retrieval.get("skipped"):
            print(f"      skipped: {retrieval['reason']}")
        else:
            print(f"      {retrieval['n_passed']}/{retrieval['n_checked']} checked cases passed "
                  f"({retrieval['n_total']} total, {retrieval['n_errors']} errors) -- "
                  f"hit@1={retrieval['hit_at_1_rate']} hit@k={retrieval['hit_at_k_rate']} mrr={retrieval['mrr']}")
    else:
        retrieval = {"skipped": True, "reason": "engine failed to load, see infra checks above", "records": []}
        print("\n[2/5] Retrieval-only -- SKIPPED (engine did not load)")
    report["retrieval"] = retrieval

    # ---- Suite 3: RAG correctness, full pipeline (retrieval + generation) ----
    if args.retrieval_only:
        rag = {"skipped": True, "reason": "--retrieval-only: full generation suite skipped", "records": []}
        print("\n[3/5] RAG correctness (full pipeline) -- SKIPPED (--retrieval-only)")
    elif engine is not None:
        print("\n[3/5] RAG correctness + latency, full pipeline (queries.json, in-process)...")
        if not engine.require_llm:
            # --rag-only without --retrieval-only still wants generation, but the
            # engine above was loaded with require_llm=False (no Ollama check) --
            # if Ollama genuinely isn't reachable, generation calls below will
            # raise naturally per-case and show up as errored records rather
            # than crashing the whole suite.
            pass
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
        print("\n[3/5] RAG correctness (full pipeline) -- SKIPPED (engine did not load)")
    report["rag"] = rag

    # ---- Suites 4a/4b/5: HTTP ----
    smoke = conc = mem = None
    if not args.skip_http:
        print(f"\n[4/5] Server smoke test against {args.base_url} ...")
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

            print("\n[5/5] Session memory round-trip test ...")
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
            conc = {"skipped": True, "reason": "server not reachable, see 4a"}
            mem = {"skipped": True, "reason": "server not reachable, see 4a"}
            print("\n[5/5] Session memory round-trip -- SKIPPED (server not reachable)")
    else:
        reason = "--rag-only" if args.rag_only else "--skip-http"
        print(f"\n[4/5] Server smoke test -- SKIPPED ({reason})")
        print(f"[5/5] Session memory round-trip -- SKIPPED ({reason})")

    report["smoke"] = smoke
    report["concurrency"] = conc
    report["memory"] = mem

    # ---- Roll up pass/fail ----
    def suite_status(s, ok_key="all_ok"):
        if s is None:
            return "⏭️ skipped (--skip-http/--rag-only)"
        if s.get("skipped"):
            return f"⏭️ skipped ({s.get('reason', '')})"
        return _badge(s.get(ok_key, False))

    suites_status = {
        "1. Infrastructure": _badge(infra["all_ok"]),
        "2. Retrieval-only": ("⏭️ skipped" if retrieval.get("skipped") else _badge(retrieval["all_ok"])),
        "3. RAG correctness (full pipeline)": ("⏭️ skipped" if rag.get("skipped") else _badge(rag["all_ok"])),
        "4a. Server smoke": suite_status(smoke),
        "4b. Concurrency": suite_status(conc),
        "5. Session memory": suite_status(mem),
    }
    report["suites_status"] = suites_status

    hard_failures = [infra["all_ok"] is False]
    if not retrieval.get("skipped"):
        hard_failures.append(retrieval["all_ok"] is False)
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

    if not retrieval.get("skipped"):
        write_retrieval_csv(retrieval["records"], out_dir / "retrieval.csv")
    if not rag.get("skipped"):
        write_csv(rag["records"], out_dir / "summary.csv")

    write_markdown(report, out_dir / "report.md")

    print(f"\n=== Overall: {_badge(overall_ok)} ===")
    print(f"Results written to {out_dir}/")
    print(f"  - {out_dir / 'full.json'}")
    if not retrieval.get("skipped"):
        print(f"  - {out_dir / 'retrieval.csv'}")
    if not rag.get("skipped"):
        print(f"  - {out_dir / 'summary.csv'}")
    print(f"  - {out_dir / 'report.md'}")

    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()