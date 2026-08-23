# Waffarha Assistant -- Evaluation Report

**Run:** 20260821_170403  
**Embedding model:** intfloat/multilingual-e5-base  
**Backend:** faiss  
**LLM model:** qwen2.5:3b-instruct  
**Base URL (HTTP suites):** n/a (--skip-http)  
**Mode:** RAG-only (retrieval, no generation, no HTTP)  

## Overall result: ❌ FAIL

| Suite | Status |
|---|---|
| 1. Infrastructure | ✅ PASS |
| 2. Retrieval-only | ❌ FAIL |
| 3. RAG correctness (full pipeline) | ⏭️ skipped |
| 4a. Server smoke | ⏭️ skipped (--skip-http/--rag-only) |
| 4b. Concurrency | ⏭️ skipped (--skip-http/--rag-only) |
| 5. Session memory | ⏭️ skipped (--skip-http/--rag-only) |

## 1. Infrastructure checks

| Check | Result | Detail |
|---|---|---|
| index files present | ✅ PASS | C:\Users\devza\Work\Waffarha\waffarha-chatbot\data\index\intfloat__multilingual-e5-base\faiss\docs.pkl |
| engine load (embedding model + index, Ollama NOT required -- rag-only) | ✅ PASS | 10.87s, 9120 docs, 2954 known merchants |

## 2. Retrieval-only correctness & latency (embedding + search, NO generation)

_Isolates the retriever from the LLM: calls `engine.retrieve()` directly, so a low score here means the embedding/search/ranking layer itself is at fault, independent of anything the model does with what it's given._

- **Cases run:** 145 (**106** had ground truth to score)
- **Passed:** 86/106
- **Errors:** 0
- **Hit@1:** 0.611  |  **Hit@k:** 0.722  |  **MRR:** 0.662
- **Embedding model:** `intfloat/multilingual-e5-base`
- **Embedding determinism (cosine, same query encoded twice):** 1.0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Retrieval (embed + search + score) | 145 | 0.552s | 0.698s | 0.653s | 1.17s | 1.31s |
| Embedding only (encode call alone) | 15 | 0.081s | 0.108s | 0.104s | 0.116s | 0.191s |

**By category**

| Category | Total | Checked | Passed |
|---|---|---|---|
| adversarial_input | 8 | 0 | 0 |
| faq_direct_answer | 26 | 26 | 22 |
| faq_not_in_kb | 2 | 0 | 0 |
| followup_anaphora | 6 | 6 | 5 |
| followup_clarification_needed | 2 | 0 | 0 |
| followup_ordinal | 4 | 4 | 4 |
| multi_item_comparison | 4 | 0 | 0 |
| offer_attribute_lookup | 8 | 8 | 7 |
| offer_category_filter | 10 | 10 | 10 |
| offer_direct_answer | 13 | 13 | 7 |
| offer_direct_answer_exact_zero_discount | 2 | 2 | 2 |
| offer_edge_case_expiry | 2 | 2 | 1 |
| offer_edge_case_sold_out | 2 | 2 | 2 |
| offer_fuzzy_match | 6 | 6 | 5 |
| offer_hallucination_check | 8 | 0 | 0 |
| offer_ranking | 7 | 7 | 1 |
| out_of_scope | 7 | 0 | 0 |
| price_range | 7 | 7 | 7 |
| prompt_injection | 8 | 0 | 0 |
| same_merchant_multi_offer_disambiguation | 9 | 9 | 9 |
| stock_query | 4 | 4 | 4 |

**By language**

| Language | Total | Checked | Passed |
|---|---|---|---|
| ar | 41 | 34 | 25 |
| en | 102 | 70 | 61 |
| mixed | 2 | 2 | 0 |

**Failed / errored retrieval cases**

