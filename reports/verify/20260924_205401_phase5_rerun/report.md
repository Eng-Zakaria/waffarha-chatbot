# Waffarha Assistant -- Evaluation Report

**Run:** 20260924_205401  
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
| engine load (embedding model + index, Ollama NOT required -- rag-only/no-retrieval) | [PASS] | 84.3s, 9106 docs, 2923 known merchants |

## 2. Retrieval-only correctness & latency (embedding + search, NO generation)

_Isolates the retriever from the LLM: calls `engine.retrieve()` directly, so a low score here means the embedding/search/ranking layer itself is at fault, independent of anything the model does with what it's given._

- **Cases run:** 225 (**172** had ground truth to score)
- **Passed:** 151/172
- **Errors:** 0
- **Hit@1:** 0.444  |  **Hit@k:** 0.796  |  **MRR:** 0.569
- **Embedding model:** `BAAI/bge-m3`
- **Embedding determinism (cosine, same query encoded twice):** 1.0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Retrieval (embed + search + score) | 225 | 0.974s | 1.766s | 1.215s | 3.985s | 15.94s |
| Embedding only (encode call alone) | 15 | 0.3s | 0.524s | 0.404s | 0.473s | 2.516s |

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
| offer_category_filter | 39 | 34 | 33 |
| offer_direct_answer | 56 | 52 | 49 |
| offer_direct_answer_exact_zero_discount | 1 | 1 | 1 |
| offer_edge_case_expiry | 1 | 1 | 1 |
| offer_fuzzy_match | 8 | 8 | 7 |
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
| ar | 203 | 159 | 140 |
| en | 11 | 4 | 3 |
| mixed | 11 | 9 | 8 |

**Failed / errored retrieval cases**

| id | category | lang | question | expected | got (top-1) | rank | issue |
|---|---|---|---|---|---|---|---|
| offer_most_expensive_ar | offer_ranking | ar | أغلى عرض فين؟ كم سعره؟ | offer:8780 | offer:7128 (score=0.78) | None | not in retrieved set |
| offer_highest_discount_ar | offer_ranking | ar | أيه العرض اللي عليه أكبر خصم؟ 🎁 | offer:9192 | offer:8180 (score=0.9862) | None | not in retrieved set |
| memory_followup_no_delivery_ar | followup_memory | ar | بيوصلو لي في البيت؟ | offer:9209 | offer:8021 (score=1.0133) | None | not in retrieved set |
| memory_followup_ordinal_first_ar | followup_memory | ar | أول عرض في القائمة ده إيه؟ | offer:8119 | offer:6737 (score=1.1171) | None | not in retrieved set |
| typo_heavy_arabic | offer_fuzzy_match | ar | كام عرض كتركى؟ | offer:8119 | offer:7418 (score=0.93) | None | not in retrieved set |
| offer_delivery_negative_ar | offer_attribute_lookup | ar | العرض ده التوصيل عليه متاح؟ | offer:9209 | offer:8235 (score=0.7953) | None | not in retrieved set |
| payment_vodafone_cash_ar | faq_direct_answer | ar | فودافون كاش متاح للدفع؟ | faq:payment_109_info | faq:faq_6 (score=0.9593) | None | not in retrieved set |
| followup_more_like_this_ar | followup_memory | ar | في حاجات زي كده تانية؟ | offer:None | None:None (score=None) | None | not in retrieved set |
| order_tracking_ar | faq_direct_answer | ar | أنا اشتريت كوبون أمس، لسه مفيش في حسابي، إيه السبب؟ | faq:faq_8 | faq:payment_95_info (score=0.7875) | None | not in retrieved set |
| offer_hundred_degrees_ar | offer_direct_answer | ar | حندرد ديجريز عندها سحور بكام؟ 🌙 | offer:None | None:None (score=None) | None | not in retrieved set |
| offer_disabled_bellini_ar | offer_hallucination_check | ar | عندكم عرض بيليني؟ 👔 | null:None | offer:8160 (score=0.8831) | None | not in retrieved set |
| offer_disabled_taj_meer_ar | offer_hallucination_check | ar | في عرض تاج مير؟ 🏨 | null:None | offer:7613 (score=1.18) | None | not in retrieved set |
| franco_wafflicious | offer_direct_answer | mixed | waffle wafflicious be kam ya basha? | offer:135 | offer:7645 (score=1.432) | None | not in retrieved set |
| memory_followup_recommend_similar | followup_memory | ar | في حاجات مشابهة كده؟ | offer:None | None:None (score=None) | None | not in retrieved set |
| greeting_bono_ar | greeting | ar | بونا صباح الخير 😊 | null:None | faq:purchasing_status_8 (score=0.5042) | None | not in retrieved set |
| greeting_shokran_en | greeting | en | Thanks a lot! 🙏 | null:None | faq:purchasing_status_1 (score=0.4941) | None | not in retrieved set |
| greeting_msa_ahlan | greeting | ar | أهلاً وسهلاً | null:None | faq:purchasing_status_1 (score=0.493) | None | not in retrieved set |
| faq_refund_wallet_ar | faq_direct_answer | ar | أنا دفعت بفودافون كاش، أقدر أرجع الفلوس؟ 💸 | faq:payment_48_refund | faq:faq_refund_policy (score=0.8503) | None | not in retrieved set |
| long_story_hilton_date | offer_direct_answer | ar | عندي موعد غداً في الزمالك مع حبيبتي، عايز أحجز إقامة نهار في فندق نظيف وفخم، في حاجة كده؟ | offer:9131 | offer:8349 (score=0.9279) | None | not in retrieved set |
| offer_ramadan_sohour_ar | offer_category_filter | ar | عروض سحور في رمضان 🌙 | offer:None | None:None (score=None) | None | not in retrieved set |
| greeting_hello_arabic_style | greeting | ar | مرحبا! شلونك؟ 😄 | null:None | faq:purchasing_status_7 (score=0.4846) | None | not in retrieved set |

