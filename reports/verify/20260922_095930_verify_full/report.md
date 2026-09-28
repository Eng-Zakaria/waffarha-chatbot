# Waffarha Assistant -- Evaluation Report

**Run:** 20260922_095930  
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
| 3. RAG correctness (full pipeline) | [FAIL] |
| 4a. Server smoke | ⏭️ skipped (--skip-http/--rag-only) |
| 4b. Concurrency | ⏭️ skipped (--skip-http/--rag-only) |
| 5. Session memory | ⏭️ skipped (--skip-http/--rag-only) |

## 1. Infrastructure checks

| Check | Result | Detail |
|---|---|---|
| index files present | [PASS] | C:\Users\devza\Work\Waffarha\waffarha-chatbot\data\index\BAAI__bge-m3\qdrant\docs.pkl |
| engine load (embedding model + index, Ollama NOT required -- rag-only/no-retrieval) | [PASS] | 42.15s, 9106 docs, 2923 known merchants |

## 2. Retrieval-only correctness & latency (embedding + search, NO generation)

_Isolates the retriever from the LLM: calls `engine.retrieve()` directly, so a low score here means the embedding/search/ranking layer itself is at fault, independent of anything the model does with what it's given._

- **Cases run:** 225 (**172** had ground truth to score)
- **Passed:** 150/172
- **Errors:** 0
- **Hit@1:** 0.315  |  **Hit@k:** 0.741  |  **MRR:** 0.483
- **Embedding model:** `BAAI/bge-m3`
- **Embedding determinism (cosine, same query encoded twice):** 1.0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Retrieval (embed + search + score) | 225 | 0.838s | 1.377s | 0.993s | 3.577s | 10.374s |
| Embedding only (encode call alone) | 15 | 0.234s | 0.284s | 0.271s | 0.299s | 0.485s |

**By category**

| Category | Total | Checked | Passed |
|---|---|---|---|
| adversarial_input | 11 | 1 | 1 |
| faq_direct_answer | 25 | 24 | 21 |
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
| ar | 203 | 159 | 141 |
| en | 11 | 4 | 3 |
| mixed | 11 | 9 | 6 |

**Failed / errored retrieval cases**

| id | category | lang | question | expected | got (top-1) | rank | issue |
|---|---|---|---|---|---|---|---|
| offer_most_expensive_ar | offer_ranking | ar | أغلى عرض فين؟ كم سعره؟ | offer:8780 | offer:660 (score=1.0281) | None | not in retrieved set |
| offer_highest_discount_ar | offer_ranking | ar | أيه العرض اللي عليه أكبر خصم؟ 🎁 | offer:9192 | offer:8180 (score=1.0695) | None | not in retrieved set |
| memory_followup_no_delivery_ar | followup_memory | ar | بيوصلو لي في البيت؟ | offer:9209 | offer:2614 (score=1.1973) | None | not in retrieved set |
| memory_followup_ordinal_first_ar | followup_memory | ar | أول عرض في القائمة ده إيه؟ | offer:8119 | offer:6737 (score=1.1171) | None | not in retrieved set |
| fuzzy_kfc_typo_ar | offer_fuzzy_match | ar | عروض ك ف سي موجودة؟ | offer:8119 | offer:281 (score=1.0149) | None | not in retrieved set |
| typo_heavy_arabic | offer_fuzzy_match | ar | كام عرض كتركى؟ | offer:8119 | offer:2492 (score=0.9063) | None | not in retrieved set |
| offer_delivery_negative_ar | offer_attribute_lookup | ar | العرض ده التوصيل عليه متاح؟ | offer:9209 | offer:297 (score=0.9595) | None | not in retrieved set |
| followup_more_like_this_ar | followup_memory | ar | في حاجات زي كده تانية؟ | offer:None | None:None (score=None) | None | not in retrieved set |
| order_tracking_ar | faq_direct_answer | ar | أنا اشتريت كوبون أمس، لسه مفيش في حسابي، إيه السبب؟ | faq:faq_8 | faq:payment_131_info (score=0.8665) | None | not in retrieved set |
| gift_voucher_ar | faq_direct_answer | ar | أنا عايز أشتري قسيمة هدية لصاحبي 🎁 | faq:faq_10 | offer:279 (score=1.0521) | None | not in retrieved set |
| offer_disabled_bellini_ar | offer_hallucination_check | ar | عندكم عرض بيليني؟ 👔 | null:None | offer:2177 (score=1.7369) | None | not in retrieved set |
| offer_disabled_taj_meer_ar | offer_hallucination_check | ar | في عرض تاج مير؟ 🏨 | null:None | offer:4902 (score=1.1185) | None | not in retrieved set |
| franco_hilton | offer_direct_answer | mixed | hilton zamalek 3afya kam? | offer:9131 | offer:9121 (score=0.8437) | None | not in retrieved set |
| memory_followup_recommend_similar | followup_memory | ar | في حاجات مشابهة كده؟ | offer:None | None:None (score=None) | None | not in retrieved set |
| greeting_bono_ar | greeting | ar | بونا صباح الخير 😊 | null:None | offer:2233 (score=0.6644) | None | not in retrieved set |
| greeting_shokran_en | greeting | en | Thanks a lot! 🙏 | null:None | offer:7236 (score=0.4957) | None | not in retrieved set |
| greeting_msa_ahlan | greeting | ar | أهلاً وسهلاً | null:None | offer:739 (score=0.4939) | None | not in retrieved set |
| faq_refund_wallet_ar | faq_direct_answer | ar | أنا دفعت بفودافون كاش، أقدر أرجع الفلوس؟ 💸 | faq:payment_48_refund | faq:faq_refund_policy (score=0.8503) | None | not in retrieved set |
| arabizi_hilton | offer_direct_answer | mixed | kam offer el hilton 2lmzalk? | offer:9131 | offer:5151 (score=0.9377) | None | not in retrieved set |
| arabizi_kfc | offer_direct_answer | mixed | offer el kfc kam? | offer:8119 | offer:6812 (score=1.5699) | None | not in retrieved set |
| long_story_hilton_date | offer_direct_answer | ar | عندي موعد غداً في الزمالك مع حبيبتي، عايز أحجز إقامة نهار في فندق نظيف وفخم، في حاجة كده؟ | offer:9131 | offer:8349 (score=0.9279) | None | not in retrieved set |
| greeting_hello_arabic_style | greeting | ar | مرحبا! شلونك؟ 😄 | null:None | offer:2526 (score=0.505) | None | not in retrieved set |

