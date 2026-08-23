# Architecture Deep Dive — Waffarha Assistant RAG Pipeline

Comprehensive documentation of the retrieval-augmented generation pipeline, design decisions, and module interactions.

---

## 🎯 System Overview

The Waffarha Assistant implements a **hybrid RAG architecture** with three distinct answer paths:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            USER QUERY                                        │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         INTENT CLASSIFICATION                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │  Personal   │  │   Catalog   │  │    FAQ      │  │   Offer     │        │
│  │   Query?    │──│   Query?    │──│   Match?    │──│   Match?    │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
│         │                │                │                │                │
│         ▼                ▼                ▼                ▼                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ ClickHouse  │  │  Catalog    │  │  Direct     │  │  Direct     │        │
│  │  Live Query │  │  Service    │  │  Answer     │  │  Answer     │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
│                                                                              │
│                    ┌─────────────────────────────────┐                       │
│                    │     LLM FALLBACK (Ollama)       │                       │
│                    │  Retrieval + Generation +       │                       │
│                    │  Fact-Checking                  │                       │
│                    └─────────────────────────────────┘                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 Request Flow (Detailed)

### 1. HTTP Layer (`app.py`)

```python
@app.post("/api/chat")
async def chat(request: ChatRequest):
    # 1. Resolve user identity (for personal queries)
    user_id = identity_resolver.resolve(request)

    # 2. Get session memory (recent offers/FAQs shown)
    recent_offers = memory_store.get(request.session_id)

    # 3. Call RagEngine
    result = engine.answer(
        query=request.message,
        history=request.history,
        recent_offers=recent_offers,
        user_id=user_id,
        lang=request.lang,
    )

    # 4. Update memory with shown items
    if result.sources:
        memory_store.add(request.session_id, result.sources)

    # 5. Return shaped response
    return ChatResponse(
        answer=result.answer,
        sources=[{"title": s.title, "snippet": s.snippet} for s in result.sources]
    )
```

### 2. Identity Resolution (`identity.py`)

```python
# Interface
class IdentityResolver:
    def resolve(self, request) -> int | None: ...

# Backends
- StaticIdentityResolver: Returns fixed STATIC_USER_ID (dev only)
- HeaderIdentityResolver: Reads X-User-ID from trusted proxy header
- SessionIdentityResolver: Reads user_id from Redis session store

# Security: ONLY this module produces user_id for personal queries
```

### 3. Core RAG Pipeline (`rag_engine.py`)

#### Entry Point: `answer()`

```python
def answer(self, query, history=None, recent_offers=None, user_id=None, lang=None):
    # 1. Language detection
    lang = lang or detect_lang(query)

    # 2. PERSONAL QUERY CHECK (highest priority)
    if user_id and is_personal_query(query):
        return answer_personal_query(query, user_id, lang)

    # 3. CATALOG QUERY CHECK (structured data)
    if is_catalog_query(query):
        return answer_catalog_query(query, lang)

    # 4. VECTOR RETRIEVAL
    candidates = self.retrieve(query, k=config.RETRIEVAL_K)

    # 5. DIRECT ANSWER SHORTCUTS (no LLM call)
    #    5a. FAQ direct answer
    faq_result = self._try_faq_direct_answer(candidates, query, lang)
    if faq_result: return faq_result

    #    5b. Offer direct answer
    offer_result = self._try_offer_direct_answer(candidates, query, lang, recent_offers)
    if offer_result: return offer_result

    # 6. LLM FALLBACK with fact-checking
    return self._generate_with_llm(query, candidates, history, lang)
```

---

## 🎯 Intent Classification System

### Personal Query Detection (`personal_queries.py`)

```python
# Regex patterns matched against query (case-insensitive)
_PERSONAL_PATTERNS = [
    (r"\bmy\s+(coupons?|vouchers?)\b", "list_coupons"),
    (r"\b(status|state)\s+of\s+my\s+(order|coupon)\b", "coupon_status"),
    (r"\bhow\s+much\s+(did\s+i\s+)?(spend|paid)\b", "spending_summary"),
    (r"\bmy\s+orders?\b", "list_orders"),
    # ... more patterns
]

def is_personal_query(query: str) -> tuple[bool, str | None]:
    for pattern, intent in _PERSONAL_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return True, intent
    return False, None
```

