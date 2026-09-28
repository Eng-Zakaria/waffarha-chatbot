# Phase 0 -- Architecture Assessment (Waffarha Chatbot)

Inspection-only deliverable. No production code was modified. Baseline tests
run against `venv` (Python 3.11.4) with pytest installed for this purpose.

---

## CURRENT SYSTEM

A bilingual (Arabic/English, incl. Franco-Arabic) RAG chatbot that answers
catalog, FAQ, and per-user coupon questions through a single large
`RagEngine` (core/rag_engine.py, 3923 lines) exposed over FastAPI.

### Serving layer (core/app.py, 763 lines)
- `POST /api/chat` (JSON) and `POST /api/chat/stream` (Server-Sent Events),
  both returning `{answer, type, offers[], sources[], suggestions[]}`.
- `GET /api/health` (liveness, no engine load), `GET /api/session/{sid}/history`.
- Static widget served from `static/` at `/`.
- Lazy, double-checked-locked singleton `RagEngine` (built off the event loop
  via `asyncio.to_thread`); startup warm-up (`app.on_event("startup")`).
- Generation concurrency cap: `asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)`
  (default 4) with a `GENERATION_QUEUE_TIMEOUT` (30s) -> 503 + Retry-After.
- Session memory is best-effort (Redis outage degrades, never fails the request).

### Query pipeline (core/rag_engine.py, 3923 lines)
The main entry is `answer_stream()` (core/rag_engine.py:3454). Deterministic
short-circuits run in this order, before hybrid retrieval:

1. Closing-phrase / greeting / gibberish / prompt-injection detection.
2. User-query sanitization (strips scaffolding labels).
3. Arabizi/Franco-Arabic normalization (`normalize_arabizi_and_arabic`).
4. Personal queries (`my coupons`, scoped to resolved `user_id`) -> live
   ClickHouse `fct_coupons` (personal/personal_queries.py), gated on
   `PERSONAL_QUERIES_ENABLED` + resolved identity.
5. Live catalog queries (KFC offers / under N EGP / cheapest / branch location)
   -> ClickHouse `dim_offers` + `dim_partners` + `dim_type_price`
   (catalog/catalog_queries.py, 929 lines), gated on `CATALOG_QUERIES_ENABLED`.
6. Unknown / inactive-merchant negatives (`_unmatched_brand_mention`,
   `_inactive_merchant_mention`) and out-of-scope guardrails
   (`_looks_like_out_of_scope` + `check_out_of_scope_guardrail`).
7. Anchored follow-up answers -- built ONLY from offers already shown this
   session (session memory), zero retrieval (`_followup_anchored_answer`).
8. Superlatives ("cheapest / most expensive / max discount") -- exhaustive
   sort over the full in-memory catalog (`_get_superlative_offer_answer`).
9. Faceted structured-first routing (`_faceted_answer`, core/faceted.py):
   merchant / category / product / price-range slices with deterministic
   refusals.
10. Deterministic FAQ topic router → direct FAQ answer.
11. Hybrid retrieval (embedding + BM25, RRF merge) -> scoring with intent /
    lexical / entity / title / title-price bonuses vs
    `MIN_RELEVANCE_SCORE_STRICT`, optional LLM relevance pre-check
    (`_context_is_relevant`) between 0.45–0.55.
12. Deterministic offer-card path (real metadata only) + LLM intro line, or
    full LLM generation over CONTEXT with a REQUIRED FACTS fact-checklist and
    post-hoc fact verification (appends a "📋 Details" correction line).

### Storage / embeddings
- Vector indexes under `data/index/<embedding_model>/<backend>/`. Present:
  `BAAI__bge-m3` (faiss + qdrant, incl. stale `qdrant/.lock`), and
  `sentence-transformers__paraphrase-multilingual-mpnet-base-v2` (faiss).
  Bench corpora for e5 models in `data/bench/`. **No index exists for the
  default `.env.example` model `intfloat/multilingual-e5-large`.**
- `vectorstores/vectorstores.py`: backend-agnostic contract -- all backends
  return cosine similarity in [-1, 1] (LanceDB/pgvector convert distance
  back in `search()`). Backends: faiss, qdrant, chroma, lancedb, pgvector.
