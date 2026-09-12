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
| `ingestion/loaders/build_index.py` | Build vector indexes from `data/*.json` | CLI: `python ingestion/loaders/build_index.py` |
| `ingestion/sources/fetch_offers.py` | Scrape live offers from Waffarha mobile API | CLI: `python ingestion/sources/fetch_offers.py` |
| `ingestion/sources/fetch_*_clickhouse.py` | ClickHouse-based data fetchers | Various fetch scripts |
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
# Edit .env — WAFFARHA_SECURITY_KEY required for offer fetching
```

### 4. Run the Server

```bash
uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000** — the chat widget loads from `static/` and talks to the real backend.

> First message takes ~10–30s (embedding model loads, Ollama warms up). Subsequent responses are fast.

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
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-large` | HF model for embeddings |
| `OLLAMA_MODEL` | `qwen2.5:3b-instruct` | Ollama model tag |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_NUM_CTX` | `1536` | Context window for generation |
| `VECTOR_BACKEND` | `faiss` | `faiss` \| `chroma` \| `qdrant` \| `lancedb` \| `pgvector` |
| `INDEX_DIR` | `data/index/<model>/<backend>/` | Auto-computed from embedding model + backend |
| `MIN_RELEVANCE_SCORE` | `0.30` | Cosine similarity floor |
| `MIN_RELEVANCE_SCORE_STRICT` | `0.45` | Hard refusal floor |
| `RELEVANCE_CHECK_SCORE` | `0.55` | Skip LLM relevance check above this |
| `LEXICAL_BONUS_WEIGHT` | `0.50` | Lexical overlap boost weight |
| `INTENT_BONUS_WEIGHT` | `0.18` | Intent classification soft bonus |
| `ENTITY_MATCH_BONUS` | `0.40` | Bonus when query mentions known merchant |
| `TITLE_MATCH_BONUS` | `0.50` | Per-query-word title match bonus |
| `FAQ_DIRECT_ANSWER_SCORE` | `0.65` | FAQ template shortcut trigger |
| `OFFER_DIRECT_ANSWER_SCORE` | `0.65` | Offer template shortcut trigger |
| `MEMORY_BACKEND` | `redis` | `redis` \| `local` (dev only, no Redis needed) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `IDENTITY_BACKEND` | `static` | `static` \| `header` \| `session` |
| `STATIC_TEST_USER_ID` | `0` | Test user ID for `static` backend |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse HTTP host |
| `CLICKHOUSE_PORT` | `8123` | ClickHouse HTTP port |
| `PERSONAL_QUERIES_ENABLED` | `true` | Enable live ClickHouse personal queries |
| `CATALOG_QUERIES_ENABLED` | `true` | Enable live ClickHouse catalog queries |
| `WAFFARHA_SECURITY_KEY` | *(required)* | API key for offer fetching scripts |
| `MAX_CONCURRENT_GENERATIONS` | `4` | Max parallel Ollama generation slots |
| `GENERATION_QUEUE_TIMEOUT` | `30` | Seconds before 503 on queue overflow |

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
    └── intfloat__multilingual-e5-large/
        ├── faiss/               # FAISS index
        └── bm25.pkl             # BM25 lexical index (if hybrid enabled)
```

### Building Indexes

```bash
# Build index (uses EMBEDDING_MODEL + VECTOR_BACKEND from .env)
python ingestion/loaders/build_index.py

# Specify backend explicitly
python ingestion/loaders/build_index.py --backend faiss
python ingestion/loaders/build_index.py --backend chroma

# Incremental build (only embeds changed docs)
python ingestion/loaders/build_index_incremental.py
```

Indexes are written to `data/index/<embedding_model>/<backend>/`.

### Refreshing Offers

```bash
# Fetch fresh offers from Waffarha mobile API (requires WAFFARHA_SECURITY_KEY)
python ingestion/sources/fetch_offers.py

# Or use ClickHouse-based fetchers (requires ClickHouse credentials in .env)
python ingestion/sources/fetch_offers_clickhouse.py
python ingestion/sources/fetch_partners_clickhouse.py
python ingestion/sources/fetch_payment_methods_clickhouse.py
python ingestion/sources/fetch_purchasing_status_clickhouse.py
python ingestion/sources/fetch_type_price_clickhouse.py
python ingestion/sources/get_customers_offers.py

# Sync all ClickHouse data
python ingestion/transformers/sync_clickhouse.py

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

---

## Model Performance Reference

### Embedding Models

| Model | Dimensions | Size | Notes |
|-------|------------|------|-------|
| `intfloat/multilingual-e5-small` | 384 | ~130MB | Fast, decent Arabic |
| `intfloat/multilingual-e5-base` | 768 | ~430MB | Sweet spot for AR+EN |
| `intfloat/multilingual-e5-large` | 1024 | ~1.3GB | Best quality (default) |
| `BAAI/bge-m3` | 1024 | ~2.3GB | Multilingual, hybiud |

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
│   ├── loaders/
│   │   ├── build_index.py      # Build vector indexes
│   │   └── build_index_incremental.py  # Incremental rebuild
│   ├── sources/
│   │   ├── fetch_offers.py     # Mobile API scraper
│   │   ├── fetch_*_clickhouse.py  # ClickHouse data fetchers
│   │   └── get_customers_offers.py  # Per-customer offer fetcher
│   └── transformers/
│       └── sync_clickhouse.py  # ClickHouse sync utility
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
| API Keys | `WAFFARHA_SECURITY_KEY` only used in ingest scripts, never in chat path |
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
- **[HALLUCINATION_ANALYSIS.md](../HALLUCINATION_ANALYSIS.md)** — Hallucination audit findings