**Key property**: Runs BEFORE vector retrieval — personal queries never hit the index.

### Catalog Query Detection (`ingest/catalog_queries.py`)

```python
# Handles structured questions about merchants, categories, payment methods
_CATALOG_PATTERNS = [
    (r"\b(merchants?|partners?)\b.*\b(in|at|near)\b", "merchants_by_location"),
    (r"\bwhat\s+(categories?|types?)\b", "list_categories"),
    (r"\bpayment\s+methods?\b", "list_payment_methods"),
    # ...
]
```

### FAQ vs Offer Classification (Vector Retrieval + Heuristics)

After retrieval, candidates are classified by metadata:

```python
def _classify_candidates(candidates):
    faqs = [c for c in candidates if c.metadata.get("type") == "faq"]
    offers = [c for c in candidates if c.metadata.get("type") == "offer"]
    return faqs, offers
```

---

## 🔍 Vector Retrieval (`vectorstores.py`)

### Unified Interface

```python
class VectorStore(ABC):
    @abstractmethod
    def add(self, embeddings: np.ndarray, metadatas: list[dict]): ...

    @abstractmethod
    def search(self, query_embedding: np.ndarray, k: int) -> list[tuple[float, dict]]:
        """MUST return cosine similarity in [-1, 1]"""
        ...

    @abstractmethod
    def persist(self): ...
```

### Backend-Specific Score Normalization

| Backend | Native Score | Conversion to Cosine Similarity |
|---------|--------------|----------------------------------|
| FAISS (IndexFlatIP) | Inner product | Already cosine (vectors L2-normalized) |
| Chroma | Cosine distance | `1 - distance` |
| Qdrant | Cosine similarity | Direct (already cosine) |
| LanceDB | Cosine distance | `1 - distance` |
| pgvector | Cosine distance | `1 - distance` |

**Critical**: All backends MUST return cosine similarity so config thresholds work identically.

### Retrieval Process (`rag_engine.py:retrieve()`)

```python
def retrieve(self, query: str, k: int = 10) -> list[RetrievalResult]:
    # 1. Embed query
    query_embedding = self.embedder.encode([query], normalize_embeddings=True)[0]

    # 2. Vector search
    raw_results = self.store.search(query_embedding, k * 2)  # Over-fetch

    # 3. Lexical bonus (BM25-style)
    for score, meta in raw_results:
        lexical_bonus = self._lexical_overlap(query, meta.get("text", ""))
        score += config.LEXICAL_BONUS_WEIGHT * lexical_bonus

    # 4. Filter by minimum relevance
    filtered = [(s, m) for s, m in raw_results if s >= config.MIN_RELEVANCE_SCORE]

    # 5. Deduplicate by content hash
    seen = set()
    deduped = []
    for score, meta in filtered:
        content_hash = hash(meta.get("text", "")[:200])
        if content_hash not in seen:
            seen.add(content_hash)
            deduped.append(RetrievalResult(score=score, metadata=meta))

    return deduped[:k]
```

### Lexical Bonus

```python
def _lexical_overlap(self, query: str, text: str) -> float:
    """Simple token overlap bonus for exact term matches."""
    query_tokens = set(re.findall(r"\w+", query.lower()))
    text_tokens = set(re.findall(r"\w+", text.lower()))
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & text_tokens) / len(query_tokens)
    return overlap  # [0, 1]
```

---

## ⚡ Direct Answer Shortcuts

These bypass the LLM entirely for speed and reliability.

### FAQ Direct Answer (`_try_faq_direct_answer`)

```python
def _try_faq_direct_answer(self, candidates, query, lang):
    faqs = [c for c in candidates if c.metadata.get("type") == "faq"]
    if not faqs:
        return None

    top_faq = max(faqs, key=lambda x: x.score)

    # Threshold check
    if top_faq.score < config.FAQ_DIRECT_ANSWER_SCORE:
        return None

    # Language match check
    if lang == "ar" and not top_faq.metadata.get("answer_ar"):
        return None  # Don't serve English FAQ to Arabic user

    # Build answer from FAQ metadata
    answer_text = top_faq.metadata.get(f"answer_{lang}") or top_faq.metadata["answer_en"]

    return Answer(
        answer=answer_text,
        sources=[Source(title=top_faq.metadata["question_en"], snippet=answer_text[:200])],
        source_type="faq",
        source_id=top_faq.metadata["id"],
    )
```