- `core/embedding_providers.py`: sentence-transformers (default), `ollama:`
  prefix (batched, POST `{OLLAMA_HOST}/api/embed`), `jina:` prefix.
- `core/config.py`: the runtime default suite is `BAAI/bge-m3` +
  `qdrant` (embedded, folder mode). All thresholds (0.30 soft / 0.45 strict /
  0.55 relevance check / 0.50 risk log, direct-answer margins, hybrid BM25
  weights) tune here.

### Identity & memory
- `core/identity.py`: `IDENTITY_BACKEND` = `static` (demo-only) | `header`
  (trusted `X-User-ID` from auth proxy) | `session` (Redis). The resolver is
  the ONLY trusted source of `user_id`; `AuthBackedIdentityResolver` is a
  documented TODO, and it is the only production-safe option.
- `memory/memory.py`: server-side session memory (10 recent turns, last 4
  shown offers/FAQs per session), Redis-backed; `MEMORY_BACKEND=local`
  in-process fallback for dev.

### Ingestion
- Mobile-API scrape (`ingestion/sources/fetch_offers.py`) vs newer ClickHouse
  loaders (`fetch_offers_clickhouse.py`, `fetch_partners_clickhouse.py`,
  `fetch_payment_methods_clickhouse.py`, `fetch_purchasing_status_clickhouse.py`,
  `fetch_type_price_clickhouse.py`).
- Index builders: `ingestion/loaders/build_index.py`,
  `ingestion/loaders/build_index_incremental.py`.

### Evaluations & reports present
- `eval/`: run_eval.py + queries.json (2553 lines), conversation_test_runner,
  manual_evaluation, bench_embeddings, bench_llm_configs (referenced),
  several Claude/qwen comparison scripts, and many dated result JSONs.
- Root HAS BEEN INFESTED with report docs: EVAL_REPORT.md, EVALUATION_REPORT.md,
  CONCURRENCY_REPORT.md, HALLUCINATION_ANALYSIS.md, PERFECTION_REPORT.md,
  PRODUCTION_SCALE.md, plus build logs and temp JSON in the repo root.

---

## WHAT WORKS

- Fast, deterministic guardrail/"no" layer: greetings, gibberish, injections,
  out-of-scope, unknown/inactive brands -- verified by
  tests/unit/test_hallucination_guards.py (asserts against
  `_looks_like_*`), and mirrored in the flow of `answer_stream()`.
- Arabizi normalization + intent classification (core/rag_perfection.py),
  exercised by tests/unit/test_faq_routing.py etc.
- FacetedCatalog (core/faceted.py): merchant aliasing (EN/AR + canonical
  chains, sub-token containment fix), phantom/offer-less merchants, category
  lexicon, product diversity caps, deterministic superlatives, partners
  snapshot identity (`status`/`part_id`), price-filtering. Covered by
  tests/unit/test_faceted_routing.py (all determinism tests pass).
- Anchored follow-up layer: comparison / cheaper-than / other-offer resolved
  from shown-offer memory with a stub LLM, zero retrieval
  (tests/unit/test_followup_anchoring.py -- all pass, deterministic).
- Session-scoped memory with clean degradation when Redis is down; lazy
  memory/engine init so `/api/health` responds without the heavy stack.
- Generation concurrency cap with honest 503 + Retry-After (no invisible
  queueing inside Ollama).
- Deterministic offer-card rendering from real metadata + LLM intro, so the
  model does not fabricate prices for card answers; fact-checklist correction
  line after free-form generation; scaffolding-leak stripping in `answer()`.
- Clean abstraction contracts: vector-store cosine contract, embedding
  provider prefix routing, identity boundary. `_has_value()` correctly keeps
  falsy-but-real facts (e.g. discount=0).

## WHAT IS BROKEN

- **Domain-coupling of the request body**: `ChatRequest.session_id` defaults
  to `"default"`; the docstring itself warns two real users would see each
  other's remembered offers (core/app.py:74-80). The frontend is expected to
  send a per-user id but there is no enforcement or auth boundary for it.
- **Identity backends are unsafe for production**: only `static` / `header`/
  `session` exist today; `header` trusts an HTTP header with no signature
  verification documented, and the production `auth` backend is unimplemented
  (core/identity.py). `PERSONAL_QUERIES_ENABLED` defaults to true, so the app
  will leak/extrapolate per-user coupon data under the demo `static` backend
  (config default `IDENTITY_BACKEND=static`) if shipped as-is.
