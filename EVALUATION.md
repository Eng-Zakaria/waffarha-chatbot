# Evaluation Methodology & Results — Waffarha Assistant

Comprehensive documentation of the evaluation framework, test case design, metrics, and model performance benchmarks.

---

## 🎯 Evaluation Philosophy

| Principle | Implementation |
|-----------|----------------|
| **Regression Prevention** | Every PR must pass full eval suite |
| **Realistic Queries** | Test cases from actual user logs + edge cases |
| **Deterministic Assertions** | Exact ID match, keyword presence/absence |
| **Multi-Dimensional** | Tests cover retrieval, direct answers, LLM fallback, follow-ups, negatives |
| **CI-Ready** | Exit code 1 on any failure — blocks merge |

---

## 📊 Test Suite Composition (`queries.json`)

### Current Statistics (100+ Test Cases)

| Category | Count | Description |
|----------|-------|-------------|
| `offer_direct_answer` | ~25 | Specific offer price/discount/merchant queries |
| `faq_direct_answer` | ~20 | Account, purchase, refund, contact FAQs |
| `personal_query` | ~15 | My coupons, order status, spending (static user) |
| `followup` | ~15 | "How much before discount?" after offer answer |
| `catalog_query` | ~10 | Merchants, categories, payment methods |
| `negative` | ~10 | Disabled offers, out-of-scope, fabrications |
| `arabic` | ~15 | Arabic queries, RTL, cross-lingual |
| **Total** | **~110** | |

### Test Case Schema

```json
{
  "id": "offer_direct_answer_kfc",
  "category": "offer_direct_answer",
  "query": "How much is the KFC offer?",
  "expected_source": "offer",
  "expected_id": 8119,
  "expected_keywords": ["189", "EGP"],
  "forbidden_keywords": [],
  "lang": "en",
  "note": "Direct offer price query - should trigger offer direct answer"
}
```

### Assertion Types

| Assertion | Purpose | Example |
|-----------|---------|---------|
| `expected_source` | Correct answer path | `"offer"` vs `"faq"` vs `"llm"` |
| `expected_id` | Exact document match | `8119` (KFC offer) |
| `expected_keywords` | Required content | `["189", "EGP"]` |
| `forbidden_keywords` | Hallucination prevention | `["175"]` (wrong price) |
| `lang` | Language routing | `"en"` or `"ar"` |

---

## 🏃 Running Evaluations

### Basic Run

```bash
python run_eval.py
```

### With Overrides

```bash
# Different embedding model
python run_eval.py --embedding-model intfloat/multilingual-e5-small

# Different vector backend
python run_eval.py --backend chroma

# Different LLM
python run_eval.py --llm-model qwen2.5:3b-instruct --temperature 0.0

# Custom query file
python run_eval.py --queries my_custom_tests.json

# Tagged run (for comparison)
python run_eval.py --tag "pr-123-qdrant"
```

### Output Structure

```
eval/results/
├── 20240115_143022_pr-123-qdrant/
│   ├── full.json      # Complete results per query
│   └── summary.csv    # Spreadsheet-friendly summary
└── 20240115_143500_default/
    ├── full.json
    └── summary.csv
```

### `full.json` Schema

```json
[
  {
    "id": "offer_direct_answer_kfc",
    "category": "offer_direct_answer",
    "query": "How much is the KFC offer?",
    "passed": true,
    "checks": {
      "source_match": true,
      "id_match": true,
      "keywords": true,
      "forbidden": true
    },
    "latency_ms": 45,
    "answer": "The KFC Bucket Meal is 189 EGP (was 500 EGP, 57% off)...",
    "source_type": "offer",
    "source_id": 8119,
    "retrieval_scores": [0.42, 0.38, 0.31],
    "llm_used": false
  }
]
```

### `summary.csv` Columns

| Column | Description |
|--------|-------------|
| `id` | Test case ID |
| `category` | Test category |
| `query` | User query |
| `passed` | Overall pass/fail |
| `source_match` | Source type correct? |
| `id_match` | Exact ID match? |
| `keywords` | All keywords present? |
| `forbidden` | No forbidden keywords? |
| `latency_ms` | End-to-end latency |
| `llm_used` | LLM fallback triggered? |
| `answer` | Generated answer (truncated) |

---

## 📈 Model Performance Benchmarks

### Embedding Model Comparison

**Test Setup**: Same 110 queries, FAISS backend, `qwen2.5:1.5b-instruct` LLM

| Model | Dimensions | Index Size | Build Time | Avg Retrieval Score (Top-1) | Recall@10 | Direct Answer Rate |
|-------|------------|------------|------------|----------------------------|-----------|-------------------|
| `intfloat/multilingual-e5-small` | 384 | 45 MB | 45s | 0.31 | 0.72 | 68% |
| `intfloat/multilingual-e5-base` | 768 | 180 MB | 2min | **0.38** | **0.84** | **76%** |
| `intfloat/multilingual-e5-large` | 1024 | 480 MB | 6min | 0.41 | 0.87 | 78% |

**Recommendation**: `multilingual-e5-base` — best quality/size/speed trade-off for Arabic+English.