| id | category | lang | question | expected | got (top-1) | rank | issue |
|---|---|---|---|---|---|---|---|
| offer_direct_answer_asianwok | offer_direct_answer | en | Tell me about the Asian Wok offer. | offer:9209 | offer:1887 (score=1.1626) | None | not in retrieved set |
| faq_arabic_privacy | faq_direct_answer | ar | ايه المعلومات اللي بتجمعوها عني؟ | faq:faq_13_privacy | offer:263 (score=1.0082) | None | not in retrieved set |
| offer_cheapest | offer_ranking | en | What's the cheapest offer available? | offer:6737 | offer:7805 (score=1.0642) | None | not in retrieved set |
| mixed_language_query_kfc | offer_direct_answer | mixed | عايز اعرف الـ discount بتاع KFC كام؟ | offer:8119 | offer:1853 (score=1.0015) | None | not in retrieved set |
| offer_direct_answer_kfc_ar | offer_direct_answer | ar | كام سعر عرض كنتاكي؟ | offer:8119 | offer:3469 (score=1.2747) | None | not in retrieved set |
| offer_direct_answer_asianwok_ar | offer_direct_answer | ar | احكيلي عن عرض اسيان ووك | offer:9209 | offer:2052 (score=1.1728) | None | not in retrieved set |
| mixed_language_query_asianwok | offer_direct_answer | mixed | What's the discount بتاع Asian Wok كام؟ | offer:9209 | offer:254 (score=1.0784) | None | not in retrieved set |
| arabizi_query_kfc | offer_direct_answer | en | 3ayez a3raf offer el KFC be kam | offer:8119 | offer:1830 (score=1.2082) | None | not in retrieved set |
| faq_arabic_purchase | faq_direct_answer | ar | ازاي اشتري من تطبيق وفرها؟ | faq:faq_2 | faq:faq_12_about (score=1.1987) | None | not in retrieved set |
| faq_arabic_use_coupon | faq_direct_answer | ar | ازاي استخدم الكوبون اللي اشتريته؟ | faq:faq_4 | faq:faq_8 (score=1.001) | None | not in retrieved set |
| faq_arabic_bill_payment | faq_direct_answer | ar | ممكن ادفع فاتورة الكهرباء من خلال التطبيق؟ | faq:faq_5 | faq:faq_11 (score=1.0932) | None | not in retrieved set |
| offer_typo_merchant_asianwok | offer_fuzzy_match | en | What's the deal at Asain Wok? | offer:9209 | offer:254 (score=0.9646) | None | not in retrieved set |
| offer_cheapest_verified | offer_ranking | en | What's the single cheapest offer in the whole catalog? | offer:7659 | offer:6132 (score=1.0893) | None | not in retrieved set |
| offer_highest_discount_verified | offer_ranking | en | Which single offer has the absolute highest discount percentage? | offer:9192 | offer:7090 (score=1.0196) | None | not in retrieved set |
| offer_most_expensive | offer_ranking | en | What's the most expensive offer you have? | offer:8780 | offer:2489 (score=1.1515) | None | not in retrieved set |
| offer_cheapest_ar | offer_ranking | ar | ايه ارخص عرض عندكم؟ | offer:7659 | offer:2889 (score=1.1126) | None | not in retrieved set |
| offer_highest_discount_ar | offer_ranking | ar | ايه العرض اللي عليه اعلى نسبة خصم؟ | offer:9192 | offer:6021 (score=1.1008) | None | not in retrieved set |
| offer_delivery_query_teta | offer_attribute_lookup | en | Can Teta's kahk boxes be delivered? | offer:8990 | offer:8987 (score=1.0187) | None | not in retrieved set |
| followup_anaphora_delivery_negative | followup_anaphora | en | Does it offer home delivery? | offer:9209 | offer:2337 (score=1.2963) | None | not in retrieved set |
| offer_expiry_not_expired_ar | offer_edge_case_expiry | ar | لسه ينفع اشتري عرض كنتاكي ولا خلص؟ | offer:8119 | offer:237 (score=1.2045) | None | not in retrieved set |

## 3. RAG correctness & latency, full pipeline (retrieval + generation, in-process)

_Skipped: --retrieval-only: full generation suite skipped_

## 4a. Server smoke test

_Skipped (--skip-http)._

## 4b. Concurrency / load test

_Skipped (--skip-http)._

## 5. Session memory round-trip

_Skipped (--skip-http)._

---
_Report generated by run_full_eval.py at 20260821_170403._