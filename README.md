# Waffarha Assistant — RAG-Powered Customer Support Chatbot

A production-ready customer support chat widget for [Waffarha](https://waffarha.com), Egypt's leading deals platform. Combines **RAG (Retrieval-Augmented Generation)** over a curated knowledge base with **live ClickHouse queries** for personal user data (coupons, orders, spending).

---

## 🎯 Key Features

| Feature | Description |
|---------|-------------|
| **Bilingual Support** | English & Arabic (RTL-aware UI, multilingual embeddings) |
| **Smart Retrieval** | Hybrid vector + lexical search with intent classification |
| **Direct Answers** | Template-based shortcuts for FAQs & offers (no LLM call) |
| **Personal Queries** | Live ClickHouse lookups for "my coupons", "order status", spending |
| **Session Memory** | Redis-backed memory of shown offers/FAQs for follow-up resolution |
| **Multiple Vector Backends** | FAISS (default), Chroma, Qdrant, LanceDB, pgvector — swap via config |
| **Evaluation Harness** | 100+ test cases with CI-ready pass/fail exit codes |
| **Docker-First Deploy** | Model-baked images, zero-runtime network dependencies |

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        static/index.html                             │
│              (Chat Widget — vanilla JS, no build step)               │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ POST /api/chat
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          app.py (FastAPI)                            │
│  • CORS, static file serving                                        │
│  • Request/response shaping                                         │
│  • Identity resolution (auth proxy → user_id)                       │
└──────────────────────────┬──────────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌─────────────────┐ ┌─────────────┐ ┌───────────────┐
│   RagEngine     │ │  Identity   │ │   Memory      │
│  (rag_engine.py)│ │  Resolver   │ │  (memory.py)  │
│                 │ │ (identity.py)│ │               │
│ • Vector search │ │             │ │ • Redis/local │
│ • Intent class  │ │ • Static    │ │ • Session-scoped│
│ • Direct answer │ │ • Header    │ │ • Shown items │
│ • Fact-check    │ │ • Session   │ │ • 1hr TTL     │
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

## 📦 Module Reference

| File | Purpose | Key Exports |
|------|---------|-------------|
| `app.py` | FastAPI server, `/api/chat` endpoint, static serving | `app`, `ChatRequest`, `ChatResponse` |
| `config.py` | All runtime configuration via env vars | `EMBEDDING_DEVICE`, `OLLAMA_MODEL`, `MIN_RELEVANCE_SCORE`, etc. |
| `rag_engine.py` | Core RAG logic: retrieval, intent, direct answers, fact-check | `RagEngine`, `detect_lang`, `answer()` |
| `vectorstores.py` | Unified vector store interface (FAISS/Chroma/Qdrant/…) | `get_store()`, `VectorStore` ABC |
| `memory.py` | Session-scoped memory of shown offers/FAQs | `MemoryStore`, `get_memory_store()` |
| `identity.py` | Trusted user_id resolution from request | `IdentityResolver`, `get_identity_resolver()` |
| `personal_queries.py` | Live ClickHouse queries for user-specific data | `is_personal_query()`, `answer_personal_query()` |
| `ingest/build_index.py` | Build vector indexes from `data/*.json` | CLI: `--backend`, `--embedding-model` |
| `ingest/fetch_offers.py` | Scrape live offers from Waffarha mobile API | CLI: writes `data/offers_raw.json` |
| `ingest/*_clickhouse.py` | Alternative ClickHouse-based data fetchers | Various fetch scripts |
| `common.py` | Shared eval utilities (engine cache, test runner) | `get_engine()`, `run_one()` |
| `run_eval.py` | CLI to run `queries.json` against RagEngine | `python run_eval.py --help` |
| `queries.json` | 100+ eval test cases with assertions | Test cases: `expected_source`, `expected_id`, keywords |

---

## 🚀 Quick Start (Local Development)

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) installed and running
- Redis (Docker recommended)

### 1. Environment Setup

```bash
# Clone & enter project
cd waffarha-chatbot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Start Required Services

```bash
# Terminal 1: Ollama (LLM server)
ollama serve
ollama pull qwen2.5:1.5b-instruct  # or qwen2.5:3b-instruct for better quality

# Terminal 2: Redis (session memory)
docker run -d --name waffarha-redis -p 6379:6379 redis:7-alpine
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env — at minimum, WAFFARHA_SECURITY_KEY is required for offer fetching
```

### 4. Run the Server

```bash
uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000** — the chat widget loads from `static/` and talks to the real backend.

