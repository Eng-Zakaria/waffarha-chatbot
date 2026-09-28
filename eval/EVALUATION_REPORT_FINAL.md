# Waffarha Chatbot Manual Evaluation Report
**Date**: 2026-09-06
**Evaluator**: Claude Code (Agnes)
**Session ID**: manual_eval_session
**Memory Backend**: local (no Redis)
**Catalog Queries**: Disabled (static FAISS index only)
**Personal Queries**: Disabled

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total Conversations** | 12 |
| **Total Questions** | 100 |
| **Questions Passed** | 30 |
| **Overall Score** | **30.0%** |
| **Passing Conversations (>=50%)** | 2 |
| **Failing Conversations (<50%)** | 10 |

---

## Results by Conversation

### 1. Restaurant Offers: 4/10 (40%)
| Question | Status | Notes |
|----------|--------|-------|
| السلام عليكم، شوية عروض المطاعم؟ | FAILED | Empty answer - greeting handling issue |
| عايز أكل برجر، فين أحسن عرض؟ | PASSED | Found burger offer |
| كام سعر البرجر ده؟ | PASSED | Price correctly shown (115 جنيه) |
| طيب في عرض تشيكن أيضا؟ | FAILED | No chicken offer found |
| قارنيلي بين البرجر والتشيكن | FAILED | Retrieval returned burger only |
| الشيكين عرضه كام بالضبط؟ | PASSED | Price shown (130 جنيه) |
| عايز أعرف عرض كشري | FAILED | Retrieved burger instead of koshary |
| كام سعر الكشري؟ | PASSED | Price shown (130 جنيه) |
| الكشري ده بيوصل للبيت ولا لا؟ | FAILED | No delivery info in response |
| شكراً ليكم على المعلومات | FAILED | Greeting detection issue |

**Issues Found**:
- Greeting detection failing for Arabic greetings
- Cross-category retrieval errors (chicken query returning burger results)
- No delivery information in responses
- Follow-up context not maintained properly

---

### 2. Hotel Offers: 4/10 (40%)
| Question | Status | Notes |
|----------|--------|-------|
| مرحبا، عندكم عروض فنادق؟ | FAILED | No hotel offers returned |
| عايز إقامة نهار في هيلتون الزمالك | FAILED | Retrieved night stay instead of day use |
| كام سعر الإقامة النهارية؟ | PASSED | Price shown (7240 جنيه - but wrong offer) |
| في إقامة ليلية معاهم؟ | PASSED | Night stay found |
| قارنيلي بين النهار والليلة | PASSED | Comparison provided |
| الإقامة النهارية دى فيها إفطار؟ | FAILED | No breakfast info |
| فين الفندق ده بالضبط؟ | FAILED | Location not mentioned |
| عندكم فنادق أخرى في الإسكندرية؟ | FAILED | Alexandria hotels not found |
| كام أغلى فندق عندكم؟ | PASSED | Steigenberger found (8500 جنيه) |
| شكراً، هتفكر في الأمر | FAILED | Greeting detection issue |

**Issues Found**:
- Day/night stay disambiguation failing
- Location-based filtering not working
- Breakfast amenity not mentioned
- Greeting detection failing

---

### 3. KFC Offers: 2/9 (22%)
| Question | Status | Notes |
|----------|--------|-------|
| عندي شغف كنتاكي، عندكم إيه؟ | FAILED | KFC not matched (keywords: kfc, kentucky) |
| عايز أعرف التفاصيل | PASSED | Price 189 shown |
| كام كان سعره قبل الخصم؟ | FAILED | Original price 310 not matched with "485" |
| العرض ده لسه شغال؟ | PASSED | Validity confirmed |
| إمتى بيخلص العرض؟ | FAILED | Expiry date not extracted properly |
| في عروض تانية من كنتاكي؟ | FAILED | No other offers found |
| لو عايز أכול عيلتي، أنصحني بأيه؟ | FAILED | No family recommendation |
| عندكم خدمة توصيل؟ | FAILED | Delivery info missing |
| مشوار تمام، شكراً | FAILED | Greeting detection issue |

