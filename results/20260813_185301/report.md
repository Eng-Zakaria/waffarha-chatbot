# Waffarha Assistant -- Evaluation Report

**Run:** 20260813_185301  
**Embedding model:** intfloat/multilingual-e5-base  
**Backend:** faiss  
**LLM model:** qwen2.5:3b-instruct  
**Base URL (HTTP suites):** http://localhost:8000  

## Overall result: ❌ FAIL

| Suite | Status |
|---|---|
| 1. Infrastructure | ❌ FAIL |
| 2. RAG correctness | ❌ FAIL |
| 3a. Server smoke | ❌ FAIL |
| 3b. Concurrency | ❌ FAIL |
| 4. Session memory | ✅ PASS |

## 1. Infrastructure checks

| Check | Result | Detail |
|---|---|---|
| index files present | ✅ PASS | C:\Users\devza\Work\Waffarha\waffarha-eval\data\index\intfloat__multilingual-e5-base\faiss\docs.pkl |
| engine load (embedding model + index + Ollama reachable) | ✅ PASS | 14.81s, 1690 docs, 869 known merchants |
| redis reachable | ❌ FAIL | ConnectionError: Error 10061 connecting to localhost:6379. No connection could be made because the target machine actively refused it. (REDIS_URL=redis://localhost:6379/0) |

## 2. RAG correctness & latency (in-process)

- **Cases run:** 69 (**69** had explicit checks)
- **Passed:** 56/69
- **Errors:** 0
- **Scaffolding leaks:** 0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Per-query latency | 69 | 0.084s | 2.241s | 0.166s | 8.13s | 16.69s |

**By category**

| Category | Total | Checked | Passed |
|---|---|---|---|
| adversarial_input | 4 | 4 | 2 |
| faq_direct_answer | 16 | 16 | 13 |
| faq_not_in_kb | 2 | 2 | 1 |
| followup_anaphora | 3 | 3 | 3 |
| followup_clarification_needed | 1 | 1 | 1 |
| followup_ordinal | 2 | 2 | 1 |
| multi_item_comparison | 2 | 2 | 2 |
| offer_attribute_lookup | 3 | 3 | 3 |
| offer_category_filter | 5 | 5 | 5 |
| offer_direct_answer | 5 | 5 | 4 |
| offer_direct_answer_exact_zero_discount | 1 | 1 | 0 |
| offer_edge_case_expiry | 1 | 1 | 1 |
| offer_edge_case_sold_out | 1 | 1 | 1 |
| offer_fuzzy_match | 2 | 2 | 2 |
| offer_hallucination_check | 2 | 2 | 1 |
| offer_ranking | 2 | 2 | 1 |
| out_of_scope | 4 | 4 | 3 |
| price_range | 3 | 3 | 3 |
| prompt_injection | 5 | 5 | 4 |
| same_merchant_multi_offer_disambiguation | 3 | 3 | 3 |
| stock_query | 2 | 2 | 2 |

**Failed / errored cases**

| id | category | issue |
|---|---|---|
| offer_direct_answer_zero_discount | offer_direct_answer_exact_zero_discount | failed checks: expected_keywords |
| faq_direct_answer_use_coupon | faq_direct_answer | failed checks: expected_source, expected_id |
| faq_direct_answer_about | faq_direct_answer | failed checks: expected_source, expected_id |
| faq_arabic_refund | faq_direct_answer | failed checks: expected_source, expected_id |
| faq_out_of_kb_password_reset | faq_not_in_kb | failed checks: forbidden_keywords |
| followup_third_of_three | followup_ordinal | failed checks: expected_source, expected_id |
| out_of_scope_capital_france | out_of_scope | failed checks: forbidden_keywords |
| injection_fake_context_tag | prompt_injection | failed checks: forbidden_keywords |
| empty_query | adversarial_input | failed checks: forbidden_keywords |
| gibberish_query | adversarial_input | failed checks: forbidden_keywords |
| offer_cheapest | offer_ranking | failed checks: expected_id |
| offer_nonexistent_merchant | offer_hallucination_check | failed checks: forbidden_keywords |
| mixed_language_query_kfc | offer_direct_answer | failed checks: expected_id |

## 3a. Server smoke test

| Check | Result | Latency | Detail |
|---|---|---|---|
| GET /api/health | ✅ PASS | 0.071s | {"status":"ok","engine_loaded":false,"memory_connected":false,"generation_in_flight":0,"generation_capacity":4} |
| POST /api/chat rejects empty query (400) | ✅ PASS | 0.013s | status=400 body={"detail":"query is required"} |
| POST /api/chat basic end-to-end answer | ❌ FAIL | 90.012s | ReadTimeout: HTTPConnectionPool(host='localhost', port=8000): Read timed out. (read timeout=90) |

## 3b. Concurrency / load test

- **Requests:** 24 at concurrency 8
- **Wall time:** 125.358s (0.191 req/s)
- **Succeeded (200):** 17
- **Backpressure (503, expected under overload):** 4
- **Unexpected errors:** 3
- **Configured capacity:** MAX_CONCURRENT_GENERATIONS=4, GENERATION_QUEUE_TIMEOUT=30.0s

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Successful request latency | 17 | 1.577s | 2.426s | 2.375s | 3.419s | 3.468s |

## 4. Session memory round-trip

- **Target offer:** AniMania Zoo (id=9156)
- **Session id:** `eval-memory-0a39c416`
- **Turn 1 ("Tell me about the AniMania Zoo offer"):** ✅ PASS (9.254s)
- **Turn 2 follow-up ("How much was it before the discount?"):** resolved back to AniMania Zoo: ✅ PASS (0.386s)

<details><summary>Full transcript</summary>

**Turn 1:** Tell me about the AniMania Zoo offer

> Here's what I found:
**30% off @AniMania Zoo, Obour** — 210 EGP (was 300 EGP) — 30% off — valid until 2028-07-01

**Turn 2:** How much was it before the discount?

> Here's what I found:
**30% off @AniMania Zoo, Obour** — 210 EGP (was 300 EGP) — 30% off — valid until 2028-07-01

</details>

---
_Report generated by run_full_eval.py at 20260813_185301._