# Waffarha Assistant — RAG-Powered Customer Support Chatbot

A production-ready customer support chat widget for [Waffarha](https://waffarha.com), Egypt's leading deals platform. Combines **RAG (Retrieval-Augmented Generation)** over a curated knowledge base with **live ClickHouse queries** for personal user data (coupons, orders, spending).

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Bilingual Support** | English & Arabic (RTL-aware UI, multilingual embeddings) |
| **Smart Retrieval** | Hybrid vector (FAISS) + lexical (BM25) search with intent classification |
| **Direct Answers** | Template-based shortcuts for FAQs & offers (no LLM call when confident) |
| **Personal Queries** | Live ClickHouse lookups for "my coupons", "order status", spending |
| **Session Memory** | Redis/local memory of shown offers/FAQs for follow-up resolution |
| **Multiple Vector Backends** | FAISS (default), Chroma, Qdrant, LanceDB, pgvector — swap via config |
| **Evaluation Harness** | 100+ test cases with CI-ready pass/fail exit codes |
| **Docker-First Deploy** | Model-baked images, zero-runtime network dependencies |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        static/index.html                             │
│              (Chat Widget — vanilla JS, no build step)               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ POST /api/chat
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          app.py (FastAPI shim)                       │
│  • Re-exports core.app (see core/app.py)                             │
│  • CORS, static file serving, /api/chat, /api/chat/stream            │
│  • Concurrency control (generation semaphore)                        │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌─────────────────┐ ┌─────────────┐ ┌───────────────┐
│   RagEngine     │ │  Identity   │ │   Memory      │
│  (core/rag_     │ │  Resolver   │ │  (memory.py)  │
│   engine.py)    │ │(core/identity│ │               │
│                 │ │ .py)         │ │ • Redis/local │
│ • Vector search │ │             │ │ • Session-scoped│
│ • Intent class  │ │ • Static    │ │ • Shown items │
│ • Direct answer │ │ • Header    │ │ • 1hr TTL     │
│ • Fact-check    │ │ • Session   │ │               │
│ • LLM fallback  │ │             │ │               │
└────────┬────────┘ └─────────────┘ └───────────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌──────────┐
│Vector │ │  ClickHouse│
│Store  │ │ (personal) │
└───────┘ └──────────┘
```

---

## Module Reference

| File | Purpose | Key Exports |
|------|---------|-------------|
| `app.py` | FastAPI shim — re-exports `core.app` for `uvicorn app:app` | `app` |
| `core/app.py` | FastAPI server, `/api/chat` + `/api/chat/stream` endpoints | `app`, `ChatRequest`, `ChatResponse` |
| `core/config.py` | All runtime configuration via env vars | `EMBEDDING_MODEL`, `OLLAMA_MODEL`, `MIN_RELEVANCE_SCORE`, etc. |
| `core/rag_engine.py` | Core RAG logic: retrieval, intent, direct answers, fact-check | `RagEngine`, `detect_lang`, `answer()` |
| `core/rag_perfection.py` | Arabizi/Franco normalization, out-of-scope guardrails | `normalize_arabizi_and_arabic()`, `check_out_of_scope_guardrail()` |
| `vectorstores/vectorstores.py` | Unified vector store interface (FAISS/Chroma/Qdrant/…) | `get_store()`, `VectorStore` ABC |
| `vectorstores/bm25_store.py` | BM25 lexical search + RRF fusion | `reciprocal_rank_fusion()` |
| `memory/memory.py` | Session-scoped memory of shown offers/FAQs | `MemoryStore`, `get_memory_store()` |
| `core/identity.py` | Trusted user_id resolution from request | `IdentityResolver`, `get_identity_resolver()` |
| `personal/personal_queries.py` | Live ClickHouse queries for user-specific data | `is_personal_query()`, `answer_personal_query()` |
| `catalog/catalog_queries.py` | Live ClickHouse queries for public catalog data | `is_catalog_query()`, `CatalogQueryService` |
| `ingestion/refresh.py` | Canonical entry point -- fetch from ClickHouse + build/rebuild index | CLI: `python ingestion/refresh.py` |
| `ingestion/loaders/build_index.py` | Build vector indexes from `data/*.json` + write index manifest | CLI: `python ingestion/loaders/build_index.py` |
| `ingestion/loaders/index_manifest.py` | Shared index manifest format / helpers | Used by build_index*.py + manifest check in RagEngine |
| `ingestion/sources/fetch_*_clickhouse.py` | ClickHouse-based source fetchers (offers, partners, type_price …) | Driven by `ingestion/refresh.py` |
| `tests/manual_test_runner.py` | Manual verification of Perfection Pillars | CLI: `python tests/manual_test_runner.py` |
| `eval/run_eval.py` | Run `queries.json` eval suite against RagEngine | CLI: `python eval/run_eval.py` |
| `eval/queries.json` | 100+ eval test cases with assertions | Test cases: `expected_source`, `expected_id`, keywords |

---

## Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) installed and running
- Redis (optional — set `MEMORY_BACKEND=local` to skip)

### 1. Environment Setup

```bash
# Enter project
cd waffarha-chatbot

# Create virtual environment
python -m venv venv
venv\Scripts\activate       # Windows
# source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
```

### 2. Start Required Services

```bash
# Terminal 1: Ollama (LLM server)
ollama serve
ollama pull qwen2.5:3b-instruct

# Terminal 2 (optional): Redis for session memory
docker run -d --name waffarha-redis -p 6379:6379 redis:7-alpine
```

### 3. Configure Environment

```bash
copy .env.example .env    # Windows
# cp .env.example .env    # macOS/Linux
# Edit .env — OLLAMA_MODEL, CLICKHOUSE_* (only needed to re-run ingestion)
```

### 4. Run the Server

```bash
uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000** — the chat widget loads from `static/` and talks to the real backend.

> First message takes ~30–60s on a CPU-only host (embedding model load
> measured at ~47s here; Ollama warm-up on top). Subsequent responses are fast.

---

## Docker Deployment

The Docker setup bakes Ollama model + embedding model into images at build time so production needs zero outbound network.

### Files at Project Root

```
├── dockerfile              # App image
├── docker-compose.yml      # Orchestrates app + ollama + redis
├── .dockerignore
├── .env.example
└── ollama-docker/
    └── ollama-Dockerfile   # Ollama image with model baked in
```

### Build & Run

```bash
# One-time: copy env template and set your security key
copy .env.example .env    # Windows
# cp .env.example .env    # macOS/Linux

# Build images (downloads models — takes several minutes)
docker compose up --build

# Subsequent starts are fast
docker compose up
```

Open **http://localhost:8000**.

### Why This Docker Design

| Aspect | Approach | Benefit |
|--------|----------|---------|
| Ollama Model | Baked in `ollama-docker` image at build time | No registry access on prod |
| Embedding Model | Downloaded during app image build | Offline runtime |
| Data Volume | `ollama_models` named volume | Persists across recreates |
| Healthchecks | Redis + Ollama both have healthchecks | Compose waits for deps |

---

## Configuration Reference (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_DEVICE` | `cpu` | `cpu` or `cuda` |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model (must match the built index) |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` | Ollama model tag |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_NUM_CTX` | `1536` | Context window for generation |
| `VECTOR_STORE_BACKEND` | `qdrant` | `faiss` \| `chroma` \| `qdrant` \| `lancedb` \| `pgvector` |
| `INDEX_DIR` | `data/index/<model>/<backend>/` | Auto-computed from embedding model + backend |
| `MIN_RELEVANCE_SCORE` | `0.30` | Cosine similarity floor |
| `MIN_RELEVANCE_SCORE_STRICT` | `0.45` | Hard refusal floor |
| `RELEVANCE_CHECK_SCORE` | `0.55` | Skip LLM relevance check above this |
| `LEXICAL_BONUS_WEIGHT` | `0.50` | Lexical overlap boost weight |
| `INTENT_BONUS_WEIGHT` | `0.18` | Intent classification soft bonus |
| `ENTITY_MATCH_BONUS` | `0.40` | Bonus when query mentions known merchant |
| `TITLE_MATCH_BONUS` | `0.50` | Per-query-word title match bonus |
| `FAQ_DIRECT_ANSWER_SCORE` | `0.85` | FAQ template shortcut trigger |
| `OFFER_DIRECT_ANSWER_SCORE` | `0.85` | Offer template shortcut trigger |
| `DIRECT_ANSWER_MIN_EMBEDDING_SCORE` | `0.55` | Embedding-score floor before FAQ/offer/stock direct answers fire (blocks bonus-inflated `combined_score` from short-circuiting) |
| `MEMORY_BACKEND` | `redis` | `redis` \| `local` (dev only, no Redis needed) — also drives `SessionManager` (catalog compare) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `IDENTITY_BACKEND` | `static` | `static` (demo) \| `header` \| `session` \| `auth` (**production-safe**) |
| `IDENTITY_HEADER` | `X-User-ID` | Identity header name (`header`/`auth` backends) |
| `SESSION_COOKIE_NAME` | `session_id` | Session cookie/bearer name (`session` backend) |
| `AUTH_SIGNING_SECRET` | *(empty)* | HMAC secret for `auth` backend; empty ⇒ refuse all personal queries |
| `AUTH_SIGNATURE_HEADER` | `X-User-Auth` | Signature header (`<expiry>:<hmac>`) for `auth` backend |
| `AUTH_CLOCK_SKEW_SECONDS` | `300` | Expiry tolerance for `auth` backend tokens |
| `STATIC_TEST_USER_ID` | `0` | Test user ID for `static` backend |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse HTTP host |
| `CLICKHOUSE_PORT` | `8123` | ClickHouse HTTP port |
| `PERSONAL_QUERIES_ENABLED` | `false` | Enable live ClickHouse personal queries (only with a trusted identity backend) |
| `CATALOG_QUERIES_ENABLED` | `true` | Enable live ClickHouse catalog queries |
| `DIRECT_ANSWER_MIN_EMBEDDING_SCORE` | `0.55` | Floor on raw embedding similarity before FAQ/offer/stock direct answers may fire |
| `MAX_CONCURRENT_GENERATIONS` | `4` | Max parallel Ollama generation slots |
| `GENERATION_QUEUE_TIMEOUT` | `30` | Seconds before 503 on queue overflow |

---

## Identity & session memory

**Identity** (`core/identity.py`) is the *only* source of a trusted `user_id`,
and every "my coupons / my orders" personal query is scoped by whatever it
resolves — never by client input.

- `IDENTITY_BACKEND=static` — fixed test user. **Demo only.**
- `IDENTITY_BACKEND=header` — reads `X-User-ID` set by an auth proxy. Only if
  this service is never exposed directly to clients (no verification).
- `IDENTITY_BACKEND=session` — resolves a session cookie/bearer token against
  a server-side Redis session store shared with a login service.
- `IDENTITY_BACKEND=auth` — **the production-safe option.** The auth layer
  validates the user's session, then injects two signed headers:

  ```
  X-User-ID:   <user_id>
  X-User-Auth: <expiry_unix_ts>:<hex hmac-sha256("<user_id>:<expiry_unix_ts>")>
  ```

  signed with the shared `AUTH_SIGNING_SECRET` (see `make_auth_headers()` in
  `core/identity.py`). The app verifies the signature (constant-time) and the
  expiry, so the endpoint doesn't need to be firewalled away from clients.
  **Fail-closed:** unset secret, or missing / tampered / expired token ⇒ the
  personal-data layer simply refuses. Generate a secret with
  `python -c "import secrets;print(secrets.token_hex(32))"`.

Because personal queries are gated on a trusted identity, `PERSONAL_QUERIES_ENABLED`
defaults to **off** — enable it explicitly once a real identity backend is
configured for your deployment.

**Session memory** (offers/FAQs actually shown per session, and recent turns)
lives in `memory/memory.py` and `session/session_manager.py` (catalog
comparison). Both honor `MEMORY_BACKEND`:

- `MEMORY_BACKEND=redis` — shared across workers/replicas, survives restarts.
- `MEMORY_BACKEND=local` — in-process dict, no Redis needed for dev/tests.

Memory keys are namespaced under the resolved `user_id` (`u<uid>:<session_id>`)
so a client-supplied `session_id` shared across users (same browser/device or
a guessed string) never leaks another user's remembered offers or turns.

---

## Data Pipeline

### Static Knowledge Base (RAG Index)

```
data/
├── faqs.json                    # Curated FAQ entries
├── faqs_payment_methods.json    # Payment method FAQs from ClickHouse
├── offers_raw.json              # Live offers from mobile API
├── type_prices.json             # Offer type → price mappings
├── faqs_purchasing_status.json  # Purchasing status FAQs
└── index/                       # Built vector indexes (auto-generated)
    └── BAAI__bge-m3/
        ├── faiss/               # FAISS index
        ├── qdrant/              # Qdrant collection (embedded folder mode)
        └── bm25.pkl             # BM25 lexical index (if hybrid enabled)
```

### Building & Refreshing Indexes (single entry point)

The whole pipeline — fetch every source from ClickHouse, then build/rebuild the
index and its `index_manifest.json` — is one command (see
[INGESTION.md](INGESTION.md) for lineage, cadence, and CLI reference):

```bash
# Fetch all sources from ClickHouse + build the index
python ingestion/refresh.py

# Rebuild from already-fetched data/ files (no ClickHouse connection needed)
python ingestion/refresh.py --skip-fetch

# Dry-run: show lineage / freshness report, write/change nothing
python ingestion/refresh.py --plan

# Retired in Phase 4: the mobile-API scrape (ingestion/sources/fetch_offers.py)
# and the dead sync tool (ingestion/sync_clickhouse.py) are gone -- all index
# sources come from ClickHouse.
# Note: ingestion/sources/get_customers_offers.py is a DIAGNOSTIC script for
# per-user coupon debugging, NOT an index source; it is not part of refresh.py.
```

Under the hood `refresh.py` drives the individual steps, which you can also run
standalone:

```bash
python ingestion/sources/fetch_offers_clickhouse.py     # -> data/offers_raw.json
python ingestion/sources/fetch_partners_clickhouse.py   # -> data/partners/partners.json
python ingestion/sources/fetch_payment_methods_clickhouse.py
python ingestion/sources/fetch_purchasing_status_clickhouse.py
python ingestion/sources/fetch_type_price_clickhouse.py
python ingestion/loaders/build_index.py                 # full rebuild (embeds everything)
python ingestion/loaders/build_index_incremental.py     # only changed docs
```

Indexes are written to `data/index/<embedding_model>/<backend>/` with an
`index_manifest.json` in each directory; `RagEngine` logs a warning at load if
the stored manifest's model/backend don't match what the engine was asked for.

# Then rebuild index
python ingestion/loaders/build_index.py
```

---

## Evaluation System

The project includes a comprehensive evaluation harness with **100+ test cases** covering offer lookups, FAQ answers, follow-up memory, out-of-scope deflection, hallucination guards, and multi-language queries.

### Running Automated Evaluations

```bash
# Run full eval suite against queries.json
python eval/run_eval.py

# Override model / backend
python eval/run_eval.py --embedding-model intfloat/multilingual-e5-base --backend faiss
python eval/run_eval.py --llm-model qwen2.5:3b-instruct --temperature 0.0

# Custom query file
python eval/run_eval.py --queries my_test_cases.json
```

### Output

```
eval/results/<timestamp>_tag/
├── full.json     # Complete per-query results (retrieval scores, generation, assertions)
└── summary.csv   # Spreadsheet-friendly: id, category, query, pass/fail, latency
```

### Retrieval-Only Baseline (Phase 3)

Deterministic, LLM-free checkpoint of pure embedding+search correctness against the
prebuilt index. No Ollama needed; roughly 3–4 min plus engine load.

```bash
python -c "import os"  # ensure PYTHONIOENCODING=utf-8 on Windows for Arabic output
python utils/run_full_eval.py --retrieval-only --queries eval/queries.json --out-dir reports/phase3 --tag phase3_baseline
```

Fresh baseline (2026-09-14, HEAD aede429): **146/172 checked passed (225 total, 0 errors),
hit@1=0.333, hit@k=0.667, mrr=0.468** — output under
`reports/phase3/20260914_091409_phase3_baseline/`. The `hit@1=0.333` figure
(open-pair-rerank + combined-score bonuses) is the real retrieval ceiling; the
historical `80% / 58.1% / 4.1%` numbers predate the current architecture and are
not reproducible. Phase 3 also fixed direct-answer score inflation: the FAQ/offer/stock
shortcuts now require an unbonused embedding score ≥ `DIRECT_ANSWER_MIN_EMBEDDING_SCORE`
(every genuinely confident FAQ match in the baseline sat above 0.55), and the
unmatched-brand / out-of-scope / PILLAR-3 guardrails run before personal & catalog
short-circuits instead of after. Post-fix baseline run is identical
(`reports/phase3/20260914_095745_phase3_after/`): ranking code was untouched.

### CI Integration

Exit code is **1 if any assertion fails** — wire directly into CI:

```yaml
- name: Run RAG Evaluation
  run: python eval/run_eval.py
```

### Test Case Schema (`eval/queries.json`)

```json
{
  "id": "unique_test_id",
  "category": "offer_direct_answer | faq_direct_answer | personal_query | followup | negative | hallucination_check",
  "query": "User's question",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "recent_offers": [{"metadata": {"source": "offer", "id": 123, "merchant": "KFC"}}],
  "expected_source": "faq | offer | personal | null",
  "expected_id": 123,
  "expected_keywords": ["189", "EGP"],
  "forbidden_keywords": ["fabricated_price"],
  "lang": "en | ar | mixed",
  "note": "Human-readable context for this test case"
}
```

### Manual Testing

```bash
# Run the 5-perfection-pillar manual test script
python tests/manual_test_runner.py
```

### Agentic Intelligence Layer

Live drivers for the agent (`agent/engine.py` + tools) against the real RAG
engine. Both need Ollama running (`qwen2.5:3b-instruct`); set
`AGENT_MODEL_OVERRIDE` to test another model. UTF-8 output requires
`PYTHONIOENCODING=utf-8` on Windows.

```bash
# Continuous REPL: per-turn goal/plan/tools/decision trace + latency + SAFE
# decisions. Session state (history + recent_offers) is preserved turn to turn,
# so follow-ups like "cheaper than this" resolve against actually shown offers.
$env:PYTHONIOENCODING="utf-8"; venv\Scripts\python.exe tests\manual_agent.py

# Stage-4 live eval: replays a seeded 12-turn corpus (Arabic/Franco/English,
# similar, cross-merchant, prompts-injection, sub-15-EGP hard filter) and, per
# turn, measures budget-exhaustion (tight-vs-wide LLM budget drift + honesty
# note), unnecessary-tool-call rate, and per-stage latency by
# (tool_calls, replan_count). Outputs JSON + CSV.
$env:PYTHONIOENCODING="utf-8"; venv\Scripts\python.exe tests\eval\stage4_agent_eval.py
#   [--out path\stage4_X.json] [--csv path\stage4_X.csv] [--no-probe] [--limit N]
```

---

## Model Performance Reference

### Embedding Models

| Model | Dimensions | Size | Notes |
|-------|------------|------|-------|
| `intfloat/multilingual-e5-small` | 384 | ~130MB | Fast, decent Arabic |
| `intfloat/multilingual-e5-base` | 768 | ~430MB | Sweet spot for AR+EN |
| `intfloat/multilingual-e5-large` | 1024 | ~1.3GB | Best quality |
| `BAAI/bge-m3` | 1024 | ~2.3GB | Multilingual, hybrid (default) |

### LLM Models (Ollama)

| Model | Size | Notes |
|-------|------|-------|
| `qwen2.5:1.5b-instruct` (legacy)| ~1GB | Fast, lower quality |
| `qwen2.5:3b-instruct` | ~2GB | Production default |
| `aya-expanse:8b` | ~5GB | Best Arabic quality |
| `command-r7b-arabic` | ~5GB | Arabic-optimized |

---

## Development Guide

### Project Structure

```
waffarha-chatbot/
├── app.py                      # Shim — re-exports core.app for uvicorn
├── core/
│   ├── app.py                  # FastAPI server, /api/chat, /api/chat/stream
│   ├── config.py               # All runtime configuration
│   ├── rag_engine.py           # Core RAG: retrieve(), answer(), answer_stream()
│   ├── rag_perfection.py       # Arabizi normalization, guardrails, intent classification
│   └── identity.py             # User identity resolution
├── vectorstores/
│   ├── vectorstores.py         # VectorStore ABC + get_store() factory
│   └── bm25_store.py           # BM25 lexical search + RRF fusion
├── memory/
│   └── memory.py               # Session-scoped MemoryStore (Redis or local)
├── personal/
│   └── personal_queries.py     # ClickHouse personal data queries
├── catalog/
│   └── catalog_queries.py      # ClickHouse public catalog queries
├── ingestion/
│   ├── refresh.py               # Canonical fetch+build entry point (+ --plan)
│   ├── loaders/
│   │   ├── index_manifest.py            # Shared index-manifest format/helpers
│   │   ├── build_index.py               # Build vector indexes (+ writes manifest)
│   │   └── build_index_incremental.py   # Incremental rebuild (+ manifest-aware)
│   └── sources/
│       ├── fetch_*_clickhouse.py  # ClickHouse data fetchers (offers/partners/…)
│       └── get_customers_offers.py  # Per-customer diagnostic (not an index source)
├── data/
│   ├── faqs.json
│   ├── offers_raw.json
│   ├── faqs_payment_methods.json
│   ├── faqs_purchasing_status.json
│   └── index/                  # Built vector indexes (auto-generated)
├── eval/
# Eval CLI moved to `eval/run_eval.py`             # Eval CLI
│   ├── queries.json            # 100+ test cases
│   └── results/                # Evaluation output
├── tests/
│   ├── manual_test_runner.py   # Manual pillar verification
│   ├── memory_comprehensive_test.py
│   └── unit/                   # Unit tests
├── static/
│   └── index.html              # Chat widget (vanilla JS)
├── dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

### Adding a New Vector Backend

1. Implement `VectorStore` ABC in `vectorstores/vectorstores.py`
2. Register in `get_store()` factory
3. Add dependency to `requirements.txt` (optional)
4. Test: `python ingestion/loaders/build_index.py --backend mybackend`

### Adding Test Cases

Add to `eval/queries.json` with:
- Unique `id`
- Clear `category`
- Realistic `query` (from actual user logs if possible)
- Assertions (`expected_source`, `expected_id`, `expected_keywords`)
- `note` explaining the test intent

### Running Tests Locally

```bash
# Lint
ruff check .

# Type check
mypy app.py core/app.py core/rag_engine.py core/config.py

# Eval suite
python eval/run_eval.py

# Manual pillar test
python tests/manual_test_runner.py
```

---

## Security Considerations

| Layer | Protection |
|-------|------------|
| User Identity | `identity.py` is the only source of `user_id` — never trust client input |
| Personal Queries | All SQL scoped by trusted `user_id`; no raw identifiers from user |
| API Keys | Ingest reads ClickHouse with a READ-ONLY service account; DB-level grants are the enforcement layer. Keys (e.g. `JINA_API_KEY`, eval's standalone `WAFFARHA_SECURITY_KEY`) never touch the chat path |
| CORS | Configured in `core/app.py` — restrict `allow_origins` in production |
| Rate Limiting | Not built-in — add via nginx/API gateway in production |
| Secrets | `.env` in `.gitignore`; use Docker secrets / env injection in prod |

---

## License

Internal Waffarha project — not for external distribution.

---

## Related Documentation

- **[DOCKER.md](DOCKER.md)** — Detailed Docker deployment guide
- **[CLICKHOUSE_LOCAL_SETUP.md](CLICKHOUSE_LOCAL_SETUP.md)** — Local ClickHouse setup for development
- **[RATE_LIMIT_FIX.md](RATE_LIMIT_FIX.md)** — Rate limiting implementation notes
- **[EVALUATION_REPORT.md](../EVALUATION_REPORT.md)** — Latest evaluation results
- **[HALLUCINATION_ANALYSIS.md](../reports/HALLUCINATION_ANALYSIS.md)** — Hallucination audit findings