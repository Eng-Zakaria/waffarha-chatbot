# Hallucination Analysis & Mitigation Plan — Waffarha Chatbot

**Date:** 2026-08-31
**Author:** Claude Code
**Goal:** Ensure the chatbot replies `I don't know` (or the equivalent Arabic phrase) whenever it lacks ground-truth information, instead of fabricating offer details, prices, steps, or policies.

---

## 1. Architecture Overview (where hallucination can enter)

```
User query
   │
   ├─ _looks_like_greeting() ──► Greeting reply (no LLM)
   ├─ _looks_like_gibberish() ──► Clarification reply (no LLM)
   ├─ _looks_like_injection_attempt() ──► FALLBACK_MESSAGE (no LLM)
   ├─ _sanitize_user_query()
   ├─ Personal query? ──► ClickHouse (scoped to user) ──► PERSONAL_ERROR on failure
   ├─ Catalog query? ──► ClickHouse (live) ──► CATALOG_ERROR on failure
   ├─ _unmatched_brand_mention() ──► "We don't currently have any offers from {brand}" (no LLM)
   ├─ _inactive_merchant_mention() ──► same refusal (no LLM)
   ├─ _looks_like_out_of_scope() ──► FALLBACK_MESSAGE (no LLM)
   ├─ _get_superlative_offer_answer() ──► metadata sort (no LLM)
   ├─ _get_faq_direct_answer() ──► metadata answer (no LLM)
   ├─ _get_offer_direct_answer() ──► _format_offer_facts (no LLM)
   ├─ _get_stock_direct_answer() ──► _STOCK_NO_DATA (no LLM)
   └─ NO direct-answer shortcut matched
          │
          ├─ Retrieve → build context → LLM.generate()
          │      │
          │      ├─ fact_check_offer() may append a "Details:" line
          │      └─ answer_stream() yields raw tokens
          └─ answer() collects chunks → _strip_scaffolding_leaks() → _clean_bidi_artifacts()
```

**Key insight:** The system already blocks hallucination at *many* entry points with zero-LLM paths. The remaining risk is concentrated in **three places**:
1. The LLM generation path (when no direct-answer shortcut fires).
2. The *fact_check_offer* post-processing (adds detail lines that the LLM may not have stated).
3. Edge-case queries that slip through every filter and land in the LLM path with weak or misleading retrieved context.

---

## 2. Hallucination Vectors — Exhaustive Inventory

### 2.1 Weak/no retrieval context (LLM is asked to answer from nothing)

| Vector | Where it happens today | Why it's a problem |
|--------|------------------------|--------------------|
| **no_retrieval mode** | `answer_stream()` calls the LLM with *only* the user question, no CONTEXT block | The LLM's pre-trained knowledge fills the gap → it can confidently invent offers, prices, policies. This mode exists specifically for eval comparison but could be accidentally enabled or used in production. |
| **`best_score < MIN_RELEVANCE_SCORE` (0.30)** | Falls through to `FALLBACK_MESSAGE` — this one is *already handled* | ✅ Already blocked. |
| **Retrieved docs all pass the score floor but are semantically irrelevant** | E.g. "how do I reset my password" scores 0.35 against an unrelated FAQ about account creation | The LLM is given a *real, retrieved* context that is *about the wrong topic*. It reads plausible and answers from it, silently answering the wrong question rather than saying "I don't know". |
| **Empty retrieval** (`retrieved = []` after filtering) | `build_context([])` produces `""` — the LLM gets a prompt with `CONTEXT:\n\nUSER QUESTION: ...` | LLM fills the void with training-data knowledge. |

### 2.2 Wrong topic retrieved (honest-looking but misdirected context)