**Issues Found**:
- Merchant name matching weak for Arabic queries
- Original price not being extracted correctly
- Follow-up context lost after first turn
- Delivery information not surfaced

---

### 4. FAQ - App Usage: 1/9 (11%)
| Question | Status | Notes |
|----------|--------|-------|
| أنا جديد على التطبيق، ازاي أبدأ؟ | FAILED | FAQ not retrieved |
| إزاي أشتري كوبون من التطبيق؟ | FAILED | FAQ not retrieved |
| طرق الدفع المتاحة إيه؟ | FAILED | Payment methods not found |
| أنا اشتريت كوبون، إزاي أستخدمه؟ | FAILED | Usage instructions missing |
| حالة الطلب 'مستعمل' معناه إيه؟ | PASSED | Status explanation found |
| أنا عايز أرجع فلوسي، إزاي؟ | FAILED | Empty answer |
| الكاش باك بيتصرف إزاي؟ | FAILED | Cashback info missing |
| إيه فائدة تطبيق وفرها بالضبط؟ | FAILED | About page not found |
| شكراً على التوضيح | FAILED | Greeting detection issue |

**Issues Found**:
- **Critical**: FAQ retrieval severely underperforming
- Greeting detection broken for thank-you messages
- Personal data leakage (PII mentioned in unrelated responses)

---

### 5. Superlative Queries: 2/8 (25%)
| Question | Status | Notes |
|----------|--------|-------|
| أرخص عرض عندكم قد إيه؟ | PASSED | Found 35 جنيه |
| أغلى عرض فين وكام؟ | FAILED | Expected 9100, got 8500 |
| أكبر خصم فين؟ | FAILED | Expected 85%, got 75% |
| في عروض تحت 100 جنيه؟ | FAILED | PII leaked instead of offers |
| في عروض من 300 لحد 800؟ | FAILED | PII leaked instead of offers |
| أنصحوني بأحسن عرض حاليا | FAILED | PII leaked instead of offers |
| عندي ميزانية 200 جنيه، إéh المناسب؟ | PASSED | Found offer at 160 جنيه |
| شكرا على التوصيات | FAILED | Greeting detection issue |

**Issues Found**:
- Superlative queries returning incorrect values
- PII leakage in responses (serious issue)
- Price range filtering broken for some ranges

---

### 6. Entertainment & Activities: 1/8 (12%)
| Question | Status | Notes |
|----------|--------|-------|
| عايز أفكر إيه في نهاية الأسبوع؟ | FAILED | Wrong category returned |
| في عروض cinema؟ | PASSED | Cinema found |
| كام سعر تذكرة السينما؟ | FAILED | Expected 21, got 290 |
| عروض فون قديم كام؟ | FAILED | Expected 143, got wrong offer |
| أنيمانيا زوو بكام؟ | FAILED | Expected 32, got 210 |
| القرية الفرعونية فيها عروض قد إيه؟ | FAILED | No offers found |
| عروض للأطفال فين؟ | FAILED | Wrong category |
| شكرا، هروح مع العيلة | FAILED | Greeting detection issue |

**Issues Found**:
- Price accuracy poor across entertainment categories
- Category-based filtering not working
- Entertainment offers not properly indexed

---

### 7. Food & Beverages: 1/8 (12%)
| Question | Status | Notes |
|----------|--------|-------|
| عايز أشرب قهوة، عندكم إيه؟ | PASSED | Coffee found |
| كام سعر قهوة كوفي شوب؟ | FAILED | Expected 88, got 85 |
| في شاورما؟ كام سعرها؟ | FAILED | Returned coffee instead |
| ويفليشوس عندهم عرض بكام؟ | FAILED | Returned coffee instead |
| زادنا عندها حلويات كام؟ | FAILED | Expected 70, got 49 |
| حندرد ديجريز عندها سحور بكام؟ | FAILED | Expected 85, got 49 |
| أتلانتس كورنرز فيه كام عرض؟ | FAILED | Expected 130, got 105 |
| شكرا، أكل الكترونات | FAILED | Greeting detection issue |