## 3. RAG correctness & latency, full pipeline (retrieval + generation, in-process)

- **Cases run:** 225 (**225** had explicit checks)
- **Passed:** 82/225
- **Errors:** 0
- **Scaffolding leaks:** 0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Per-query latency (retrieval + generation) | 225 | 0.0s | 3.024s | 2.556s | 9.928s | 21.787s |

**By category**

| Category | Total | Checked | Passed |
|---|---|---|---|
| adversarial_input | 11 | 11 | 6 |
| faq_direct_answer | 25 | 25 | 2 |
| faq_not_in_kb | 2 | 2 | 0 |
| followup_clarification_needed | 2 | 2 | 1 |
| followup_memory | 12 | 12 | 1 |
| greeting | 8 | 8 | 1 |
| multi_item_comparison | 6 | 6 | 1 |
| offer_attribute_lookup | 6 | 6 | 3 |
| offer_category_filter | 39 | 39 | 21 |
| offer_direct_answer | 56 | 56 | 20 |
| offer_direct_answer_exact_zero_discount | 1 | 1 | 1 |
| offer_edge_case_expiry | 1 | 1 | 1 |
| offer_fuzzy_match | 8 | 8 | 4 |
| offer_hallucination_check | 7 | 7 | 2 |
| offer_ranking | 8 | 8 | 2 |
| out_of_scope | 11 | 11 | 4 |
| price_range | 8 | 8 | 4 |
| prompt_injection | 6 | 6 | 3 |
| same_merchant_multi_offer_disambiguation | 4 | 4 | 2 |
| stock_query | 4 | 4 | 3 |

**By language**

| Language | Total | Checked | Passed |
|---|---|---|---|
| ar | 203 | 203 | 74 |
| en | 11 | 11 | 3 |
| mixed | 11 | 11 | 5 |

**Failed / errored cases (question + answer)**