| Vector | Why it's a problem |
|--------|--------------------|
| **FAQ about a *related* topic** | System prompt says "Do not adapt, extend, or guess at steps/prices/details for the user's actual question based on a related-but-different item." But the LLM is *still given* that related FAQ in CONTEXT, and a weak model will answer from it anyway. Example: user asks "How do I create an account?" but the top FAQ is "How do I collect personal data?" (same category, different FAQ). |
| **Same-merchant disambiguation failure** | User asks "How much is Tamara's iftar?" but retrieval returns the Tamara sohour offer (close embedding). `_get_offer_direct_answer` fires and the user gets the *wrong* offer's price — confidently stated. The score guard (`OFFER_DIRECT_ANSWER_SAME_ENTITY_MARGIN`) exists but doesn't catch all cases. |
| **Hallucinated brand** | "What's the discount at Starbucks Egypt?" — previously scored 0.956 against a real unrelated offer. Now blocked by `_unmatched_brand_mention()` + `MERCHANT_FUZZY_MATCH_CUTOFF = 0.8`. ✅ Largely handled. |
| **Out-of-scope that doesn't match patterns** | `_OUT_OF_SCOPE_PATTERNS` is regex-based; anything not matching (e.g. "tell me about climate change" without the word "weather") passes through and reaches the LLM with whatever context retrieved. |

### 2.3 Post-generation fabrication vectors

| Vector | Where it happens |
|--------|-----------------|
| **`fact_check_offer` appending facts the LLM didn't state** | If the LLM omits a fact that `_fact_check_offer` expects, the code appends a "Details:" line at the end with the missing fact verbatim from metadata. This is *ground-truth* data, but it appears as if the LLM said it — and if the LLM's *intro* sentence already stated a *different* (wrong) price, you now have a contradiction in the same reply. |
| **Bidi/control character leaks** | Already handled by `_clean_bidi_artifacts()`. ✅ |
| **Scaffolding label leaks** | Already handled by `_strip_scaffolding_leaks()`. ✅ |
| **LLM repeats a wrong number from context** | The system prompt says "Validate that EVERY number in your response appears exactly as written in the CONTEXT." But this is a *prompt-only* guard, not enforced programmatically for the final answer — the LLM can still output a number present in context that belongs to a *different* offer/doc. |

### 2.4 Prompt-injection / instruction-following failure

| Vector | Status |
|--------|--------|
| Fake `CONTEXT:` / `SYSTEM:` headers in user message | Blocked by `_looks_like_injection_attempt()` + `_sanitize_user_query()` + system-prompt hierarchy line. ✅ Largely handled. |
| "Ignore previous instructions" style attacks | Same as above. ✅ |

### 2.5 Follow-up anchoring failures

| Vector | Why it's a problem |
|--------|--------------------|
| **Follow-up with no pronoun, no signal phrase, no price filter** | E.g. "similar offers" or "عروض مشابهة" — previously free-floated. Now handled by `_FOLLOWUP_SIGNAL_PHRASES`. ✅ |
| **Bare "بكام" / "كام"** | Now caught by `_BARE_PRICE_QUESTION_WORDS` + LLM fallback. ✅ |
| **LLM follow-up fallback (`_maybe_llm_resolve_followup`)** | This itself makes an LLM call. If it misclassifies "NEW" as "SAME", a genuinely new question gets anchored to the wrong cached offer. Falls closed on errors (returns False → treat as fresh). ✅ Mostly safe, but the false-negative side means it sometimes misses a real follow-up. |

### 2.6 Catalog / personal query failures

| Vector | Status |
|--------|--------|
| ClickHouse unreachable | Returns `CATALOG_ERROR` / `PERSONAL_ERROR` — no fabrication. ✅ |
| `CATALOG_QUERIES_ENABLED` / `PERSONAL_QUERIES_ENABLED` | Feature flags; if disabled, these queries fall back to the static RAG index. ✅ |

---

## 3. Recommended Solutions (prioritized)

### Tier 1 — Must fix (high hallucination risk, simple to implement)

#### 3.1 Hard guard on `no_retrieval` mode in production
**Problem:** `no_retrieval=True` makes the LLM answer from its own knowledge with zero grounding.  
**Fix:** In `app.py`'s `chat()` / `chat_stream()`, refuse to run in `no_retrieval` mode unless explicitly allowed via a config flag or admin header. Add to `core/config.py`:

