# Waffarha Assistant -- Evaluation Report

**Run:** 20260914_091409  
**Embedding model:** BAAI/bge-m3  
**Backend:** qdrant  
**LLM model:** qwen2.5:3b-instruct  
**Base URL (HTTP suites):** n/a (--skip-http)  
**Mode:** RAG-only (retrieval, no generation, no HTTP)  

## Overall result: [FAIL]

| Suite | Status |
|---|---|
| 1. Infrastructure | [PASS] |
| 2. Retrieval-only | [FAIL] |
| 3. RAG correctness (full pipeline) | ⏭️ skipped |
| 4a. Server smoke | ⏭️ skipped (--skip-http/--rag-only) |
| 4b. Concurrency | ⏭️ skipped (--skip-http/--rag-only) |
| 5. Session memory | ⏭️ skipped (--skip-http/--rag-only) |

## 1. Infrastructure checks

| Check | Result | Detail |
|---|---|---|
| index files present | [PASS] | C:\Users\devza\Work\Waffarha\waffarha-chatbot\data\index\BAAI__bge-m3\qdrant\docs.pkl |
| engine load (embedding model + index, Ollama NOT required -- rag-only/no-retrieval) | [PASS] | 50.75s, 9142 docs, 2954 known merchants |

## 2. Retrieval-only correctness & latency (embedding + search, NO generation)

_Isolates the retriever from the LLM: calls `engine.retrieve()` directly, so a low score here means the embedding/search/ranking layer itself is at fault, independent of anything the model does with what it's given._

- **Cases run:** 225 (**172** had ground truth to score)
- **Passed:** 146/172
- **Errors:** 0
- **Hit@1:** 0.333  |  **Hit@k:** 0.667  |  **MRR:** 0.468
- **Embedding model:** `BAAI/bge-m3`
- **Embedding determinism (cosine, same query encoded twice):** 1.0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Retrieval (embed + search + score) | 225 | 0.863s | 1.23s | 1.048s | 1.783s | 14.077s |
| Embedding only (encode call alone) | 15 | 0.23s | 0.407s | 0.301s | 0.37s | 1.798s |

**By category**

| Category | Total | Checked | Passed |
|---|---|---|---|
| adversarial_input | 11 | 1 | 1 |
| faq_direct_answer | 25 | 24 | 17 |
| faq_not_in_kb | 2 | 0 | 0 |
| followup_clarification_needed | 2 | 0 | 0 |
| followup_memory | 12 | 12 | 8 |
| greeting | 8 | 4 | 0 |
| multi_item_comparison | 6 | 5 | 5 |
| offer_attribute_lookup | 6 | 6 | 5 |
| offer_category_filter | 39 | 34 | 34 |
| offer_direct_answer | 56 | 52 | 48 |
| offer_direct_answer_exact_zero_discount | 1 | 1 | 1 |
| offer_edge_case_expiry | 1 | 1 | 1 |
| offer_fuzzy_match | 8 | 8 | 6 |
| offer_hallucination_check | 7 | 2 | 0 |
| offer_ranking | 8 | 7 | 5 |
| out_of_scope | 11 | 0 | 0 |
| price_range | 8 | 7 | 7 |
| prompt_injection | 6 | 0 | 0 |
| same_merchant_multi_offer_disambiguation | 4 | 4 | 4 |
| stock_query | 4 | 4 | 4 |

**By language**

| Language | Total | Checked | Passed |
|---|---|---|---|
| ar | 203 | 159 | 137 |
| en | 11 | 4 | 3 |
| mixed | 11 | 9 | 6 |

**Failed / errored retrieval cases**