## 3. RAG correctness & latency, full pipeline (retrieval + generation, in-process)

- **Cases run:** 225 (**225** had explicit checks)
- **Passed:** 64/225
- **Errors:** 0
- **Scaffolding leaks:** 0

**Latency**

| Metric | N | Min | Mean | Median | p95 | Max |
|---|---|---|---|---|---|---|
| Per-query latency (retrieval + generation) | 225 | 0.0s | 3.337s | 1.513s | 11.969s | 24.134s |

**By category**

| Category | Total | Checked | Passed |
|---|---|---|---|
| adversarial_input | 11 | 11 | 6 |
| faq_direct_answer | 25 | 25 | 2 |
| faq_not_in_kb | 2 | 2 | 0 |
| followup_clarification_needed | 2 | 2 | 1 |
| followup_memory | 12 | 12 | 2 |
| greeting | 8 | 8 | 1 |
| multi_item_comparison | 6 | 6 | 1 |
| offer_attribute_lookup | 6 | 6 | 2 |
| offer_category_filter | 39 | 39 | 16 |
| offer_direct_answer | 56 | 56 | 11 |
| offer_direct_answer_exact_zero_discount | 1 | 1 | 0 |
| offer_edge_case_expiry | 1 | 1 | 1 |
| offer_fuzzy_match | 8 | 8 | 3 |
| offer_hallucination_check | 7 | 7 | 3 |
| offer_ranking | 8 | 8 | 2 |
| out_of_scope | 11 | 11 | 5 |
| price_range | 8 | 8 | 3 |
| prompt_injection | 6 | 6 | 3 |
| same_merchant_multi_offer_disambiguation | 4 | 4 | 0 |
| stock_query | 4 | 4 | 2 |

**By language**

| Language | Total | Checked | Passed |
|---|---|---|---|
| ar | 203 | 203 | 57 |
| en | 11 | 11 | 2 |
| mixed | 11 | 11 | 5 |

**Failed / errored cases (question + answer)**