**Issues Found**:
- Single merchant dominating responses (Sira coffee)
- Price accuracy inconsistent
- Merchant-specific queries not working well

---

### 8. Wellness & Beauty: 3/8 (38%)
| Question | Status | Notes |
|----------|--------|-------|
| عايز أعمل سبا ومساج | PASSED | Spa found |
| كام سعر الـ spa؟ | FAILED | Keyword matching issue |
| عروض تجميل وشعر فين؟ | FAILED | Keyword matching issue |
| كام teeth whitening عند Dental Boss؟ | PASSED | Dental found |
| أرخص عرض في Dental Boss كام؟ | FAILED | Wrong merchant returned |
| بامبو نيل سبا فيه عروض كام؟ | FAILED | Wrong merchant returned |
| في عروض gym؟ | PASSED | Gym found |
| شكرا على العروض | FAILED | Greeting detection issue |

**Issues Found**:
- Superlative queries failing (cheapest not working)
- Merchant-specific follow-ups losing context

---

### 9. Franco-Arabic Queries: 5/7 (71%) ⭐ BEST
| Question | Status | Notes |
|----------|--------|-------|
| 3ayez a3raf kam offer el KFC? | PASSED | KFC found (189 EGP) |
| discount McDonald's be kam ya som3a? | PASSED | McDonald's found (79 EGP) |
| waffle wafflicious be kam ya basha? | PASSED | Wafflicious found (95 EGP) |
| hilton zamalek 3afya kam? | PASSED | Hilton found |
| fun kingdom 3afyat kam? | PASSED | Fun Kingdom found |
| arabizi offer kam? | FAILED | Generic query failed |
| shukran ya mu3allem | FAILED | Greeting detection issue |

**Strengths**:
- Best performing category
- Franco-Arabic normalization working well
- Multi-language queries handled correctly

---

### 10. Out-of-Scope Tests: 3/9 (33%)
| Question | Status | Notes |
|----------|--------|-------|
| عاملين إيه الجو في القاهرة النهاردة؟ | FAILED | Should deflect, didn't |
| عندي وجع头部، آخذ إيه دواء؟ | PASSED | Correctly deflected |
| اعمليلي نكتة | FAILED | Should deflect, didn't |
| سويتشي من ستياربكس | FAILED | Hallucinated offer |
| عندكم بيتزا هت؟ | FAILED | hallucinated offer |
| الطقس عامل ايه | PASSED | Correctly deflected |
| مين رئيس مصر دلوقتي؟ | FAILED | Should deflect, didn't |
| في عرض بيليني؟ | PASSED | Correctly deflected (disabled merchant) |
| شكرا على الشفافية | FAILED | Greeting detection issue |

**Issues Found**:
- Out-of-scope detection inconsistent
- Hallucination guardrails failing for brand names
- Some queries incorrectly treated as valid

---

### 11. Price Range Filtering: 4/7 (57%)
| Question | Status | Notes |
|----------|--------|-------|
| عايز عروض تحت 50 جنيه | PASSED | Under 50 found |
| في حاجات اغلى من كده؟ | FAILED | No follow-up range |
| عايز عروض من 100 لحد 300 | FAILED | Range not applied |
| في حاجة فوق 500؟ | PASSED | Over 500 handled |
| أنا طالب وبقتي ضيقة، في عروض تحت 30 جنيه؟ | PASSED | Under 30 found |
| أحسن عرض تحت 100 جنيه هو إéh؟ | PASSED | Best under 100 found |
| شكرا على المساعدة | FAILED | Greeting detection issue |

**Strengths**:
- Price range filtering working for basic cases
- Budget-conscious queries handled reasonably

---