| id | category | lang | question | expected | got (top-1) | rank | issue |
|---|---|---|---|---|---|---|---|
| offer_most_expensive_ar | offer_ranking | ar | أغلى عرض فين؟ كم سعره؟ | offer:8780 | offer:660 (score=1.0282) | None | not in retrieved set |
| offer_highest_discount_ar | offer_ranking | ar | أيه العرض اللي عليه أكبر خصم؟ 🎁 | offer:9192 | offer:5339 (score=1.1418) | None | not in retrieved set |
| faq_purchase_ar | faq_direct_answer | ar | إزاي أشتري كوبون من التطبيق؟ | faq:faq_2 | offer:3456 (score=0.8742) | None | not in retrieved set |
| faq_bill_payment_ar | faq_direct_answer | ar | أقدر أدفع فواتيري من على الوفرها؟ | faq:faq_5 | faq:payment_4_info (score=0.9457) | None | not in retrieved set |
| faq_refund_ar | faq_direct_answer | ar | أنا عايز أرجع فلوسي، إزاي؟ 🤔 | faq:faq_8 | offer:1753 (score=0.7827) | None | not in retrieved set |
| memory_followup_no_delivery_ar | followup_memory | ar | بيوصلو لي في البيت؟ | offer:9209 | offer:2614 (score=1.0176) | None | not in retrieved set |
| memory_followup_ordinal_first_ar | followup_memory | ar | أول عرض في القائمة ده إيه؟ | offer:8119 | offer:6737 (score=1.1163) | None | not in retrieved set |
| fuzzy_kfc_typo_ar | offer_fuzzy_match | ar | عروض ك ف سي موجودة؟ | offer:8119 | offer:281 (score=1.0149) | None | not in retrieved set |
| typo_heavy_arabic | offer_fuzzy_match | ar | كام عرض كتركى؟ | offer:8119 | offer:2492 (score=0.9074) | None | not in retrieved set |
| offer_delivery_negative_ar | offer_attribute_lookup | ar | العرض ده التوصيل عليه متاح؟ | offer:9209 | offer:297 (score=0.9595) | None | not in retrieved set |
| followup_more_like_this_ar | followup_memory | ar | في حاجات زي كده تانية؟ | offer:None | None:None (score=None) | None | not in retrieved set |
| order_tracking_ar | faq_direct_answer | ar | أنا اشتريت كوبون أمس، لسه مفيش في حسابي، إيه السبب؟ | faq:faq_8 | offer:237 (score=0.6824) | None | not in retrieved set |
| gift_voucher_ar | faq_direct_answer | ar | أنا عايز أشتري قسيمة هدية لصاحبي 🎁 | faq:faq_10 | offer:600 (score=0.9747) | None | not in retrieved set |
| offer_disabled_bellini_ar | offer_hallucination_check | ar | عندكم عرض بيليني؟ 👔 | null:None | offer:2177 (score=1.7362) | None | not in retrieved set |
| offer_disabled_taj_meer_ar | offer_hallucination_check | ar | في عرض تاج مير؟ 🏨 | null:None | offer:4902 (score=1.1199) | None | not in retrieved set |
| franco_hilton | offer_direct_answer | mixed | hilton zamalek 3afya kam? | offer:9131 | offer:9121 (score=0.6653) | None | not in retrieved set |
| memory_followup_recommend_similar | followup_memory | ar | في حاجات مشابهة كده؟ | offer:None | None:None (score=None) | None | not in retrieved set |
| greeting_bono_ar | greeting | ar | بونا صباح الخير 😊 | null:None | offer:2233 (score=0.6639) | None | not in retrieved set |
| greeting_shokran_en | greeting | en | Thanks a lot! 🙏 | null:None | offer:7236 (score=0.4967) | None | not in retrieved set |
| greeting_msa_ahlan | greeting | ar | أهلاً وسهلاً | null:None | offer:739 (score=0.4939) | None | not in retrieved set |
| faq_refund_wallet_ar | faq_direct_answer | ar | أنا دفعت بفودافون كاش، أقدر أرجع الفلوس؟ 💸 | faq:payment_48_refund | faq:payment_109_refund (score=0.9074) | None | not in retrieved set |
| faq_cancel_order_ar | faq_direct_answer | ar | أنا اشتريت كوبون وعايز ألغي الطلبية، إزاي؟ | faq:faq_8 | faq:purchasing_status_5 (score=0.7698) | None | not in retrieved set |
| arabizi_hilton | offer_direct_answer | mixed | kam offer el hilton 2lmzalk? | offer:9131 | offer:5384 (score=0.9368) | None | not in retrieved set |
| arabizi_kfc | offer_direct_answer | mixed | offer el kfc kam? | offer:8119 | offer:6812 (score=1.5704) | None | not in retrieved set |
| long_story_hilton_date | offer_direct_answer | ar | عندي موعد غداً في الزمالك مع حبيبتي، عايز أحجز إقامة نهار في فندق نظيف وفخم، في حاجة كده؟ | offer:9131 | offer:8349 (score=0.9279) | None | not in retrieved set |
| greeting_hello_arabic_style | greeting | ar | مرحبا! شلونك؟ 😄 | null:None | offer:2526 (score=0.5043) | None | not in retrieved set |

## 3. RAG correctness & latency, full pipeline (retrieval + generation, in-process)

_Skipped: --retrieval-only: full generation suite skipped_

## 4a. Server smoke test

_Skipped (--skip-http)._

## 4b. Concurrency / load test

_Skipped (--skip-http)._

## 5. Session memory round-trip

_Skipped (--skip-http)._

---
_Report generated by run_full_eval.py at 20260914_091409._