| id | category | lang | question | answer | issue |
|---|---|---|---|---|---|
| greeting_salam | greeting | ar | السلام عليكم 👋 | أهلاً بيك! أقدر أساعدك في العروض، طلباتك، الكاش باك، أو الاسترجاع — تحب تعرف إيه؟ | failed checks: expected_keywords |
| greeting_shukran | greeting | ar | شكراً ليكم 🙏 | أهلاً بيك! أقدر أساعدك في العروض، طلباتك، الكاش باك، أو الاسترجاع — تحب تعرف إيه؟ | failed checks: expected_keywords |
| greeting_ahlan_bik | greeting | ar | أهلا بيك يا باشا 😊 | أهلاً بيك! أقدر أساعدك في العروض، طلباتك، الكاش باك، أو الاسترجاع — تحب تعرف إيه؟ | failed checks: expected_keywords |
| offer_kfc_arabic_direct | offer_direct_answer | ar | عندكم عرض كنتاكي؟ بكام؟ 🍗 | مرحبا، تم إعداد عروضنا الخاصة لك في هذه الصفحة. يرجى رؤية العروض أدناه للاطلاع على أفضل العروض الحالية من فروع دجاج كنتاكي. 🏷️ حصريا: وجبة ساندوتش زنجر وقطع ستربس كومبو بخصم 39% من فروع كنتاكي فرايد ت… | failed checks: expected_keywords |
| offer_mcdonalds_price_ar | offer_direct_answer | ar | كام سعر ماكدونالدز؟ عايز أعرف 👀 | مرحبا، تم ترتيب العروض الخاصة لك في المجموعة المرفوعة أدناه. يرجى مراجعتها للاطلاع على أفضل الخيارات المتاحة. 🏷️ حصريا! الساندوتشات اللذيذة مع بطاطس وصنداي بخصم حتى 57% من فروع ماكدونالدز! المتجر: ماك… | failed checks: expected_keywords |
| offer_koshary_ar | offer_direct_answer | ar | عندي شغف للكشري، عندكم عرض كشري؟ | مرحبا، نحن هنا لعرض لك عروضنا المثيرة للإعجاب التي سنقدمها لك تحت هذه العروض. يرجى متابعة الرابط أدناه لمعرفة المزيد عن العروض المتاحة. 🏷️ كشري وطواجن مع حلو ومشروب غازي بخصم حتى 36% من فروع كشري الخد… | failed checks: expected_id, expected_keywords |
| offer_nonexistent_pizza_hut | offer_hallucination_check | ar | عندكم بيتزا هت؟🍕 | مرحبا، تم ترتيب العروض الخاصة لك في المجموعة القادمة من الأسفل، يرجى ملاحظة العروض المقدمة لمنتجات بيتزا هت. 🏷️ وجبات بيتزا وسط وكبيرة مع مقبل وسلطة بخصم حتى 35% من فروع بيتزا هت! المتجر: بيتزا هت الف… | failed checks: expected_source, forbidden_keywords |
| offer_hilton_dayuse_ar | offer_direct_answer | ar | بكام إقامة نهار في هيلتون الزمالك؟ | مرحبا، سأرسل لك العروض المميزة للإقامة والأنشطة التي طلبتها في الأسفل، يمكنك مشاهدتها مباشرةً من الرابط المقدم. 🏷️ إقامة ليلية استثنائية بكل تفاصيلها، مع إطلالة نيلية ساحرة في هيلتون القاهرة زمالك ريز… | failed checks: expected_id |
| offer_most_expensive_ar | offer_ranking | ar | أغلى عرض فين؟ كم سعره؟ | ده أغلى عرض متاح دلوقتي: 🏷️ جولات جوية لا تُنسى فوق أشهر معالم القاهرة الكبرى بخصم 21% في Horus Jets، مطار ألماظة المتجر: Horus Jets الفئة: الترفيه والعائلة السعر: من 5550 حتى 13999 جنيه (2 خيارات) 🔥… | failed checks: expected_id, expected_keywords |
| offer_highest_discount_ar | offer_ranking | ar | أيه العرض اللي عليه أكبر خصم؟ 🎁 | ده العرض اللي عليه أعلى خصم دلوقتي: 🏷️ دللي نفسك مع باقتي الحمام المغربي والعناية الكاملة بخصم حتى 88% في أماني التونسي بيوتى آند سبا المتجر: أماني التونسي بيوتي سنتر & سبا الفئة: الجمال والعناية السع… | failed checks: expected_keywords |
| price_range_under_100_ar | price_range | ar | عايز عروض تحت 100 جنيه 🙏 | دي العروض اللي لقتها لك: 🏷️ استمتع ب 70 دقيقة كياك في النيل بخصم 33% في جو كياك! فقط 100 جنيه بدلا من 150 جنيه المتجر: جو كياك الفئة: الترفيه والعائلة السعر: 100 جنيه بدل ما كان 150 جنيه 🔥 خصم 33% سار… | failed checks: expected_keywords |
| price_range_over_500_ar | price_range | ar | عايز عروض فوق 500 جنيه | مرحبا، تم ترتيب العروض الخاصة لك في هذه القائمة، يمكنك الاطلاع عليها مباشرةً للحصول على أفضل العروض المقدمة من شركائنا. 🏷️ باقة تحاليل طبية كاملة للعناية بصحتك بخصم 33% في الحياة لاب، بنها! فقط 500 جن… | failed checks: expected_keywords |
| faq_register_ar | faq_direct_answer | ar | أنا جديد، ازاي أشتغل حساب جديد؟ | كيفية انشاء حساب على وفرها؟ خطوات إنشاء حساب جديد على ابلكيشن وفرها: 1) اضغط على 'إنشاء حساب'. 2) سجل رقم الموبايل. 3) هيوصلك كود تفعيل على نفس رقم الموبايل في رسالة نصية. 4) اكتب الكود في الابلكيشن و… | failed checks: expected_source, expected_id, expected_keywords |
| faq_purchase_ar | faq_direct_answer | ar | إزاي أشتري كوبون من التطبيق؟ | كيفية الشراء من أبلكيشن وفرها؟ 1) اختار العرض اللي يناسبك. 2) حدد عدد الكوبونات واضغط على 'أضف إلى العربة'. 3) اضغط على 'الذهاب للعربة' علشان تراجع تفاصيل الطلب. 4) اضغط على 'تابع عملية الشراء' واختار… | failed checks: expected_source, expected_id, expected_keywords |
| faq_payment_methods_ar | faq_direct_answer | ar | إيه طرق الدفع المتاحة؟ 💳 | طرق الدفع المتاحة على الابلكيشن؟ وفرها بتقدملك اكتر من طريقة دفع: البطاقات البنكية (فيزا/ماستركارد)، التقسيط البنكي، Apple Pay، المحافظ الإلكترونية (فودافون كاش، اتصالات كاش، أورنج كاش وغيرها)، الدفع… | failed checks: expected_source, expected_id, expected_keywords |
| faq_use_coupon_ar | faq_direct_answer | ar | أنا اشتريت كوبون، إزاي أستخدمه؟ | طريقة استخدام الكوبون بعد الشراء؟ بعد إتمام الدفع، تقدر تلاقي الكوبونات من خانة 'طلباتي' < 'تفاصيل الطلب' < 'اظهر كود العرض'. لو حابب تستخدمه في الفرع، زور الفرع من خانة 'الفروع المتاحة' ووضح رقم الكو… | failed checks: expected_source, expected_id, expected_keywords |
| faq_bill_payment_ar | faq_direct_answer | ar | أقدر أدفع فواتيري من على الوفرها؟ | طريقة دفع الفواتير من الابلكيشن؟ تقدر تسدد فواتيرك من خانة 'دفع الفواتير' على الابلكيشن، هتلاقي خدمات زي شحن الرصيد وفواتير الموبايل والخط الأرضي والفواتير المنزلية والأقساط الشهرية. بعد اختيار الخدمة… | failed checks: expected_source, expected_id, expected_keywords |
| faq_refund_ar | faq_direct_answer | ar | أنا عايز أرجع فلوسي، إزاي؟ 🤔 | لرجاع الفلوس في منصتك، يمكنك اتباع الخطوات التالية: 1. دخولك إلى حسابك عبر الابلكيشن أو الموقع الرسمي. 2. زيارة صفحة 'الإشعارات' أو 'المستندات' لرؤية أي طلبات لل refunds التي قمت بها. 3. إذا لم تجد طل… | failed checks: expected_id |
| faq_cashback_ar | faq_direct_answer | ar | الكاش باك بيتصرف إزاي؟ | ما هي سياسة الكاش باك؟ رصيد الكاش باك متاح لمدة 30 يوم فقط من وقت إضافته، ويتم إضافته بعد تفعيل الكوبونات للعروض أو دفع الفاتورة للفواتير. تقدر تختار بين استخدام رصيد الكاش باك أو تطبيق كود خصم (الاتن… | failed checks: expected_source, expected_id, expected_keywords |
| faq_about_waffarha_ar | faq_direct_answer | ar | وفرها تطبيقات إيه؟ 📱 | توفر خدمة وفرها تطبيقات متعددة للاستخدام عبر الهواتف الذكية. إليك بعض المعلومات حول التطبيقات الرئيسية: 1. وفرها للجوال: هذا التطبيق هو الأكثر شيوعاً ويتيح لك الوصول إلى جميع الخدمات مثل تقديم الكوبون… | failed checks: expected_id, expected_keywords |
| faq_not_in_kb_refund_policy | faq_not_in_kb | ar | إمتى بيوصلني الكوبون بعد الشراء؟ | بعد إكمال عملية الدفع، ستحصل على الكوبون فورًا في حسابك على وفرها. ستظهر الكوبونات مباشرة تحت خانة "طلباتي". يمكنك استلام الكوبون وإستعماله للحصول على العرض المقدم من التجار. | failed checks: expected_source |
| faq_not_in_kb_branches | faq_not_in_kb | ar | فين فروع كنتاكي المتوفرة؟ | مرحبا، تم تخصيص لك عرضين من بين العديد من العروض المتوفرة حالياً في المتاجر المختلفة. يرجى ملاحظة أن هذه العروض سارية حتى 1 نوفمبر 2026. 🏷️ حصريا: وجبة ساندوتش زنجر وقطع ستربس كومبو بخصم 39% من فروع ك… | failed checks: expected_source |
| memory_followup_price_before | followup_memory | ar | كام كان سعره قبل الخصم؟ | دي العروض اللي لقتها لك: 🏷️ Exclusive! Zinger sandwich & strips combo meal with 39% off from KFC branches! المتجر: KFC الفئة: الطعام والمشروبات السعر: 189 جنيه بدل ما كان 310 جنيه 🔥 خصم 39% ساري حتى 2… | failed checks: expected_keywords |
| memory_followup_still_valid | followup_memory | ar | العرض ده لسه شغال؟ ✅ | مرحبا، أهلاً بكم! سأعدكم باعرضنا المميزة للطعام والمشروبات من متجر McDonald's الخاصة بكم هنا. يرجى مراجعة العروض أدناه. 🏷️ Exclusive! the delicious sandwiches with fries & ice cream at 57% off @McDona… | failed checks: expected_keywords |
| memory_followup_delivery_ar | followup_memory | ar | العرض ده بيوصل للبيت ولا لا؟ 🚗 | للأسف مفيش عندنا عروض من بيوصل للبيت حاليًا. لو محتاج مساعدة، اتواصل مع دعم Waffarha على support@waffarha.com. | failed checks: expected_keywords |
| memory_followup_no_delivery_ar | followup_memory | ar | بيوصلو لي في البيت؟ | للأسف مفيش عندي معلومات عن ده حاليًا. يرجى التواصل مع خدمة عملاء وفرها للمساعدة في الموضوع ده. | failed checks: expected_source, expected_id |
| memory_followup_ordinal_first_ar | followup_memory | ar | أول عرض في القائمة ده إيه؟ | مرحبا، تم ترتيب عروضنا الخاصة لك في هذه القائمة أسفلك. يرجى التحقق من العروض المثيرة التي نقدمها لك. 🏷️ كشري وطواجن مع حلو ومشروب غازي بخصم حتى 36% من فروع كشري الخديوي المتجر: كشري الخديوي الفئة: الط… | failed checks: expected_id, expected_keywords |
| memory_ambiguous_no_context | followup_clarification_needed | ar | كام سعره؟ | مرحبا، سأرسل لك العروض المميزة للمنتجات التي تود معرفة المزيد عنها في الأسفل. يرجى التحقق من العروض المثيرة للاهتمام! 🏷️ استمتع بمشروبك المفضل مع الآيس كريم بخصم حتى 33% في Buko، المقطم! المتجر: Buko… | failed checks: forbidden_keywords |
| category_gym_ar | offer_category_filter | ar | عروض جيم وفيتنس 💪 | للأسف مفيش عندنا عروض من جيم وفيتنس 💪 حاليًا. لو محتاج مساعدة، اتواصل مع دعم Waffarha على support@waffarha.com. | failed checks: expected_source |
| comparison_kfc_mcdonalds_ar | multi_item_comparison | ar | قارنيلي بين كنتاكي وماكدونالدز 🍟 | إليك العروض لكل متجر: **دجاج كنتاكي:** 🏷️ حصريا: وجبة ساندوتش زنجر وقطع ستربس كومبو بخصم 39% من فروع كنتاكي فرايد تشيكن! المتجر: دجاج كنتاكي الفئة: الطعام والمشروبات السعر: 189 جنيه بدل ما كان 310 جني… | failed checks: expected_keywords |
| … | | | | | +131 more, see full.json |

## 4a. Server smoke test

_Skipped (--skip-http)._

## 4b. Concurrency / load test

_Skipped (--skip-http)._

## 5. Session memory round-trip

_Skipped (--skip-http)._

---
_Report generated by run_full_eval.py at 20260924_205401._