### Vector Backend Comparison

**Test Setup**: `multilingual-e5-base`, same queries

| Backend | Index Type | Build Time | Search Latency (p95) | Recall@10 | Notes |
|---------|------------|------------|---------------------|-----------|-------|
| FAISS (IndexFlatIP) | Exact | 2min | **8ms** | 0.84 | Default, fastest |
| Chroma | HNSW | 3min | 15ms | 0.83 | Persistent, metadata filtering |
| Qdrant | HNSW | 3min | 12ms | 0.84 | Production-ready, scaling |
| LanceDB | IVF | 4min | 20ms | 0.82 | Columnar, good for analytics |
| pgvector | IVFFlat | 5min | 25ms | 0.81 | SQL integration, ACID |

**Recommendation**: FAISS for single-node; Qdrant for multi-node production.

### LLM Model Comparison (Ollama)

**Test Setup**: `multilingual-e5-base` + FAISS, 25 LLM-fallback queries only

| Model | Size | Speed (tok/s) | Quality Score* | Hallucination Rate | Memory (VRAM/RAM) |
|-------|------|---------------|----------------|-------------------|-------------------|
| `qwen2.5:1.5b-instruct` | 1 GB | 45 | 7.2/10 | 12% | 2 GB |
| `qwen2.5:3b-instruct` | 2 GB | 25 | **8.5/10** | **5%** | 4 GB |
| `qwen2.5:7b-instruct` | 4.5 GB | 12 | 9.1/10 | 3% | 8 GB |
| `llama3.2:3b-instruct` | 2 GB | 20 | 8.0/10 | 7% | 4 GB |
| `gemma2:2b-instruct` | 1.6 GB | 35 | 7.5/10 | 10% | 3 GB |

*Quality Score: Human eval on 25 LLM-fallback cases (coherence, accuracy, completeness)

**Recommendation**: `qwen2.5:3b-instruct` for production; `1.5b` for constrained environments.

### Temperature Impact

| Temperature | Direct Answer Rate | LLM Quality | Hallucination Rate | Use Case |
|-------------|-------------------|-------------|-------------------|----------|
| 0.0 | 76% | High (deterministic) | 3% | Production default |
| 0.3 | 76% | Good | 5% | Balanced |
| 0.7 | 76% | Creative | 12% | Not recommended |

**Key Finding**: Temperature doesn't affect direct answers (no LLM call). Only impacts LLM fallback quality.

---

## 🎯 Category-Level Results (Current Baseline)

### With `qwen2.5:3b-instruct`, `multilingual-e5-base`, FAISS, temp=0.0

| Category | Tests | Pass Rate | Avg Latency | Notes |
|----------|-------|-----------|-------------|-------|
| `offer_direct_answer` | 25 | **100%** | 42ms | High-confidence threshold works |
| `faq_direct_answer` | 20 | **100%** | 38ms | FAQ threshold well-tuned |
| `personal_query` | 15 | **100%** | 180ms | ClickHouse queries fast |
| `followup` | 15 | **93%** | 55ms | 1 failure: memory not propagated |
| `catalog_query` | 10 | **100%** | 65ms | Catalog service working |
| `negative` | 10 | **90%** | 48ms | 1 false positive on disabled offer |
| `arabic` | 15 | **87%** | 52ms | 2 cross-lingual routing issues |

**Overall**: **96.4% pass rate** (106/110)

### Known Failures (Baseline)

| Test ID | Category | Issue | Root Cause | Fix Planned |
|---------|----------|-------|------------|-------------|
| `followup_memory_loss` | followup | Memory not found | Race condition in Redis write | Add retry/confirm |
| `negative_disabled_offer` | negative | Returns disabled offer | Index contains stale offer | Filter at ingest |
| `arabic_cross_lingual_1` | arabic | English answer for Arabic query | Language detection edge case | Improve `detect_lang` |
| `arabic_cross_lingual_2` | arabic | Wrong FAQ matched | Embedding similarity confusion | Add language filter |

---

## 🔬 Evaluation Methodology Details

### Retrieval Quality Metrics

```python
# Computed per query in run_eval.py
metrics = {
    # Did we retrieve the expected document in top-k?
    "recall_at_k": expected_id in [r.metadata["id"] for r in candidates[:k]],

    # What rank was the expected document?
    "mrr": 1 / (rank + 1) if found else 0,

    # Score distribution
    "top_score": candidates[0].score if candidates else 0,
    "score_gap": candidates[0].score - candidates[1].score if len(candidates) > 1 else 0,
}
```

### Direct Answer Precision/Recall

| Metric | Definition | Target |
|--------|------------|--------|
| **Direct Answer Precision** | Of queries where direct answer triggered, % correct | > 98% |
| **Direct Answer Recall** | Of queries that SHOULD trigger direct answer, % that did | > 90% |
| **LLM Fallback Rate** | % of queries falling to LLM | < 30% |

Current: Precision 99%, Recall 89%, Fallback Rate 24%

### Latency Budgets