| id | category | lang | question | answer | issue |
|---|---|---|---|---|---|
| greeting_salam | greeting | ar | السلام عليكم 👋 | أهلاً بيك! أقدر أساعدك في العروض، طلباتك، الكاش باك، أو الاسترجاع — تحب تعرف إيه؟ | failed checks: expected_keywords |
| greeting_shukran | greeting | ar | شكراً ليكم 🙏 | عافاك! لو محتاج أي حاجة تانية، أنا موجود | failed checks: expected_keywords |
| greeting_ahlan_bik | greeting | ar | أهلا بيك يا باشا 😊 | أهلاً بيك! أقدر أساعدك في العروض، طلباتك، الكاش باك، أو الاسترجاع — تحب تعرف إيه؟ | failed checks: expected_keywords |
| offer_kfc_arabic_direct | offer_direct_answer | ar | عندكم عرض كنتاكي؟ بكام؟ 🍗 | مرحبا، سأهدي لك عرضين من العروض المميزة التي سنعرضها لك أدناه. 🏷️ حصريا: وجبة ساندوتش زنجر وقطع ستربس كومبو بخصم 39% من فروع كنتاكي فرايد تشيكن! المتجر: دجاج كنتاكي الفئة: الطعام والمشروبات السعر: 189… | failed checks: expected_keywords |
| offer_mcdonalds_price_ar | offer_direct_answer | ar | كام سعر ماكدونالدز؟ عايز أعرف 👀 | مرحبا، لقد تم ترتيب عروضنا الخاصة لك في الأسفل. يرجى قراءة العروض واتبع الروابط المقدمة للحصول على التفاصيل الكاملة. 🏷️ حصريا! الساندوتشات اللذيذة مع بطاطس وصنداي بخصم حتى 57% من فروع ماكدونالدز! المت… | failed checks: expected_keywords |
| offer_koshary_ar | offer_direct_answer | ar | عندي شغف للكشري، عندكم عرض كشري؟ | مرحبا، نحن سعداء بتوفير هذه العروض المثيرة لكشري الخديوي والتحرير. يرجى تصفح العروض أدناه للحصول على أفضل عروضنا الحالية. 🏷️ كشري وطواجن مع حلو ومشروب غازي بخصم حتى 36% من فروع كشري الخديوي المتجر: كش… | failed checks: expected_id, expected_keywords |
| offer_nonexistent_starbucks | offer_hallucination_check | ar | سويتشي من ستياربكس ☕ | مرحبا، حضرتك تعرف عن العروض المميزة التي نقدمها لك؟ انظروا إلى ما وفرنا من خصومات على الطعام والمشروبات في المتاجر المختلفة. 🏷️ ادفع 20 جنيه بدلا من 44 جنيه واحصل على أي مشروب كبير من اختيارك + (مافن… | failed checks: expected_source, forbidden_keywords |
| offer_nonexistent_pizza_hut | offer_hallucination_check | ar | عندكم بيتزا هت؟🍕 | إليك العروض الحالية: 🏷️ وجبات بيتزا وسط وكبيرة مع مقبل وسلطة بخصم حتى 35% من فروع بيتزا هت! المتجر: بيتزا هت الفئة: الطعام والمشروبات السعر: 275 جنيه بدل ما كان 421 جنيه 🔥 خصم 35% ساري حتى 2026-11-01… | failed checks: expected_source, forbidden_keywords |
| offer_hilton_dayuse_ar | offer_direct_answer | ar | بكام إقامة نهار في هيلتون الزمالك؟ | مرحبا، نحن سعداء بعرض لك مجموعة من العروض المثالية التي سنقدمها لك أدناه بعد رسالتك هذه. تفضل بالاطلاع على الخيارات المتاحة واتخاذ قرارك! 🏷️ إقامة ليلية استثنائية بكل تفاصيلها، مع إطلالة نيلية ساحرة ف… | failed checks: expected_id |
| offer_most_expensive_ar | offer_ranking | ar | أغلى عرض فين؟ كم سعره؟ | ده أغلى عرض متاح دلوقتي: 🏷️ جولات جوية لا تُنسى فوق أشهر معالم القاهرة الكبرى بخصم 21% في Horus Jets، مطار ألماظة المتجر: Horus Jets الفئة: الترفيه والعائلة السعر: من 5550 حتى 13999 جنيه (2 خيارات) 🔥… | failed checks: expected_id, expected_keywords |
| offer_highest_discount_ar | offer_ranking | ar | أيه العرض اللي عليه أكبر خصم؟ 🎁 | ده العرض اللي عليه أعلى خصم دلوقتي: 🏷️ دللي نفسك مع باقتي الحمام المغربي والعناية الكاملة بخصم حتى 88% في أماني التونسي بيوتى آند سبا المتجر: أماني التونسي بيوتي سنتر & سبا الفئة: الجمال والعناية السع… | failed checks: expected_keywords |
| price_range_under_100_ar | price_range | ar | عايز عروض تحت 100 جنيه 🙏 | دي العروض اللي لقتها لك: 🏷️ حصريا: فقط 100 جنيه واحصل على مشتريات بقيمة 200 جنيه من اختيارك من (قميص رسومات - بنطلون جينز - سويتشيرت - بدلة - بالطو) من أكبر تشكيلة هتكملك شياكتك وهتخليك مميز من تاي ها… | failed checks: expected_keywords |
| price_range_over_500_ar | price_range | ar | عايز عروض فوق 500 جنيه | مرحبا، تم توجيهك إلى عروضنا الفريدة التي ستساعدك في إيجاد أفضل الخيارات للإقامة خلال الإجازة الصيفية مع العائلة والأصدقاء في بورتو السخنة. يرجى قراءة العروض المقدمة أدناه. 🏷️ ادفع 500 جنيه لقضاء ليلة… | failed checks: expected_keywords |
| faq_register_ar | faq_direct_answer | ar | أنا جديد، ازاي أشتغل حساب جديد؟ | كيفية انشاء حساب على وفرها؟ خطوات إنشاء حساب جديد على ابلكيشن وفرها: 1) اضغط على 'إنشاء حساب'. 2) سجل رقم الموبايل. 3) هيوصلك كود تفعيل على نفس رقم الموبايل في رسالة نصية. 4) اكتب الكود في الابلكيشن و… | failed checks: expected_source, expected_id, expected_keywords |
| faq_purchase_ar | faq_direct_answer | ar | إزاي أشتري كوبون من التطبيق؟ | كيفية الشراء من أبلكيشن وفرها؟ 1) اختار العرض اللي يناسبك. 2) حدد عدد الكوبونات واضغط على 'أضف إلى العربة'. 3) اضغط على 'الذهاب للعربة' علشان تراجع تفاصيل الطلب. 4) اضغط على 'تابع عملية الشراء' واختار… | failed checks: expected_source, expected_id, expected_keywords |
| faq_payment_methods_ar | faq_direct_answer | ar | إيه طرق الدفع المتاحة؟ 💳 | طرق الدفع المتاحة على الابلكيشن؟ وفرها بتقدملك اكتر من طريقة دفع: البطاقات البنكية (فيزا/ماستركارد)، التقسيط البنكي، Apple Pay، المحافظ الإلكترونية (فودافون كاش، اتصالات كاش، أورنج كاش وغيرها)، الدفع… | failed checks: expected_source, expected_id, expected_keywords |
| faq_use_coupon_ar | faq_direct_answer | ar | أنا اشتريت كوبون، إزاي أستخدمه؟ | طريقة استخدام الكوبون بعد الشراء؟ بعد إتمام الدفع، تقدر تلاقي الكوبونات من خانة 'طلباتي' < 'تفاصيل الطلب' < 'اظهر كود العرض'. لو حابب تستخدمه في الفرع، زور الفرع من خانة 'الفروع المتاحة' ووضح رقم الكو… | failed checks: expected_source, expected_id, expected_keywords |
| faq_bill_payment_ar | faq_direct_answer | ar | أقدر أدفع فواتيري من على الوفرها؟ | طريقة دفع الفواتير من الابلكيشن؟ تقدر تسدد فواتيرك من خانة 'دفع الفواتير' على الابلكيشن، هتلاقي خدمات زي شحن الرصيد وفواتير الموبايل والخط الأرضي والفواتير المنزلية والأقساط الشهرية. بعد اختيار الخدمة… | failed checks: expected_source, expected_id, expected_keywords |
| faq_refund_ar | faq_direct_answer | ar | أنا عايز أرجع فلوسي، إزاي؟ 🤔 | عذراً، يبدو أن هناك بعض الالتباس في سؤالك. من فضلك قدم المزيد من التفاصيل لكي أتمكن من مساعدتك بشكل أفضل. هل ترغب في استرجاع فلوسي بسبب شراء ما تم دفعه باستخدام FORSA؟ أو هل تريد استرجاع كupon؟ يرجى ت… | failed checks: expected_id, expected_keywords |
| faq_cashback_ar | faq_direct_answer | ar | الكاش باك بيتصرف إزاي؟ | ما هي سياسة الكاش باك؟ رصيد الكاش باك متاح لمدة 30 يوم فقط من وقت إضافته، ويتم إضافته بعد تفعيل الكوبونات للعروض أو دفع الفاتورة للفواتير. تقدر تختار بين استخدام رصيد الكاش باك أو تطبيق كود خصم (الاتن… | failed checks: expected_source, expected_id, expected_keywords |
| faq_about_waffarha_ar | faq_direct_answer | ar | وفرها تطبيقات إيه؟ 📱 | مرحبا، تم ترتيب العروض الخاصة لك في هذه القائمة، يرجى ملاحظة أن هذه العروض سارية المفعول حتى التاريخ المحدد. 🏷️ حصريا: احصل على كارت مشتريات بقيمة 100 جنيه هديتك من وفرها.كوم في عيد ميلادها السادس صال… | failed checks: expected_source, expected_id, expected_keywords |
| faq_not_in_kb_refund_policy | faq_not_in_kb | ar | إمتى بيوصلني الكوبون بعد الشراء؟ | بعد إكمال عملية الدفع بنجاح، الكوبونات ستظهر فورًا في حسابك على وفرها تحت خانة "الكوبونات". يمكنك اعادة ترتيب أو طباعة الكوبونات واستخدامها عند الشراء الت دي. | failed checks: expected_source |
| faq_not_in_kb_branches | faq_not_in_kb | ar | فين فروع كنتاكي المتوفرة؟ | مرحبًا، سأيتم بالoffers المميزين للحجز في المتاجر المختلفة. يرجى قراءة العروض المرفقة أدناه. 🏷️ حصريا: وجبة ساندوتش زنجر وقطع ستربس كومبو بخصم 39% من فروع كنتاكي فرايد تشيكن! المتجر: دجاج كنتاكي الفئة… | failed checks: expected_source |
| memory_followup_price_before | followup_memory | ar | كام كان سعره قبل الخصم؟ | دي العروض اللي لقتها لك: 🏷️ 11th Anniversary! Fried chicken combo meal with 58% off from KFC branches! EGP 125 instead of EGP 299 المتجر: KFC الفئة: الطعام والمشروبات السعر: 125 جنيه بدل ما كان 299 جن… | failed checks: expected_id, expected_keywords |
| memory_followup_still_valid | followup_memory | ar | العرض ده لسه شغال؟ ✅ | مرحبا، أهلاً بكم! سأعدكم باعرضنا المميزة للطعام والمشروبات التي سنقوم بمدلكم بها بعد قليل. 🏷️ Exclusive! the delicious sandwiches with fries & ice cream at 57% off @McDonald's branches! المتجر: McDona… | failed checks: expected_keywords |
| memory_followup_delivery_ar | followup_memory | ar | العرض ده بيوصل للبيت ولا لا؟ 🚗 | للأسف مفيش عندنا عروض من بيوصل للبيت حاليًا. لو محتاج مساعدة، اتواصل مع دعم Waffarha على support@waffarha.com. | failed checks: expected_keywords |
| memory_followup_no_delivery_ar | followup_memory | ar | بيوصلو لي في البيت؟ | مرحبًا، أتمنى أن تنال هذه العروض اهتمامك. إليك مجموعة من العروض المثيرة التي نقدمها لك. 🏷️ جعان في البيت ومحتار تطلب إيه؟ ادفع 80 جنيه بدلا من 165 جنيه واستمتع بأحلى وجبة غداء أوعشاء مكونة من (بيتزا أ… | failed checks: expected_id |
| memory_followup_ordinal_first_ar | followup_memory | ar | أول عرض في القائمة ده إيه؟ | مرحبا، سأعدك تصفية للعروض المميزة التي نقدمها لك على وفّارهha، انظر إلى العروض التالية التي سنعرضها لك. 🏷️ كشري وطواجن مع حلو ومشروب غازي بخصم حتى 36% من فروع كشري الخديوي المتجر: كشري الخديوي الفئة:… | failed checks: expected_id, expected_keywords |
| memory_followup_compare_ar | followup_memory | ar | قارن بين العروضين دي 📊 | هنا مقارنة بين العروض: 🏷️ 11th Anniversary! Fried chicken combo meal with 58% off from KFC branches! EGP 125 instead of EGP 299 المتجر: KFC الفئة: الطعام والمشروبات السعر: 125 جنيه بدل ما كان 299 جنيه… | failed checks: expected_keywords |
| memory_ambiguous_no_context | followup_clarification_needed | ar | كام سعره؟ | مرحبا، نرحب بكم هنا لعرض تشكيلة من العروض المميزة التي سنقدمها لكم أدناه. هل يمكنني مساعدتك في العثور على ما يناسب احتياجاتك؟ 🏷️ ادفعى 295 جنيه بدلا من 600 جنيه واحصلى على طقم الفوط 16 قطعة باللون الم… | failed checks: forbidden_keywords |
| … | | | | | +113 more, see full.json |

## 4a. Server smoke test

_Skipped (--skip-http)._

## 4b. Concurrency / load test

_Skipped (--skip-http)._

## 5. Session memory round-trip

_Skipped (--skip-http)._

---
_Report generated by run_full_eval.py at 20260922_095930._