### Offer Direct Answer (`_try_offer_direct_answer`)

```python
def _try_offer_direct_answer(self, candidates, query, lang, recent_offers):
    offers = [c for c in candidates if c.metadata.get("type") == "offer"]
    if not offers:
        return None

    # Try to match specific offer from query or recent_offers (follow-up)
    target_offer = self._resolve_offer_target(query, offers, recent_offers)
    if not target_offer:
        return None

    if target_offer.score < config.OFFER_DIRECT_ANSWER_SCORE:
        return None

    # Build structured offer answer
    return self._build_offer_answer(target_offer, lang)
```

### Follow-Up Resolution (`_resolve_offer_target`)

```python
def _resolve_offer_target(self, query, offers, recent_offers):
    # 1. Explicit mention in query (merchant name, price)
    for offer in offers:
        if self._matches_query(offer, query):
            return offer

    # 2. Recent offers from session memory (follow-up context)
    if recent_offers:
        for recent in recent_offers:
            for offer in offers:
                if offer.metadata["id"] == recent["offer_id"]:
                    return offer

    # 3. Highest scoring offer (fallback)
    return max(offers, key=lambda x: x.score) if offers else None
```

---

## 🤖 LLM Fallback Generation (`_generate_with_llm`)

### Prompt Construction

```python
def _generate_with_llm(self, query, candidates, history, lang):
    # 1. Build context from top candidates
    context_blocks = []
    for i, cand in enumerate(candidates[:config.LLM_CONTEXT_K]):
        text = cand.metadata.get(f"text_{lang}") or cand.metadata["text_en"]
        context_blocks.append(f"[Source {i+1}] {text}")

    context = "\n\n".join(context_blocks)

    # 2. Build messages
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # Add history (last N turns)
    if history:
        for turn in history[-config.HISTORY_TURNS:]:
            messages.append({"role": turn["role"], "content": turn["content"]})

    # Current query with context
    messages.append({
        "role": "user",
        "content": f"CONTEXT:\n{context}\n\nQUESTION: {query}"
    })

    # 3. Call Ollama
    response = ollama.chat(
        model=self.llm_model,
        messages=messages,
        options={
            "temperature": self.temperature,
            "num_ctx": config.OLLAMA_NUM_CTX,
            "num_predict": config.OLLAMA_NUM_PREDICT,
        }
    )

    answer_text = response["message"]["content"]

    # 4. FACT-CHECKING (critical for hallucination prevention)
    answer_text = self._fact_check_answer(answer_text, candidates, lang)

    return Answer(
        answer=answer_text,
        sources=self._candidates_to_sources(candidates[:3]),
        source_type="llm",
        source_id=None,
    )
```

### Fact-Checking (`_fact_check_answer`)

```python
def _fact_check_answer(self, answer, candidates, lang):
    """Verify specific claims in answer against retrieved sources."""

    # Extract verifiable claims (prices, discounts, percentages, dates)
    claims = self._extract_claims(answer)

    for claim in claims:
        # Check if claim is supported by any candidate
        supported = any(self._claim_supported(claim, c, lang) for c in candidates)

        if not supported:
            # Remove or flag unsupported claim
            answer = answer.replace(claim, "[unverified]")

    return answer
```

---

## 💾 Session Memory (`memory.py`)

### Purpose

Stores **which specific offers/FAQs were SHOWN to the user** (not just retrieved) for follow-up resolution.

```python
# What's stored per session_id:
{
    "offers": [
        {"offer_id": 8119, "title": "KFC", "price_before": 500, "price_after": 189, "discount": 57}
    ],
    "faqs": [
        {"id": "faq_1", "question": "How to register", "category": "account"}
    ],
    "updated_at": "2024-01-15T10:30:00Z"
}
```

### Backends

| Backend | Use Case | Consistency |
|---------|----------|-------------|
| Redis (default) | Production, multi-worker | Strong (shared) |
| Local (dict) | Dev, single-worker | Process-local only |

### Integration Flow