```python
# Prevent the LLM from answering from its own (ungrounded) knowledge in production.
# only set NO_RETRIEVAL_ENABLED=True in eval/comparison contexts.
NO_RETRIEVAL_ENABLED = os.getenv("NO_RETRIEVAL_ENABLED", "false").lower() == "true"
```

In `app.py` and `answer_stream()`:

```python
if engine.no_retrieval and not config.NO_RETRIEVAL_ENABLED:
    yield FALLBACK_MESSAGE[reply_lang]
    return
```

**Why this works:** Forces every production request to go through retrieval, so the LLM always has at least some retrieved context.

---

#### 3.2 Refuse when retrieval returns zero docs OR all docs below a strict floor
**Problem:** Today `MIN_RELEVANCE_SCORE = 0.30` is relatively low; a query that retrieves irrelevant-but-scoring docs can still reach the LLM.  
**Fix:** Add a second, stricter check *after* retrieval to catch the empty-context case:

```python
# In answer_stream(), after retrieve():
if not retrieved:
    yield FALLBACK_MESSAGE[reply_lang]
    return
```

This is partially present (the `best_score < MIN_RELEVANCE_SCORE` check), but `retrieved` can be non-empty with every doc below the strict *grounding* threshold. Consider adding:

```python
# NEW: If the top candidate's score is below this stricter floor,
# refuse outright instead of sending it to the LLM (which would
# hallucinate from weak context).
if best_score < config.MIN_RELEVANCE_SCORE_STRICT:
    yield FALLBACK_MESSAGE[reply_lang]
    return
```

```python
# config.py
MIN_RELEVANCE_SCORE_STRICT = 0.45  # stricter floor for outright refusal
```

---

#### 3.3 Add a retrieval-confidence check *before* the LLM path
**Problem:** Even when `best_score >= MIN_RELEVANCE_SCORE`, the retrieved docs may be about a *different* topic than the query.  
**Fix:** Use the LLM itself (cheaply, before generation) as a *classifier* to decide whether the retrieved context is relevant to the question. Add a pre-generation gate:

```python
# In answer_stream(), after retrieve(), before building context + calling the LLM:
if not self._context_is_relevant(retrieved, query):
    yield FALLBACK_MESSAGE[reply_lang]
    return
```

```python
def _context_is_relevant(self, retrieved: list, query: str) -> bool:
    """Return True only if the top retrieved doc's text looks like it
    actually answers `query`. Uses a cheap LLM call; fails closed
    (returns True) on any error so we never block a valid request."""
    if not retrieved:
        return False
    top = retrieved[0]
    if top["combined_score"] < config.RELEVANCE_CHECK_SCORE:
        return False  # already weak; no need to spend an LLM call
    try:
        resp = self.client.chat(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": (
                    "You are a relevance judge. Given a user question and a "
                    "short retrieved passage, reply with exactly one word: "
                    "RELEVANT or NOT_RELEVANT."
                )},
                {"role": "user", "content": (
                    f"Question: {query}\n\nRetrieved passage: {top['text'][:300]}\n\n"
                    "Answer with exactly one word: RELEVANT or NOT_RELEVANT."
                )},
            ],
            stream=False,
            options={"num_predict": 3, "temperature": 0.0},
        )
        verdict = (resp.get("message", {}).get("content") or "").strip().upper()
        return verdict.startswith("RELEVANT")
    except Exception:
        log.warning("Relevance check failed for query %r; proceeding to LLM.", query)
        return True  # fail closed — better to answer than to block
```

```python
# config.py
RELEVANCE_CHECK_SCORE = 0.55  # skip the LLM relevance check for confident matches
```

**Trade-off:** Adds one extra LLM call per non-direct-answer request (~200–500ms). Use `RELEVANCE_CHECK_SCORE` to skip it for high-confidence matches and reserve it for borderline cases.

---

### Tier 2 — Strongly recommended (medium risk, moderate effort)