> ⚡ **First message takes 10–30s** (embedding model loads, Ollama warms up). Subsequent responses are fast.

---

## 🐳 Docker Deployment (Production-Ready)

The Docker setup **bakes the Ollama model and embedding model into images at build time**, so production servers need **zero outbound network access** at runtime.

### Files Required at Project Root

```
├── Dockerfile              # App image
├── docker-compose.yml      # Orchestrates app + ollama + redis
├── .dockerignore
├── .env.example
└── ollama-docker/
    └── ollama-Dockerfile   # Ollama image with model baked in
```

### Build & Run

```bash
# One-time: copy env template and set your security key
cp .env.example .env
# Edit .env — WAFFARHA_SECURITY_KEY is mandatory

# Build images (downloads models — takes several minutes)
docker compose up --build

# Subsequent starts are fast (models already in images)
docker compose up
```

Open **http://localhost:8000**.

### Why This Docker Design?

| Aspect | Approach | Benefit |
|--------|----------|---------|
| **Ollama Model** | Baked in `ollama-docker` image at build time | No registry access needed on prod servers |
| **Embedding Model** | Downloaded during app image build | Same — offline runtime |
| **Data Volume** | `ollama_models` named volume | Model persists across container recreates |
| **Healthchecks** | Redis + Ollama both have healthchecks | Compose waits for dependencies |
| **Single Origin** | App serves widget + API | No CORS, no separate static hosting |

### Production Notes

- **Change `OLLAMA_MODEL`** in `.env` → rebuilds `ollama` image only
- **Scale app** with `docker compose up --scale app=3` (Redis shares memory)
- **Logs**: `docker compose logs -f app`
- **Shell**: `docker compose exec app bash`

---

## 🔧 Configuration Reference (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_DEVICE` | `cpu` | `cpu` or `cuda` — device for sentence-transformers |
| `EMBEDDING_MODEL` | `intfloat/multilingual-e5-base` | HF model for embeddings |
| `OLLAMA_MODEL` | `qwen2.5:1.5b-instruct` | Ollama model tag for generation |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_NUM_CTX` | `2048` | Context window for generation |
| `VECTOR_BACKEND` | `faiss` | `faiss` \| `chroma` \| `qdrant` \| `lancedb` \| `pgvector` |
| `INDEX_DIR` | `data/index` | Root directory for vector indexes |
| `MIN_RELEVANCE_SCORE` | `0.22` | Cosine similarity threshold for retrieval |
| `LEXICAL_BONUS_WEIGHT` | `0.08` | BM25-style lexical boost weight |
| `FAQ_DIRECT_ANSWER_SCORE` | `0.38` | Score threshold for FAQ direct-answer shortcut |
| `OFFER_DIRECT_ANSWER_SCORE` | `0.30` | Score threshold for offer direct-answer shortcut |
| `MEMORY_BACKEND` | `redis` | `redis` \| `local` (local = in-process dict, dev only) |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `IDENTITY_BACKEND` | `static` | `static` \| `header` \| `session` |
| `STATIC_USER_ID` | `12345` | Test user ID for `static` identity backend |
| `IDENTITY_HEADER` | `X-User-ID` | Header name for `header` identity backend |
| `CLICKHOUSE_HOST` | `localhost` | ClickHouse HTTP host |
| `CLICKHOUSE_PORT` | `8123` | ClickHouse HTTP port |
| `CLICKHOUSE_USERNAME` | `default` | ClickHouse username |
| `CLICKHOUSE_PASSWORD` | (empty) | ClickHouse password |
| `CLICKHOUSE_DATABASE` | `default` | ClickHouse database |
| `WAFFARHA_SECURITY_KEY` | *required* | API key for offer fetching scripts |

---

## 📚 Data Pipeline

### Static Knowledge Base (RAG Index)

```
data/
├── faqs.json                    # Curated FAQ entries (id, question, answer, category)
├── faqs_payment_methods.json    # Auto-generated from ClickHouse dim_payment_methods
├── offers_raw.json              # Live offers scraped from mobile API
├── type_prices.json             # Offer type → price mappings
└── faqs_purchasing_status.json  # Purchasing status FAQs
```

### Building Indexes

```bash
# Default: FAISS with multilingual-e5-base
python ingest/build_index.py

# Chroma instead
python ingest/build_index.py --backend chroma

# Both backends, different embedding model
python ingest/build_index.py --backend both --embedding-model intfloat/multilingual-e5-small
```

Indexes are written to `data/index/<embedding_model>/<backend>/` — multiple combinations coexist.

### Refreshing Offers

```bash
# Fetch fresh offers from Waffarha API (requires WAFFARHA_SECURITY_KEY)
python ingest/fetch_offers.py

