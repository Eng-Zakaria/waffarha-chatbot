# Local Development Guide — Waffarha Assistant

Complete guide for developing, debugging, testing, and enhancing the Waffarha Assistant locally.

---

## 🛠️ Prerequisites

### Required Software

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.11+ | [python.org](https://python.org) |
| Ollama | Latest | [ollama.com](https://ollama.com) |
| Redis | 7+ | Docker: `docker run -d -p 6379:6379 redis:7-alpine` |
| Git | Latest | [git-scm.com](https://git-scm.com) |
| (Optional) ClickHouse | 23+ | For data ingestion scripts |

### Recommended VS Code Extensions

- Python (Microsoft)
- Pylance
- Ruff
- Docker
- REST Client (for API testing)
- Markdown All in One

---

## 🏁 Getting Started

### 1. Clone & Setup Environment

```bash
git clone <repo-url>
cd waffarha-chatbot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Upgrade pip & install deps
pip install --upgrade pip
pip install -r requirements.txt

# Install dev dependencies (linting, testing)
pip install ruff mypy pytest pytest-asyncio
```

### 2. Start Services

```bash
# Terminal 1: Ollama
ollama serve
# In another tab:
ollama pull qwen2.5:1.5b-instruct  # or 3b-instruct for better quality

# Terminal 2: Redis
docker run -d --name waffarha-redis -p 6379:6379 redis:7-alpine

# Terminal 3: App (with auto-reload)
uvicorn app:app --reload --port 8000
```

### 3. Verify Setup

Open http://localhost:8000 — you should see the chat widget.

Test API directly:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "session_id": "test-123"}'
```

---

## 🔄 Development Workflow

### Code Changes → Auto-Reload

`uvicorn --reload` watches `.py` files and restarts automatically.
- **FastAPI routes** (`app.py`): instant reload
- **Core logic** (`rag_engine.py`, `vectorstores.py`, etc.): reload on save
- **Config changes** (`config.py`): require restart (cached on import)

### Configuration for Development

Create `.env.dev` (or edit `.env`):

```env
# Development overrides
EMBEDDING_DEVICE=cpu
OLLAMA_MODEL=qwen2.5:1.5b-instruct  # Faster, smaller
VECTOR_BACKEND=faiss
MEMORY_BACKEND=local  # No Redis needed for quick tests
IDENTITY_BACKEND=static
STATIC_USER_ID=12345
LOG_LEVEL=DEBUG
```

### Running Without Redis (Quick Iteration)

```env
MEMORY_BACKEND=local
```

This uses an in-process dict — fine for single-worker dev, **not for production**.

---

## 🧪 Testing

### Run Evaluation Suite

```bash
# Full eval (uses config defaults)
python run_eval.py

# Quick smoke test (subset)
python run_eval.py --queries eval/smoke_queries.json  # Create this file

# Test specific model/backends
python run_eval.py --embedding-model intfloat/multilingual-e5-small --backend chroma
python run_eval.py --llm-model qwen2.5:3b-instruct --temperature 0.0
```

### Run Unit Tests (When Added)

```bash
# All tests
pytest -v

# Specific module
pytest tests/test_rag_engine.py -v

# With coverage
pytest --cov=rag_engine --cov=vectorstores --cov=memory
```

### Manual Testing Checklist

| Feature | Test Command |
|---------|--------------|
| Basic chat | `curl -X POST /api/chat -d '{"message": "Hi"}'` |
| Offer query | `curl ... '{"message": "KFC offer price"}'` |
| FAQ query | `curl ... '{"message": "How to register"}'` |
| Follow-up | Send "How much before discount?" after offer answer |
| Arabic | `curl ... '{"message": "كيف أسجل"}'` |
| Personal (static) | `curl ... '{"message": "My coupons"}'` with `IDENTITY_BACKEND=static` |

---

## 🐛 Debugging

### Logging

All modules use `logging.getLogger("waffarha-app")`. Configure in `app.py` or via env:

```env
LOG_LEVEL=DEBUG
```

Key loggers to watch:

```python
# In any module
log = logging.getLogger("waffarha-app")
log.debug("Retrieved %d candidates", len(candidates))
log.info("Direct answer triggered: %s", source_type)
log.warning("LLM fallback used for: %s", query)
```

### Debug Endpoints (Add Temporarily)

```python
# In app.py during development
@app.get("/debug/index-stats")
async def index_stats():
    from rag_engine import RagEngine
    engine = RagEngine()
    store = engine.store
    return {
        "vectors": store.index.ntotal if hasattr(store, 'index') else "N/A",
        "backend": type(store).__name__,
    }

@app.get("/debug/memory/{session_id}")
async def debug_memory(session_id: str):
    from memory import get_memory_store
    store = get_memory_store()
    return store.get(session_id)
```

### Common Issues & Fixes

| Issue | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: sentence_transformers` | Venv not activated | `source venv/bin/activate` |
| `Connection refused: localhost:11434` | Ollama not running | `ollama serve` |
| `Index not found` | Index path wrong | Check `config.INDEX_DIR` and `data/index/...` exists |
| `CUDA out of memory` | GPU OOM | Set `EMBEDDING_DEVICE=cpu` or use smaller model |
| Arabic text garbled | Terminal encoding | `chcp 65001` (Windows) or use UTF-8 terminal |

### Interactive Debugging

```bash
# Drop into Python REPL with app context
python -c "
from rag_engine import RagEngine
engine = RagEngine()
result = engine.answer('KFC offer price')
print(result.answer)
print(result.sources)
"
```

---

## 🔧 Adding Features

### 1. New Vector Backend

**File**: `vectorstores.py`

```python
class MyBackendStore(VectorStore):
    def __init__(self, path: str, **kwargs):
        # Initialize connection, create collection
        pass

    def add(self, embeddings: np.ndarray, metadatas: list[dict]):
        # Add vectors with metadata
        pass

    def search(self, query_embedding: np.ndarray, k: int) -> list[tuple[float, dict]]:
        # Return [(cosine_similarity, metadata), ...]
        # MUST return cosine similarity in [-1, 1]
        pass

    def persist(self):
        # Flush to disk if needed
        pass
```

**Register** in `get_store()`:

```python
def get_store(backend: str, **kwargs) -> VectorStore:
    if backend == "mybackend":
        return MyBackendStore(**kwargs)
    # ... existing backends
```

**Test**:

```bash
python ingest/build_index.py --backend mybackend
python run_eval.py --backend mybackend
```

### 2. New Personal Query Type

**File**: `personal_queries.py`

```python
# 1. Add intent pattern
_PERSONAL_PATTERNS = [
    # ... existing
    (r"\bmy\s+spending\b", "spending_summary"),
]

# 2. Add handler method
async def _spending_summary(self, user_id: int, lang: str) -> dict:
    sql = """
    SELECT SUM(total_price) as total_spent, COUNT(*) as orders
    FROM main.fct_coupons
    WHERE user_id = {user_id:UInt64}
    """
    # Execute via clickhouse_connect
    # Format response
    return {"answer": "...", "sources": []}

# 3. Route in answer_personal_query()
```

**Add test case** to `queries.json`:

```json
{
  "id": "personal_spending_summary",
  "category": "personal_query",
  "query": "How much have I spent total?",
  "expected_source": "personal",
  "expected_keywords": ["spent", "total"],
  "lang": "en"
}
```

### 3. New Direct Answer Type

**File**: `rag_engine.py`

In `RagEngine.answer()`, add intent detection and template:

```python
# Detect intent
if self._is_new_intent(query):
    candidate = self._retrieve_top(query)
    if candidate.score > config.NEW_DIRECT_ANSWER_SCORE:
        return self._build_new_direct_answer(candidate, lang)

# Add threshold to config.py
NEW_DIRECT_ANSWER_SCORE = 0.35
```

### 4. New Evaluation Category

**File**: `queries.json`

Add test cases with new `category` value.

**File**: `common.py` — `run_one()` handles assertions generically by `expected_source`, `expected_id`, keywords.

---

## 📝 Code Style & Standards

### Type Hints

**Required** on all public functions:

```python
def answer(
    self,
    query: str,
    history: list[dict[str, str]] | None = None,
    recent_offers: list[dict] | None = None,
) -> Answer:
    ...
```

### Docstrings (Google Style)

```python
def retrieve(self, query: str, k: int = 10) -> list[RetrievalResult]:
    """Retrieve top-k documents for a query.

    Args:
        query: User's question text.
        k: Number of results to return.

    Returns:
        List of RetrievalResult sorted by score descending.

    Raises:
        ValueError: If query is empty.
    """
```

### Logging

```python
log = logging.getLogger("waffarha-app")

# Levels
log.debug("Detailed diagnostic info")
log.info("General operation: %s", action)
log.warning("Unexpected but handled: %s", issue)
log.error("Failed operation: %s", error, exc_info=True)
```

### Imports Order

```python
# 1. Standard library
import os
import json
from typing import List, Optional

# 2. Third-party
import ollama
from sentence_transformers import SentenceTransformer
from fastapi import FastAPI

# 3. Local
import config
from vectorstores import get_store
from memory import get_memory_store
```

---

## 🔍 Code Navigation Tips

### Key Entry Points

| Task | Start Here |
|------|------------|
| Understand chat flow | `app.py` → `/api/chat` → `RagEngine.answer()` |
| Retrieval logic | `rag_engine.py` → `retrieve()` → `vectorstores.py` |
| Direct answers | `rag_engine.py` → `_try_direct_answer()` |
| LLM fallback | `rag_engine.py` → `_generate_with_llm()` |
| Personal queries | `personal_queries.py` → `answer_personal_query()` |
| Session memory | `memory.py` → `MemoryStore` |
| Index building | `ingest/build_index.py` → `build_index()` |
| Evaluation | `run_eval.py` → `common.run_one()` |

### Search Patterns

```bash
# Find all direct answer handlers
grep -n "DIRECT_ANSWER_SCORE" *.py

# Find all intent patterns
grep -n "_PATTERNS" personal_queries.py rag_engine.py

# Find config usage
grep -n "config\." *.py | head -30
```

---

## 📊 Performance Profiling

### Profile Retrieval Latency

```python
# In rag_engine.py retrieve()
import time
t0 = time.time()
results = self.store.search(embedding, k)
log.info("Retrieval took %.1fms for %d results", (time.time()-t0)*1000, len(results))
```

### Profile LLM Generation

```python
# In _generate_with_llm()
t0 = time.time()
response = ollama.chat(...)
log.info("LLM generation: %.1fms, %d tokens", (time.time()-t0)*1000, len(response.message.content))
```

### Memory Profiling

```bash
# Install
pip install memray

# Profile
memray run run_eval.py
memray summary memray-results.bin
```

---

## 🧹 Pre-Commit Checks

### Automated (Pre-commit Hook)

Create `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        additional_dependencies: [types-requests]
```

Install: `pre-commit install`

### Manual Checklist Before Commit

- [ ] `ruff check .` — no lint errors
- [ ] `ruff format .` — code formatted
- [ ] `mypy app.py rag_engine.py config.py` — type clean
- [ ] `python run_eval.py` — eval passes (or known failures documented)
- [ ] New test cases added for new features
- [ ] `.env.example` updated if new config vars added

---

## 📦 Dependency Management

### Adding a Dependency

1. Add to `requirements.txt` with version pin:
   ```
   new-package>=1.2,<2.0
   ```

2. If optional (e.g., new vector backend), add comment:
   ```
   # Optional: Qdrant backend
   qdrant-client>=1.9
   ```

3. Update `Dockerfile` if system deps needed

### Updating Dependencies

```bash
# Check outdated
pip list --outdated

# Update (test thoroughly!)
pip install -U package-name
# Update requirements.txt
pip freeze > requirements.txt  # Or edit manually
```

---

## 🌿 Git Workflow

### Branch Naming

| Type | Prefix | Example |
|------|--------|---------|
| Feature | `feature/` | `feature/qdrant-backend` |
| Bugfix | `fix/` | `fix/arabic-encoding` |
| Refactor | `refactor/` | `refactor/memory-interface` |
| Docs | `docs/` | `docs/api-reference` |
| Chore | `chore/` | `chore/update-deps` |

### Commit Messages

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`

Example:
```
feat(rag): add Qdrant vector backend support

- Implement QdrantStore in vectorstores.py
- Add cosine similarity conversion for Qdrant scores
- Register in get_store() factory
- Update build_index.py CLI

Closes #123
```

---

## 📚 Useful Commands Reference

### Data Pipeline

```bash
# Fetch fresh offers (needs WAFFARHA_SECURITY_KEY)
python ingest/fetch_offers.py

# Fetch from ClickHouse (needs CLICKHOUSE_* config)
python ingest/fetch_offers_clickhouse.py
python ingest/fetch_partners_clickhouse.py
python ingest/fetch_payment_methods_clickhouse.py --write

# Build index
python ingest/build_index.py --backend faiss
python ingest/build_index.py --backend chroma --embedding-model intfloat/multilingual-e5-small

# Rebuild all combos for benchmarking
for model in small base large; do
  for backend in faiss chroma; do
    python ingest/build_index.py --backend $backend --embedding-model intfloat/multilingual-e5-$model
  done
done
```

### Evaluation

```bash
# Full eval with custom tag
python run_eval.py --tag "pr-123-qdrant" --backend qdrant

# Compare two runs
python -c "
import json
with open('eval/results/run1/full.json') as f: r1 = json.load(f)
with open('eval/results/run2/full.json') as f: r2 = json.load(f)
for a,b in zip(r1,r2):
    if a['passed'] != b['passed']:
        print(f'{a[\"id\"]}: {a[\"passed\"]} -> {b[\"passed\"]}')
"
```

### Docker (Local)

```bash
# Build with custom model
OLLAMA_MODEL=qwen2.5:1.5b-instruct docker compose build ollama

# Run with local data mounted
docker compose up -d
docker compose logs -f app

# Run eval inside container
docker compose run --rm app python run_eval.py
```

---

## 🤝 Getting Help

| Question | Where |
|----------|-------|
| Architecture | `ARCHITECTURE.md` |
| API reference | `API.md` |
| Docker issues | `DOCKER.md` |
| Evaluation | `EVALUATION.md` |
| Code review | Ask in PR or team Slack |

---

## 📋 Quick Reference Card

```bash
# Start dev stack
ollama serve & docker run -d -p 6379:6379 redis:7-alpine & uvicorn app:app --reload

# Test API
curl -X POST localhost:8000/api/chat -H "Content-Type: application/json" -d '{"message":"test","session_id":"dev"}'

# Run eval
python run_eval.py

# Build index
python ingest/build_index.py

# Lint & type check
ruff check . && mypy app.py rag_engine.py config.py

# View logs
tail -f logs/app.log  # If file logging configured
```