#### 3.4 Disambiguate same-merchant candidates with a *content-match* check
**Problem:** `_get_offer_direct_answer` fires on the top candidate even when the user's question is about a *different* offer from the same merchant (e.g., Tamara iftar vs sohour).  
**Fix:** Add a lightweight *content-match* check using the query's specific nouns against the top doc's title/fields before shortcutting:

```python
# In _get_offer_direct_answer / _get_stock_direct_answer:
def _offer_matches_query(doc_meta: dict, query: str) -> bool:
    """Returns True when the doc's title / category / key fields
    contain at least one content word from the query (not a stopword)."""
    q_words = set(re.findall(r"[A-Za-z][A-Za-z\s]{2,}", query.lower()))
    # also extract any digits (prices, quantities)
    q_words |= set(re.findall(r"\d+", query))
    if not q_words:
        return True  # can't meaningfully check
    title_words = set(re.findall(r"[A-Za-z][A-Za-z\s]{2,}", (doc_meta.get("title") or "").lower()))
    title_words |= set(re.findall(r"\d+", str(doc_meta.get("price", "")) or ""))
    common = q_words & title_words
    # require at least one shared content word of length >= 3
    return any(len(w) >= 3 for w in common)
```

If `not _offer_matches_query(top["metadata"], query)`:
- Don't shortcut.
- Fall through to the LLM path where *all* retrieved docs (including the right one, if present) are in context.

---

#### 3.5 Strengthen the out-of-scope detector
**Problem:** `_OUT_OF_SCOPE_PATTERNS` is regex-based and misses anything not explicitly listed.  
**Fix:** Add a cheap LLM-based out-of-scope classifier as a Tier-1 fallback for queries that pass all rule-based filters but look generic:

```python
def _looks_like_out_of_scope_llm(query: str) -> bool:
    """Secondary classifier for queries the regex _OUT_OF_SCOPE_PATTERNS
    didn't catch. Cheap, single-shot, fails closed (returns False)."""
    try:
        resp = self.client.chat(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": (
                    "You classify one short user message from a deals/coupons "
                    "chatbot for the Waffarha platform. Reply with exactly one "
                    "word: IN_SCOPE or OUT_OF_SCOPE."
                )},
                {"role": "user", "content": (
                    "The user asked: \"" + query + "\"\n\n"
                    "Is this a question about Waffarha offers, orders, "
                    "cashback, returns, payments, or account help? "
                    "Or is it about something completely unrelated "
                    "(general knowledge, weather, medical, competitors, "
                    "programming, etc.)? Reply with exactly one word: "
                    "IN_SCOPE or OUT_OF_SCOPE."
                )},
            ],
            stream=False,
            options={"num_predict": 3, "temperature": 0.0},
        )
        verdict = (resp.get("message", {}).get("content") or "").strip().upper()
        return not verdict.startswith("IN_SCOPE")
    except Exception:
        return False  # fail closed
```

Wire it in `answer_stream()` after the existing `_looks_like_out_of_scope()` check:

```python
if _looks_like_out_of_scope(query) or _looks_like_out_of_scope_llm(query):
    yield FALLBACK_MESSAGE.get(detect_lang(query), FALLBACK_MESSAGE["en"])
    return
```

---

#### 3.6 Fix the `fact_check_offer` post-processing to avoid contradictions
**Problem:** `_fact_check_offer` appends a "Details:" line with facts from metadata *even when the LLM already stated a different (wrong) number*. This creates a contradictory answer.  
**Fix:** Change the post-processing to *replace* any wrong fact already in the answer rather than append:

```python
# After the LLM generates full_text, and missing_facts are found:
# Only append the fact-checklist line if the LLM's answer already
# mentions the correct numbers. If it stated a wrong number,
# the fact_checklist should OVERRIDE, not supplement.

if missing_facts:
    # Check whether the LLM already stated the correct numbers
    # (i.e., it got the right answer but phrased it differently).
    # If the LLM stated numbers that don't match any fact,
    # we should not blindly append — instead, the fact_checklist
    # line serves as the authoritative correction.
    lang = detect_lang(full_text) if full_text else detect_lang(query)
    header = "\n\n📋 " + ("Details: " if lang == "en" else "التفاصيل: ")
    yield header + " | ".join(missing_facts)
```