# Or use ClickHouse-based fetchers (requires ClickHouse credentials)
python ingest/fetch_offers_clickhouse.py
python ingest/fetch_partners_clickhouse.py
# ...etc.

# Then rebuild index
python ingest/build_index.py
```

---

## 🧪 Evaluation System

The project includes a comprehensive evaluation harness with **100+ test cases** covering:

- **Offer direct answers** (price, discount, merchant)
- **FAQ direct answers** (registration, purchase, refunds)
- **Personal queries** (my coupons, order status, spending)
- **Follow-up resolution** ("how much before discount?")
- **Negative cases** (disabled offers, out-of-scope questions)
- **Arabic queries** (RTL, Arabic embeddings)
- **Cross-lingual** (Arabic query → English answer, vice versa)

### Running Evaluations

```bash
# Basic run (uses config defaults)
python run_eval.py

# Override embedding model / backend
python run_eval.py --embedding-model intfloat/multilingual-e5-small --backend chroma

# Override LLM
python run_eval.py --llm-model qwen2.5:3b-instruct --temperature 0.0

# Custom query file
python run_eval.py --queries my_test_cases.json
```

### Output

```
eval/results/<timestamp>_<tag>/
├── full.json    # Complete results per query (retrieval, generation, assertions)
└── summary.csv  # Spreadsheet-friendly: id, category, query, pass/fail, latency
```

### CI Integration

Exit code is **1 if any assertion fails** — wire directly into CI:

```yaml
# .github/workflows/eval.yml
- name: Run RAG Evaluation
  run: python run_eval.py
```

### Test Case Schema (`queries.json`)

```json
{
  "id": "unique_test_id",
  "category": "offer_direct_answer | faq_direct_answer | personal_query | followup | negative",
  "query": "User's question",
  "history": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
  "recent_offers": [{"offer_id": 123, "title": "KFC", "price_before": 500, "price_after": 189}],
  "expected_source": "faq | offer | personal",
  "expected_id": 123,
  "expected_keywords": ["189", "EGP"],
  "forbidden_keywords": ["fabricated_price"],
  "lang": "en | ar",
  "note": "Human-readable context for this test case"
}
```

---

## 🔬 Model Performance & Benchmarking

### Embedding Models Tested

| Model | Dimensions | Size | Index Build Time | Retrieval Quality (Recall@10) |
|-------|------------|------|------------------|-------------------------------|
| `intfloat/multilingual-e5-small` | 384 | ~130MB | ~45s | Baseline |
| `intfloat/multilingual-e5-base` | 768 | ~430MB | ~2min | +12% over small |
| `intfloat/multilingual-e5-large` | 1024 | ~1.3GB | ~6min | +8% over base |

> **Recommendation**: `multilingual-e5-base` is the sweet spot for Arabic+English.

### LLM Models Tested (Ollama)

| Model | Size | Speed (tokens/s) | Quality (subjective) | Best For |
|-------|------|------------------|---------------------|----------|
| `qwen2.5:1.5b-instruct` | ~1GB | ~45 | Good | Fast responses, low RAM |
| `qwen2.5:3b-instruct` | ~2GB | ~25 | Better | Production default |
| `qwen2.5:7b-instruct` | ~4.5GB | ~12 | Best | High-quality, needs GPU |
| `llama3.2:3b-instruct` | ~2GB | ~20 | Good | Alternative |

### Retrieval Thresholds (config.py)

These thresholds were tuned on the eval set:

| Threshold | Value | Purpose |
|-----------|-------|---------|
| `MIN_RELEVANCE_SCORE` | 0.22 | Global cosine similarity floor |
| `FAQ_DIRECT_ANSWER_SCORE` | 0.38 | FAQ template shortcut trigger |
| `OFFER_DIRECT_ANSWER_SCORE` | 0.30 | Offer template shortcut trigger |
| `LEXICAL_BONUS_WEIGHT` | 0.08 | BM25 boost for exact term matches |

---

## 🛠️ Development Guide

### Project Structure

```
waffarha-chatbot/
├── app.py                      # FastAPI server
├── config.py                   # Configuration
├── rag_engine.py               # Core RAG logic
├── vectorstores.py             # Vector store abstraction
├── memory.py                   # Session memory
├── identity.py                 # Auth / user_id resolution
├── personal_queries.py         # ClickHouse personal data queries
├── common.py                   # Eval utilities
├── run_eval.py                 # Eval CLI
├── queries.json                # Test cases
├── requirements.txt
├── dockerfile
├── docker-compose.yml
├── DOCKER.md
├── .env.example
├── static/
│   └── index.html              # Chat widget (vanilla JS)
├── ingest/
│   ├── build_index.py          # Index builder
│   ├── fetch_offers.py         # API scraper
│   ├── fetch_*_clickhouse.py   # ClickHouse fetchers
│   └── catalog_queries.py      # Catalog query service
├── data/
│   ├── faqs.json
│   ├── offers_raw.json
│   ├── faqs_payment_methods.json
│   ├── type_prices.json
│   ├── faqs_purchasing_status.json
│   └── index/                  # Built vector indexes
├── eval/
│   └── results/                # Evaluation outputs
└── ollama-docker/
    └── ollama-Dockerfile       # Model-baked Ollama image