### 12. Delivery & Availability: 0/7 (0%) ⭐ WORST
| Question | Status | Notes |
|----------|--------|-------|
| كشري التحرير بيوصل للدار؟ | FAILED | Delivery info missing |
| الحواوشي الرفاعي فيه توصيل؟ | FAILED | Delivery info missing |
| ويفليشوس بيوصل للبيت؟ | FAILED | Delivery info missing |
| العرض ده بيوصل؟ | FAILED | Generic delivery query failed |
| فاضل كام كوبون من كنتاكي؟ | FAILED | Stock info missing |
| أنيمانيا زوو فاضي ولا فيه كوبونات؟ | FAILED | Stock info missing |
| شكرا، هطلب دلوقتي | FAILED | Greeting detection issue |

**Critical Issues**:
- Delivery attribute lookup completely broken
- Stock availability not tracked or returned
- No delivery information in any response

---

## Key Findings

### Strengths
1. **Franco-Arabic Queries (71%)**: Best performance, normalization working well
2. **Price Range Filtering (57%)**: Basic range queries work
3. **Wellness & Beauty (38%)**: Category-specific queries reasonably accurate
4. **Hotel Comparison (partial)**: When offers are found, comparison works
5. **Direct Answer Format**: When offers are retrieved, formatting is clean

### Critical Issues
1. **Greeting Detection Broken**: All thank-you/greeting responses failing across all conversations
2. **FAQ Retrieval Poor (11%)**: Most critical user onboarding questions failing
3. **Delivery Info Missing (0%)**: Completely non-functional for delivery queries
4. **PII Leakage**: Personal information appearing in unrelated responses
5. **Context Loss**: Follow-up questions losing conversation context
6. **Price Accuracy**: Multiple cases of wrong prices returned
7. **Merchant Matching**: Arabic merchant names not reliably matched

### Hallucination Risks
- Starbucks query returned irrelevant sushi offers
- Pizza Hut query returned actual offers (brand IS in catalog - false positive)
- PII mentioned in superlative query responses

### Memory/Context Issues
- Follow-up questions like "كام كان سعره قبل الخصم؟" not resolving to previous offer
- Ordinal references ("التاني") not working
- Comparison requests losing anchor offers

---

## Recommendations

### Immediate (P0)
1. **Fix greeting detection** - Add more Arabic greeting patterns
2. **Fix FAQ retrieval** - Debug intent classification for FAQ queries
3. **Add delivery attribute** - Include delivery info in offer metadata
4. **Fix PII leakage** - Investigate why personal data appears in responses

### Short-term (P1)
5. **Improve merchant matching** - Add more Arabic aliases
6. **Fix price accuracy** - Debug retrieval for specific merchants
7. **Enhance context tracking** - Improve session memory for follow-ups
8. **Add stock tracking** - Implement remaining_coupons_count lookup

### Medium-term (P2)
9. **Improve out-of-scope detection** - Better guardrails for invalid queries
10. **Add category filtering** - Better category-based retrieval
11. **Enhance superlative handling** - Fix "cheapest/most expensive" queries
12. **Add location-based filtering** - Support city/area queries

---

## Test Methodology

- **12 conversations** with 100 total questions
- All queries in Arabic (except Franco-Arabic test)
- Each conversation tests different capabilities:
  - Restaurant offers
  - Hotel bookings
  - Specific merchant queries
  - FAQ usage
  - Superlative comparisons
  - Entertainment categories
  - Food & beverages
  - Wellness/beauty
  - Franco-Arabic mixed queries
  - Out-of-scope handling
  - Price range filtering
  - Delivery & stock

- **Evaluation criteria**: Keyword presence in response, correct deflection for out-of-scope, no hallucination
- **Score**: 30/100 = **30.0%**

---

## Appendix: Raw Results JSON

Full evaluation results saved to:
- `eval/manual_eval_20260906_113852.json`
- `eval/EVALUATION_REPORT_MANUAL_20260906.md`