- **`session/session_manager.py` imports `redis` unconditionally** (no
  try/except like core/app.py) and builds the Redis client in `__init__`; the
  catalog comparison path instantiates `SessionManager()` lazily. With
  `MEMORY_BACKEND=local` and no Redis, any code path touching
  `SessionManager` (catalog comparisons) raises on first use.
- **Test suite that needs modifying does not run concurrently**: the heavy
  unit files (test_hallucination_guards.py, test_followup_detection.py)
  construct a full RagEngine per test via `RagEngine.__new__(...)`;
  test_hallucination_guards's function-scoped `engine` fixture plus model
  load makes a full `pytest tests/unit` exceed many minutes. With a cached
  BGE-M3 it still takes several engine loads: cold load is ~47s each.
- **`_price` coercion / price parsing relies on regex digit stripping and
  locale assumptions** (EGP thousands separators "1,200", Arabic-Indic digits
  in DMS), a fragile spot for price-range filtering.
- **`.env.example` documents `intfloat/multilingual-e5-large` as the default
  embedding model but only bge-m3 and mpnet indexes exist on disk**, so
  following `.env.example` verbatim produces a hard "Index not found" until
  an index is built.
- **Auto-close (Qdrant) teardown bug**: on process shutdown the embedded
  Qdrant client `__del__` raises `ModuleNotFoundError: import of msvcrt
  halted; None in sys.modules` (observed on every RagEngine exit in this
  environment). Cosmetic today but noisy in logs/tests.
- **Stale embedded-Qdrant `.lock` in `data/index/BAAI__bge-m3/qdrant/`**
  from a previous run; if a process is still alive it will refuse to open
  ("already accessed by another instance").
- **Gap between direct-answer thresholds and reality**: comments in the code
  (core/rag_engine.py:3797-3804) record that the single-offer direct-answer
  shortcut fired on `combined_score` inflation and had to be disabled by
  design; the direct-answer shortcut is now dead/disabled code that still
  ships.
- **The report/docs area is unmaintained**: 6+ root-level markdown reports and
  several `eval/*.json` snapshots have overlapping/inconsistent numbers
  (e.g. retrieval pass 80% in EVALUATION_REPORT.md vs 58.1% in EVAL_REPORT.md,
  full-RAG pipeline 4.1% at the time of EVALUATION_REPORT.md). They represent
  different runs at different times and cannot be reconciled to one number
  from the current tree.
- **A scratch/journal artifact `session-ses_f7fc.md` and `_tmp_*.json` are
  committed to the repo root** -- repo hygiene issue.
- **Ingestion has TWO competing sources** (mobile-API `fetch_offers.py` vs
  ClickHouse loaders) plus `ingestion/sources/get_customers_offers.py`; the
  interplay, lineage, and refresh cadence are undocumented and unclear.

## WHAT SHOULD STAY

- The layered deterministic guardrails ("no"-answers before retrieval) --
  greetings, gibberish, injections, out-of-scope, unknown/inactive brands.
- FacetedCatalog structured-first routing over self.docs (offline, fast,
  deterministic) as the default offer/module for catalog questions at chat time.
- The anchored follow-up layer (session-memory-resolved follow-ups), including
  its deterministic gate that avoids LLM calls on obvious references.
- The vector-store cosine-similarity contract + embedding provider abstraction
  (prefix-routing) + identity resolver boundary pattern.
- Session memory with graceful Redis degradation and lazy init.
- Hybrid BM25+embedding retrieval with score thresholds and LLM-only "relevance
  check" between 0.45 and 0.55 as a last-resort gate.
- Deterministic offer-card rendering, `_has_value` falsy handling,
  scaffolding-leak stripping, and the fact-checklist correction line.
- Generation concurrency cap with timeout + Retry-After.

## WHAT SHOULD CHANGE

- Identity: implement `AuthBackedIdentityResolver` and make the request-level
  `user_id`/`session_id` source an explicit, tested boundary; never trust
  client-supplied `session_id` for personal memory isolation.