Additionally, **add a safety rule**: if the LLM's generated text contains a number that conflicts with the retrieved metadata's number, the fact-check line should be phrased as a correction, not a supplement:

```python
# In _fact_check_offer: if the answer text contains a number that
# conflicts with the doc's price/discount, emit a correction line
# instead of a supplement line. This prevents contradictions.
```

---

### Tier 3 — Good-to-have (lower risk, higher effort)

#### 3.7 Add a "confidence score" field to ChatResponse
Expose `confidence: float` in the `ChatResponse` model so the frontend can render a "I'm not sure" indicator when retrieval confidence is low:

```python
class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceCard] = []
    suggestions: List[str] = []
    confidence: float = 1.0  # 0.0–1.0; below 0.4 → frontend may preface with "I'm not sure"
```

Compute it from `best_score / MIN_RELEVANCE_SCORE`, capped at 1.0.

---

#### 3.8 Create a project-specific test suite
The `tests/` directory is empty of project-specific tests. Add at minimum:

- `tests/unit/test_rag_engine_direct_answers.py` — unit tests for `_get_offer_direct_answer`, `_get_faq_direct_answer`, `_get_stock_direct_answer`, `_get_superlative_offer_answer` with mocked `RagEngine`.
- `tests/unit/test_intent_classification.py` — tests for `_classify_intent`, `_looks_like_greeting`, `_looks_like_gibberish`, `_looks_like_out_of_scope`, `_looks_like_injection_attempt`.
- `tests/unit/test_hallucination_guards.py` — tests that verify:
  - `no_retrieval` mode refuses (when config flag is off).
  - Empty retrieval → `FALLBACK_MESSAGE`.
  - Out-of-scope query → `FALLBACK_MESSAGE`.
  - Unmatched brand → refusal message.

---

#### 3.9 Log a "hallucination risk" warning
When the LLM path is taken *and* the top retrieved doc's score is below a configurable threshold, log a warning so operators can audit cases where the LLM might have fabricated:

```python
if best_score < config.HALLUCINATION_RISK_LOG_THRESHOLD:
    log.warning(
        "Hallucination risk: query=%r best_score=%.3f < %.3f — LLM may fabricate",
        query, best_score, config.HALLUCINATION_RISK_LOG_THRESHOLD,
    )
```

---

## 4. Summary table

| # | Solution | Tier | Effort | Blocks what? |
|---|----------|------|--------|--------------|
| 3.1 | Gate `no_retrieval` in production | 1 | ~10 min | LLM answering from own knowledge |
| 3.2 | Stricter refusal floor + empty-retrieval guard | 1 | ~15 min | Weak-context LLM answers |
| 3.3 | LLM relevance pre-check | 1 | ~1h | Wrong-topic retrieved docs |
| 3.4 | Content-match disambiguation for same-merchant offers | 2 | ~1h | Wrong offer from same merchant |
| 3.5 | LLM out-of-scope classifier | 2 | ~1h | Generic OOS queries slipping through |
| 3.6 | Fact-check replacement instead of supplement | 2 | ~30 min | Contradictory facts in one answer |
| 3.7 | Confidence score in response | 3 | ~30 min | Frontend can show uncertainty |
| 3.8 | Project-specific test suite | 3 | ~4h | Regression coverage for all guards |
| 3.9 | Hallucination-risk logging | 3 | ~15 min | Operational visibility |

---

## 5. The "I don't know" reply text (consistent across languages)

```python
I_DONT_KNOW = {
    "en": "I don't have that information in my current data. Please contact Waffarha support for help with this.",
    "ar": "للأسف مفيش عندي معلومات عن ده حاليًا. يرجى التواصل مع خدمة عملاء وفرها للمساعدة في الموضوع ده.",
}
```

This is already defined as `FALLBACK_MESSAGE` in `rag_engine.py` (lines 47–50) — use it consistently as the single source of truth for the "I don't know" reply, and reference it everywhere instead of duplicating the string.