```python
# In app.py chat endpoint
result = engine.answer(...)

# After answer generated, record what was SHOWN
if result.sources:
    shown_items = []
    for src in result.sources:
        if src.source_type == "offer":
            shown_items.append({"type": "offer", "data": src.metadata})
        elif src.source_type == "faq":
            shown_items.append({"type": "faq", "data": src.metadata})

    memory_store.add(session_id, shown_items)
```

---

## 📊 Data Model

### FAQ Document (Indexed)

```json
{
  "id": "faq_1",
  "type": "faq",
  "question_en": "How do I register?",
  "answer_en": "Visit the app and click Register...",
  "question_ar": "كيف أسجل؟",
  "answer_ar": "زور التطبيق واضغط تسجيل...",
  "category": "account",
  "text_en": "How do I register? Visit the app and click Register...",
  "text_ar": "كيف أسجل؟ زور التطبيق واضغط تسجيل..."
}
```

### Offer Document (Indexed)

```json
{
  "id": 8119,
  "type": "offer",
  "title_en": "KFC Bucket Meal",
  "title_ar": "وجبة كنتاكي",
  "merchant_en": "KFC",
  "merchant_ar": "كنتاكي",
  "category": "food",
  "price_before": 500,
  "price_after": 189,
  "discount": 57,
  "description_en": "Bucket meal with 8 pieces...",
  "description_ar": "وجبة دلو بـ 8 قطع...",
  "text_en": "KFC Bucket Meal - 500 EGP now 189 EGP (57% off)...",
  "text_ar": "كنتاكي وجبة دلو - 500 جنيه الآن 189 جنيه (خصم 57%)..."
}
```

### Retrieval Result

```python
@dataclass
class RetrievalResult:
    score: float              # Cosine similarity [-1, 1]
    metadata: dict            # Full document metadata
```

### Answer Response

```python
@dataclass
class Answer:
    answer: str
    sources: list[Source]
    source_type: str          # "faq" | "offer" | "personal" | "catalog" | "llm"
    source_id: int | str | None

@dataclass
class Source:
    title: str
    snippet: str
    metadata: dict | None = None
```

---

## ⚙️ Configuration-Driven Thresholds (`config.py`)

All thresholds are externalized for tuning without code changes:

| Config | Default | Purpose |
|--------|---------|---------|
| `MIN_RELEVANCE_SCORE` | 0.22 | Global retrieval floor |
| `LEXICAL_BONUS_WEIGHT` | 0.08 | Token overlap boost |
| `FAQ_DIRECT_ANSWER_SCORE` | 0.38 | FAQ shortcut trigger |
| `OFFER_DIRECT_ANSWER_SCORE` | 0.30 | Offer shortcut trigger |
| `RETRIEVAL_K` | 10 | Initial vector search k |
| `LLM_CONTEXT_K` | 5 | Candidates passed to LLM |
| `HISTORY_TURNS` | 4 | Chat history turns to include |
| `OLLAMA_NUM_CTX` | 2048 | LLM context window |
| `OLLAMA_NUM_PREDICT` | 256 | Max generation tokens |

---

## 🔧 Design Decisions & Trade-offs

### Why Hybrid (Direct Answers + LLM)?

| Approach | Pros | Cons |
|----------|------|------|
| Pure LLM | Flexible, handles anything | Slow, hallucinates, expensive |
| Pure Templates | Fast, accurate, controllable | Brittle, limited coverage |
| **Hybrid (Chosen)** | Best of both: 70%+ direct answers, LLM for rest | More complex logic |

**Metrics**: ~75% of queries resolved via direct answers (sub-100ms), ~25% use LLM (500ms-2s).

### Why Cosine Similarity Normalization?

Different vector databases report different native scores:
- FAISS IndexFlatIP: Inner product (cosine for normalized vectors)
- Chroma/Qdrant: Cosine similarity directly
- LanceDB/pgvector: Cosine **distance** (1 - similarity)

**Solution**: `vectorstores.py` normalizes ALL backends to cosine similarity in [-1, 1]. Thresholds in `config.py` are meaningful and portable.

### Why Separate Personal Query Path?

| Alternative | Problem |
|-------------|---------|
| Embed user data in index | Stale instantly; privacy violation; index explosion |
| RAG over user docs | Same issues; doesn't support aggregation queries |
| **Live ClickHouse (Chosen)** | Always fresh; scoped by trusted user_id; supports SQL analytics |