- Session/compare layer: unify `memory.py` and `session/session_manager.py`
  behind one Redis-optional backend; removing the unconditional `import redis`
  and connection-on-`__init__` semantics (mirror core/app.py's pattern).
- Re-center the enginel logic: split `core/rag_engine.py` (3923 lines) into
  dispatch/guard, retrieval, follow-up, and rendering modules once incremental
  refactors are safe; keep the deterministic guards in one place.
- Make the direct-answer shortcut either active again with honest thresholds
  based on evidence, or delete it explicitly (avoid dead code paths that
  reference scoring semantics that no longer exist).
- Pytest test hygiene: use `RagEngine.__new__` stubs consistently (like
  test_followup_anchoring.py) for unit tests that do not need the index, and
  create an `integration/` test suite for the heavy real-index cases that runs
  once per CI instead of per-test.
- Ingestion: pick the ClickHouse source as canonical, provide a single
  refresh entry point with lineage + cadence, and rebuild indexes
  deterministically, writing a manifest (model/backend/data hash) next to each
  index.
- Config consistency: make `.env.example` match real prebuilt indexes (or
  document the build step for e5/large) so "follow the README" cannot produce
  an "Index not found".
- Repo hygiene: move reports into a `reports/` dir (or archive), remove scratch
  browser artifacts from the root, and commit real history.

## WHAT IS MISSING

- **Production identity/auth backend** (the single biggest missing piece for
  personal data).
- **A CI pipeline / single baseline test command** that runs fast, plus tests
  for ingestion (index builders) and the api/routes layer (both packages are
  empty; all API logic lives in core/app.py with essentially zero tests).
- **Integration tests**: `tests/integration/` exists but is empty.
- **An end-to-end latency/cost budget profile**: no automated latency budget
  tracked across runs (only per-run reports).
- **Observability**: no structured metrics/OTel for retrieval scores,
  generation latency, 503 rates, guard-hit counts, or fact-correction counts.
- **A canonical evaluation runner + dataset with versioned golden answers**
  (queries.json exists but results across files are from different snapshots
  and not reproducible in one command).
- **Qdrant-vs-FAISS tradeoff documentation and lock/close handling** for
  embedded Qdrant in multi-worker deployments.
- **Security**: a README-visible threat model for injection/evasion, and
  rate-limiting/abuse protection beyond the generation semaphore (no auth on
  /api/chat, CORS limited but `session_id` unauthenticated).

## RISK AREAS

- **Hallucination via mixed retrieval paths**: catalog/personal/faceted paths
  return answers without passing the RAG score floor; bugs in intent detection
  there bypass the thickness of the guardrails built for the main path.
- **The 955-row `config.py` + giant `rag_engine.py` coupling**: changes that
  add threshold/flag across both are hard to review; the docstrings already
  document subtle ordering-dependent behavior ("ORDER MATTERS" in
  catalog_queries.py, intent-pattern ordering).
- **Score inflation**: `combined_score` includes ~ +0.5 bonus (intent 0.18 +
  entity 0.40 + title 0.50 + price 0.60) -- exceeded thresholds in the past
  and caused the single-card direct answer bug; future threshold tuning must
  account for bonuses by construction, not by margin.
- **Prompt-injection / refusing overlay**: RAG context includes user-echoed
  content; hierarchy line + sanitization + `_looks_like_injection_attempt`
  are defense-in-depth but the system prompt is the last line of defense.
- **Embedded Qdrant**: single-process lock, `.lock` files, and `__del__`
  teardown errors are a sharp edge in tests and in any multi-worker setup.
- **`.env` carries real ClickHouse credentials** (clickhouse-test.waffarha.tech
  referenced in .env.example) -- must stay git-ignored and the DB user must be
  read-only (config.py:57-65 states the intent but not enforcement).
- **Competing data sources** (scrape vs ClickHouse) can silently diverge the
  static index from the live catalog; price/expiry freshness drives most
  hallucination risk on offers.
- **Local-VS-deployed model drift**: OLLAMA_MODEL default mismatch
  (config: `qwen2.5:3b`; .env.example: `qwen2.5:3b-instruct`; app docstring
  example: `qwen2.5:1.5b-instruct`) makes behavior hard to reproduce across
  environments.

## PERFORMANCE BASELINE (measured in this environment, Windows, venv/Python 3.11.4)

- Cold RagEngine init (embedding model BAAI/bge-m3 on CPU + embedded Qdrant
  index open): **~47.1s** (single measurement).
- Node count on disk: **9,142 docs**, **2,954 offer merchants** in the index.
- Fast unit subset (tests/unit/test_faq_routing.py,
  test_faceted_routing.py, test_followup_anchoring.py): **36/36 passed in
  1.92s** (deterministic, no model/no Ollama).
- Heavy unit files (test_hallucination_guards.py, test_followup_detection.py):
  build a real RagEngine per test; a full `pytest tests/unit` exceeds ~4min
  and did not finish within the run budget here (blocked by repeated model
  loads). Their assertions are config/guard-level and would benefit from
  `__new__`-style stubs (collection of all 81 tests takes 2.44s).
- Ollama is reachable at localhost:11434 in this environment; BGE-M3 is
  cache-hit (no download observed).
- Historical reported numbers (unverifiable from a single command, different
  snapshots): retrieval hit rate 80.0% / Hit@1 57.6% / Hit@k 68.2%
  (EVALUATION_REPORT.md); a later run 122/210 passed = 58.1% (EVAL_REPORT.md);
  full-RAG pass 6/145 = 4.1% at the time of EVALUATION_REPORT.md. These need
  re-baselining from a fixed checkout + one command.

## PROPOSED IMPLEMENTATION ORDER

1. **Phase 1 -- Repo hygiene & reproducible baseline**: fix .env.example,
   remove root scratch files, archive reports, standardize model naming,
   add a single `python -m pytest` command that runs fast (stub heavy tests),
   and a `make benchmark` style eval command with versioned golden answers.
2. **Phase 2 -- Identity & session hardening**: implement
   `AuthBackedIdentityResolver`; make `session_id` resolution enforced;
   unify memory.py/session_manager.py behind one Redis-optional backend.
3. **Phase 3 -- Retrieval correctness re-baseline**: rerun queries.json under a
   fixed checkout, fix score-inflation accounting in direct-answer logic,
   align catalog/personal/faceted paths with the RAG score floor, and add
   integration tests for the real index.
4. **Phase 4 -- Ingestion canonicalization**: collapse to one ClickHouse-based
   pipeline, add index manifest + rebuild script + cadence docs.

**Phase status (as of 2026-09-14):**
- **Phase 1-3: complete and delivered** (Phases 1-2 per their phase reports;
  Phase 3 = retrieval-correctness re-baseline, see reports/phase3/).
- **Phase 4: complete.** One pipeline: `ingestion/refresh.py` (fetch from
  ClickHouse + full/incremental build, `--plan` lineage report,
  `data/refresh_log.json`). Manifest: `ingestion/loaders/index_manifest.py`,
  written by both builders into every `data/index/<model>/<backend>/` dir, read
  back by RagEngine at load (warn-only on model/backend mismatch). Cadence docs:
  `docs/INGESTION.md`. The mobile-API scrape (`fetch_offers.py`) and the
  dead replication tool (`sync_clickhouse.py`/`clickhouse_sync/`) were removed,
  along with their config surface (`WAFFARHA_SECURITY_KEY`/`get_security_key`,
  `OFFERS_API_URL`, `OFFERS_API_BASE_BODY`, `CATEGORY_IDS` in `core/config.py`;
  `docker-compose.yml` no longer requires the key). Verified: fast suite
  106 passed, 8 new manifest unit tests, sample build wrote a manifest with
  matching corpus_hash, `refresh.py --plan` works offline.
5. **Phase 5 -- Observability & scale**: metrics for retrieval scores/latency/
   503/guard/fact-correction; expose budget, replace embedded Qdrant with a
   server deployment (or wrap with correct lock/close handling); generate
   concurrency test (the repo has concurrency-report.html committed as
   evidence of past manual load testing).
6. **Phase 6 -- Model/config converge**: pin OLLAMA_MODEL + EMBEDDING_MODEL
   across envs via documented env template, so a reproducible local dev ==
   prod behavior.

---

### Verification notes
- Python: 3.11.4 venv. `pytest` was pip-installed into `venv` for this phase
  (was absent). Git state: master, `docs/requirements.txt` modified, many
  untracked artifacts.
- All claims marked "unverifiable from a single command" come from the
  committed report docs, not from a current-tree run.