| Path | Target (p95) | Current (p95) |
|------|-------------|---------------|
| FAQ Direct Answer | < 50ms | 38ms |
| Offer Direct Answer | < 60ms | 55ms |
| Personal Query | < 300ms | 180ms |
| LLM Fallback | < 2000ms | 1200ms |

---

## 📊 Historical Performance Tracking

### Running Comparisons

```bash
# Compare two eval runs
python -c "
import json, csv, sys

def load_summary(path):
    with open(path) as f:
        return {row['id']: row for row in csv.DictReader(f)}

old = load_summary('eval/results/old/summary.csv')
new = load_summary('eval/results/new/summary.csv')

print(f'{'ID':<40} {'Old':<6} {'New':<6} {'Delta'}')
for id in set(old) | set(new):
    o = old.get(id, {'passed': 'N/A'})
    n = new.get(id, {'passed': 'N/A'})
    if o['passed'] != n['passed']:
        print(f'{id:<40} {o[\"passed\"]:<6} {n[\"passed\"]:<6} {\"REGRESSION\" if o[\"passed\"]==\"True\" else \"FIXED\"}')"
```

### CI Dashboard Data

Each run produces machine-readable results for dashboarding:

```json
{
  "timestamp": "2024-01-15T14:30:22Z",
  "git_sha": "abc123",
  "config": {
    "embedding_model": "intfloat/multilingual-e5-base",
    "backend": "faiss",
    "llm_model": "qwen2.5:3b-instruct",
    "temperature": 0.0
  },
  "summary": {
    "total": 110,
    "passed": 106,
    "failed": 4,
    "pass_rate": 0.964,
    "avg_latency_ms": 78,
    "llm_fallback_rate": 0.24
  },
  "by_category": {
    "offer_direct_answer": {"total": 25, "passed": 25},
    "faq_direct_answer": {"total": 20, "passed": 20},
    // ...
  }
}
```

---

## 🧪 Adding New Test Cases

### From User Logs

```bash
# 1. Export anonymized queries from production logs
# 2. Cluster by intent (manual or automated)
# 3. Select representative samples per cluster
# 4. Add to queries.json with assertions
```

### Template for New Test Case

```json
{
  "id": "category_descriptive_name",
  "category": "offer_direct_answer | faq_direct_answer | personal_query | followup | catalog_query | negative | arabic",
  "query": "Exact user phrasing",
  "history": [
    {"role": "user", "content": "Previous question"},
    {"role": "assistant", "content": "Previous answer"}
  ],
  "recent_offers": [
    {"offer_id": 123, "title": "Offer Name", "price_before": 500, "price_after": 200}
  ],
  "expected_source": "offer",
  "expected_id": 123,
  "expected_keywords": ["200", "EGP", "discount"],
  "forbidden_keywords": ["wrong_price", "competitor_name"],
  "lang": "en",
  "note": "Why this test matters / what it catches"
}
```

### Negative Test Cases (Critical)

```json
{
  "id": "negative_fabricated_price",
  "category": "negative",
  "query": "What's the price of the Asian Wok offer?",
  "expected_source": "llm",
  "expected_keywords": ["don't have", "currently", "Asian Wok"],
  "forbidden_keywords": ["175", "180", "200"],
  "note": "Asian Wok offer is DISABLED in dataset — must not hallucinate a price"
}
```

---

## 🔄 Continuous Evaluation

### Pre-Merge Gate

```yaml
# .github/workflows/eval.yml
name: RAG Evaluation

on:
  pull_request:
    branches: [main]

jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Start Ollama
        run: |
          ollama serve &
          sleep 10
          ollama pull qwen2.5:1.5b-instruct

      - name: Run evaluation
        run: python run_eval.py
```

### Nightly Benchmark

```yaml
# .github/workflows/nightly-benchmark.yml
name: Nightly Benchmark

on:
  schedule:
    - cron: '0 2 * * *'  # 2 AM daily

jobs:
  benchmark:
    runs-on: self-hosted  # Needs GPU for larger models
    steps:
      - uses: actions/checkout@v4

      - name: Run full matrix
        run: |
          for model in small base large; do
            for backend in faiss chroma qdrant; do
              python run_eval.py \
                --embedding-model intfloat/multilingual-e5-$model \
                --backend $backend \
                --tag "nightly-$model-$backend"
            done
          done

      - name: Publish results
        run: |
          # Upload to S3/GCS for dashboard
          aws s3 sync eval/results/ s3://waffarha-eval-results/$(date +%Y-%m-%d)/
```

---

## 📋 Evaluation Checklist for PRs

Before merging any RAG-related change:

- [ ] `python run_eval.py` passes locally
- [ ] New test cases added for new features
- [ ] Negative test cases for edge cases
- [ ] Arabic test cases if language handling changed
- [ ] Follow-up tests if memory logic changed
- [ ] Personal query tests if ClickHouse queries changed
- [ ] No regression in pass rate (>95% baseline)
- [ ] Latency within budgets (check `summary.csv`)

---

## 📚 Related Documentation

- **[README.md](README.md)** — Quick start & project overview
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — RAG pipeline deep dive
- **[DEVELOPMENT.md](DEVELOPMENT.md)** — Local development workflow
- **[API.md](API.md)** — API reference for testing