### Why Session Memory (Not Just Chat History)?

| Approach | Limitation |
|----------|------------|
| LLM context only | Small models (1.5B-3B) lose track; context window limited |
| Full chat history to LLM | Wastes tokens; model still infers poorly |
| **Structured memory (Chosen)** | Exact offer/FAQ references; deterministic follow-up resolution; tiny storage |

---

## 📈 Scaling Considerations

### Horizontal Scaling

```
                    ┌─────────────┐
                    │ Load Balancer│
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
     ┌─────────┐      ┌─────────┐      ┌─────────┐
     │ App #1  │      │ App #2  │      │ App #3  │
     │(uvicorn)│      │(uvicorn)│      │(uvicorn)│
     └────┬────┘      └────┬────┘      └────┬────┘
          │                │                │
          └────────────────┼────────────────┘
                           │
                    ┌──────┴──────┐
                    │   Redis     │  ← Shared session memory
                    │  (Cluster)  │
                    └─────────────┘
                           │
                    ┌──────┴──────┐
                    │  ClickHouse │  ← Personal queries
                    │  (Cluster)  │
                    └─────────────┘
```

### Stateless App Requirement

- **No in-process state** — use `MEMORY_BACKEND=redis`
- **Identity resolver** must be header/session based (not static)
- **Vector store** must be shared (Qdrant/pgvector) or each worker builds own FAISS index

### Index Sharding (Future)

For very large catalogs:
```
data/index/
├── shard_0/     # Categories 1-10
├── shard_1/     # Categories 11-20
└── shard_2/     # Categories 21-30
```

Route queries to relevant shard(s) based on intent classification.

---

## 🧪 Evaluation Architecture

### Test Case Design (`queries.json`)

```json
{
  "id": "unique_id",
  "category": "offer_direct_answer | faq_direct_answer | personal_query | followup | negative | catalog",
  "query": "User question",
  "history": [...],           // For follow-up tests
  "recent_offers": [...],     // For follow-up context
  "expected_source": "faq|offer|personal|catalog|llm",
  "expected_id": 123,         // Exact match expected
  "expected_keywords": [...], // Must appear in answer
  "forbidden_keywords": [...],// Must NOT appear
  "lang": "en|ar"
}
```

### Evaluation Runner (`run_eval.py` + `common.py`)

```python
# common.py
def run_one(engine, case):
    result = engine.answer(case["query"], history, recent_offers)

    # Assertions
    checks = {
        "source_match": result.source_type == case["expected_source"],
        "id_match": result.source_id == case["expected_id"],
        "keywords": all(kw.lower() in result.answer.lower() for kw in case.get("expected_keywords", [])),
        "forbidden": not any(kw.lower() in result.answer.lower() for kw in case.get("forbidden_keywords", [])),
    }

    return {
        "id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "latency_ms": ...,
        "answer": result.answer,
        # ...
    }
```

### CI Integration

Exit code = 1 if ANY assertion fails → blocks merge on failure.

---

## 🔮 Future Architecture Evolution

### Planned Improvements

| Area | Current | Target |
|------|---------|--------|
| **Reranking** | Lexical bonus only | Cross-encoder reranker (e.g., `bge-reranker-base`) |
| **Query Expansion** | None | HyDE / multi-query retrieval |
| **Multilingual** | Shared index | Language-specific indexes + language router |
| **Streaming** | Blocking LLM call | Token streaming via SSE/WebSocket |
| **Caching** | None | Redis cache for frequent queries + embeddings |
| **Observability** | Basic logging | Structured logs + metrics + tracing (OpenTelemetry) |

### Extension Points

1. **New Vector Backend** → Implement `VectorStore` ABC
2. **New Intent Type** → Add pattern in `personal_queries.py` or `catalog_queries.py`
3. **New Direct Answer** → Add handler in `RagEngine.answer()` before LLM fallback
4. **New Evaluation Metric** → Extend `run_one()` in `common.py`

---

## 📚 Related Documentation

- **[README.md](README.md)** — Project overview & quick start
- **[DEVELOPMENT.md](DEVELOPMENT.md)** — Local development workflow
- **[DOCKER.md](DOCKER.md)** — Deployment guide
- **[EVALUATION.md](EVALUATION.md)** — Evaluation methodology
- **[API.md](API.md)** — API reference