```

### Adding a New Vector Backend

1. Implement `VectorStore` ABC in `vectorstores.py`:
   ```python
   class MyBackendStore(VectorStore):
       def __init__(self, ...): ...
       def add(self, embeddings, metadatas): ...
       def search(self, query_embedding, k): ...  # Must return cosine similarity
       def persist(self): ...
   ```
2. Register in `get_store()` factory
3. Add dependency to `requirements.txt` (optional)
4. Test: `python ingest/build_index.py --backend mybackend`

### Adding a New Personal Query Type

1. Add intent pattern to `_PERSONAL_PATTERNS` in `personal_queries.py`
2. Add SQL query method in `PersonalQueryService`
3. Add response formatter
4. Add test cases to `queries.json` with `"category": "personal_query"`

### Running Tests Locally

```bash
# Lint
ruff check .

# Type check
mypy app.py rag_engine.py config.py

# Eval suite
python run_eval.py
```

---

## 🔒 Security Considerations

| Layer | Protection |
|-------|------------|
| **User Identity** | `identity.py` is the **only** source of `user_id` — never trust client input |
| **Personal Queries** | All SQL scoped by trusted `user_id`; no raw identifiers from user |
| **API Keys** | `WAFFARHA_SECURITY_KEY` only used in ingest scripts, never in chat path |
| **CORS** | Configured in `app.py` — restrict `allow_origins` in production |
| **Rate Limiting** | Not built-in — add via nginx/API gateway in production |
| **Secrets** | `.env` in `.gitignore`; use Docker secrets / env injection in prod |

### Production Identity Backend

For real users, implement `AuthBackedIdentityResolver` in `identity.py`:

```python
class AuthBackedIdentityResolver(IdentityResolver):
    def resolve(self, request) -> int | None:
        # Validate session token from cookie/header against your auth system
        # Return verified user_id or None
        ...
```

Then set `IDENTITY_BACKEND=auth` in production `.env`.

---

## 📈 Monitoring & Observability

### Logging

All modules use `logging.getLogger("waffarha-app")` — configure once in `app.py`:

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
```

### Key Metrics to Track

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| `/api/chat` latency (p95) | FastAPI middleware | > 3s |
| Retrieval score distribution | `rag_engine.py` logs | Shift below 0.2 |
| Direct-answer hit rate | `rag_engine.py` logs | < 60% |
| Personal query error rate | `personal_queries.py` | > 5% |
| Redis memory usage | `memory.py` TTL stats | > 80% maxmemory |

---

## 🤝 Contributing

### Workflow

1. **Fork** the repository
2. **Create branch**: `git checkout -b feature/your-feature`
3. **Make changes** — follow existing code style (type hints, docstrings)
4. **Run eval**: `python run_eval.py` — must pass
5. **Submit PR** with description of changes and eval results

### Code Style

- **Type hints** on all public functions
- **Docstrings** on all modules, classes, public methods (Google style)
- **Logging** via `log = logging.getLogger("waffarha-app")`
- **No hardcoded values** — everything in `config.py` / `.env`

### Adding Test Cases

Add to `queries.json` with:
- Unique `id`
- Clear `category`
- Realistic `query` (from actual user logs if possible)
- Assertions (`expected_source`, `expected_id`, `expected_keywords`)
- `note` explaining the test intent

---

## 📄 License

Internal Waffarha project — not for external distribution.

---

## 🔗 Related Documentation

- **[DOCKER.md](DOCKER.md)** — Detailed Docker deployment guide
- **[DEVELOPMENT.md](DEVELOPMENT.md)** — Extended development guide
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Deep dive into RAG pipeline
- **[EVALUATION.md](EVALUATION.md)** — Evaluation methodology & results
- **[API.md](API.md)** — API reference for `/api/chat` endpoint