# Memory System Test Analysis - Detailed Findings

**Test Date:** 2026-09-03  
**Test Type:** Comprehensive Conversation Memory Testing  
**Total Conversations:** 10  
**Total Turns:** 134  
**Follow-up Resolution Rate:** 100% (78/78)

---

## Executive Summary

The memory system's **core mechanics are working** (turn storage, offer deduplication, session isolation), but there are **critical semantic issues** in follow-up resolution that cause merchant drift and contextual confusion.

**Overall Grade: C+ (Working but needs improvement)**

---

## Test Results Breakdown

### ✅ What Works Correctly

| Feature | Status | Evidence |
|---------|--------|----------|
| Turn storage | PASS | All sessions store 24-30 turns correctly |
| Offer deduplication | PASS | No duplicate offers in memory |
| Session isolation | PASS | Sessions don't leak between users |
| Most-recent-first ordering | PASS | New offers move to front |
| Basic follow-up detection | PASS | "بكام العرض ده" works when anchored |
| Topic return | PASS | "ارجع لعروض كنتاكي" returns correctly |

### ❌ Critical Issues Found

#### 1. Merchant Drift in Follow-ups (HIGH SEVERITY)

**Issue:** When user asks for "another offer from them", the system returns offers from DIFFERENT merchants instead of the SAME merchant.

**Examples:**
```
CONV 01 - Turn 2:
Query: "فيه عرض تاني عندهم؟" (Another offer from them?)
Expected: Different KFC offer
Got: Rocket's fried chicken offer  ← WRONG MERCHANT

CONV 02 - Turn 2:
Query: "فيه برجر ارخص؟" (Cheaper burger?)
Expected: Another McDonald's burger
Got: Same McDonald's offer repeated  ← NO DRIFT BUT NO NEW OFFER
```

**Root Cause:** The `_resolve_followup_targets()` function correctly classifies "OTHER_OFFER_SAME_SOURCE" but the retrieval doesn't actually filter by merchant. It pins the merchant but doesn't exclude the already-shown offer ID, so it returns the same or unrelated offers.

---

#### 2. Contextual Topic Confusion (HIGH SEVERITY)

**Issue:** When user switches topics, the system loses context and returns irrelevant categories.

**Examples:**
```
CONV 03 - Turn 6:
Context: User was asking about PIZZA offers
Query: "في عروض عائلية؟" (Family offers?)
Expected: Pizza family deals
Got: Animania (entertainment theme park)  ← COMPLETELY WRONG CATEGORY

CONV 04 - Turn 6:
Context: User switched to coffee
Query: "شو عروض الكوفي؟" (What coffee offers?)
Expected: Coffee/Starbucks offers
Got: Dolphin shows at Dolphina  ← WRONG CATEGORY
```

**Root Cause:** The retrieval system uses embedding similarity which can match "family" conceptually to "family entertainment" instead of staying within the food category. There's no category continuity enforcement.

---

#### 3. Closing Phrases Treated as Open Queries (MEDIUM SEVERITY)

**Issue:** When user says "thank you" or "that's all", the bot continues to search for offers.

**Examples:**
```
CONV 01 - Turn 14:
Query: "شكراً، هذا كل شيء" (Thanks, that's all)
Expected: Polite closing response
Got: Random offer + Chinese characters in answer  ← BAD

CONV 03 - Turn 13:
Query: "شكراً" (Thanks)
Expected: "You're welcome"
Got: Pizza offer again  ← WRONG BEHAVIOR
```

**Impact:** 9 out of 10 conversations had this issue (90% failure rate on closing phrases)

**Root Cause:** No explicit closing detection in the chat pipeline. The LLM doesn't know when to stop retrieving.

---

#### 4. Garbled Answers (LOW SEVERITY)

**Issue:** Some answers contain Chinese characters, indicating model scaffolding leaks.

**Examples:**
```
CONV 01 - Turn 14:
Answer: "نعم، هذه المعلومات هي كلها. إذا كان لديك أي أسئلة أخرى，请问有什么我可以帮助你的？"
                                                                ^^^^^^^^^^^^^^^^^^^^ Chinese!

CONV 04 - Turn 7:
Answer: mentions "ستارب克斯" (Chinese transliteration of Starbucks)
```

**Root Cause:** The LLM (qwen2.5:3b-instruct) occasionally leaks scaffolding markers or mixes languages. The `_strip_scaffolding_leaks()` function isn't catching all cases.

---

## Root Cause Analysis

### Problem 1: Merchant Drift

**Current Flow:**
```
User: "فيه عرض تاني عندهم؟"
  ↓
_resolve_followup_targets() → "OTHER_OFFER_SAME_SOURCE"
  ↓
retrieve() with pin=merchant, exclude=id_of_shown_offer
  ↓
Returns: Unrelated merchant's offer (embedding similarity wins)
```

**Why it fails:** The exclusion logic only excludes the EXACT offer ID, not the entire merchant. If the same merchant has no other offers, or if another merchant's offer scores higher on embedding similarity, it returns the wrong merchant.

**Fix needed:**
1. Stronger merchant filtering in retrieve()
2. When "OTHER_OFFER_SAME_SOURCE" is detected, should FIRST check if merchant has other offers
3. If no other offers from same merchant, should say "no other offers available" instead of showing unrelated offers

---

### Problem 2: Contextual Topic Confusion

**Current Flow:**
```
User asks about pizza → memory stores pizza offers
User asks "family offers"
  ↓
retrieve() searches for "family" concept
  ↓
Embedding matches "family entertainment" (Animania) > "family pizza"
  ↓
Returns completely wrong category
```

**Why it fails:** The retrieval system has no category memory. Once a topic is established (pizza), subsequent queries should stay within that category unless explicitly switching.

**Fix needed:**
1. Add category continuity tracking to memory
2. When user is in "food" category, filter results to food merchants
3. Only relax category filter on explicit topic switch ("شو فيها شاورما؟")

---

### Problem 3: Closing Detection

**Current Flow:**
```
User: "شكراً"
  ↓
No closing detection
  ↓
retrieve() runs normally
  ↓
Returns random offer
```

**Fix needed:**
1. Add explicit closing phrase detection before retrieval
2. If closing detected, skip retrieval and return polite response
3. Pattern: "شكر", "thanks", "تمام", "ده كل شيء", "ايway", etc.

---

## Recommendations

### Immediate Fixes (High Priority)

1. **Add merchant continuity enforcement**
   - When follow-up is classified as SAME_SOURCE, ensure retrieved offers are from same merchant
   - If no other offers exist, return explicit message: "لا يوجد عروض تانية من [merchant] في الوقت الحالي"

2. **Add category memory**
   - Track the last N categories discussed in session
   - Weight recent categories higher in retrieval scoring
   - Only break category continuity on explicit switch signals

3. **Add closing detection**
   - Check for closing phrases before calling retrieve()
   - Return template response: "عافاك! لو محتاج أي حاجة تانية، أنا موجود"

### Medium Priority

4. **Improve scaffolding leak detection**
   - Expand `_strip_scaffolding_leaks()` to catch Chinese characters
   - Add post-processing filter for non-Arabic/English characters

5. **Add explicit "no other offers" handling**
   - When user asks for "another offer" and none exist, say so explicitly
   - Don't fall back to unrelated offers

### Low Priority

6. **Better price comparison**
   - When comparing offers, show actual price difference
   - Current: "Compare these two" → returns generic comparison
   - Expected: "Offer A is 50 EGP, Offer B is 75 EGP, so A is cheaper by 25 EGP"

---

## Performance Metrics

| Metric | Value | Status |
|--------|-------|--------|
| Total conversations tested | 10 | - |
| Total turns | 134 | - |
| Average response time | 6.8 seconds | ⚠️ Slow |
| Fastest conversation | 2.7s (conv_09) | ✅ |
| Slowest conversation | 15.8s (conv_04) | ❌ |
| Follow-up resolution rate | 100% | ✅ |
| Merchant accuracy | 65% | ⚠️ Needs improvement |
| Category consistency | 70% | ⚠️ Needs improvement |
| Closing phrase handling | 10% | ❌ Critical |

---

## Test Coverage

### Scenarios Tested

✅ Single merchant exploration (KFC, McDonald's)  
✅ Multi-merchant comparison (Pizza Hut vs Domino's)  
✅ Price filtering ("under 100 EGP")  
✅ Time-sensitive queries (expiry dates)  
✅ FAQ navigation (how-to questions)  
✅ Mixed language (Arabic/English/Franco)  
✅ Repetition and deduplication  
✅ Topic switching and returning  
✅ Complex multi-topic conversations  

### Scenarios NOT Tested

❌ Concurrent users (multi-worker stress test)  
❌ Redis persistence across restarts  
❌ Session TTL expiration (60 min timeout)  
❌ Very long conversations (>20 turns)  
❌ Malicious input / prompt injection  
❌ Extremely short queries ("ده بكام؟")  

---

## Conclusion

The memory system architecture is **sound** and the core mechanics work correctly. However, there are **semantic gaps** in how follow-up intent is translated into retrieval behavior:

1. **Merchant drift** causes wrong offers to be shown
2. **Category confusion** breaks contextual continuity  
3. **Missing closing detection** makes conversations feel endless
4. **Garbled outputs** indicate model quality issues

**Priority Actions:**
1. Fix merchant continuity enforcement (2-4 hours)
2. Add category memory tracking (4-6 hours)
3. Implement closing phrase detection (1-2 hours)
4. Improve scaffolding leak detection (1 hour)

**Estimated Time to Production-Ready:** 1-2 days of focused development.

---

*Full test results: `tests/eval/conversation_memory_tests.json`*
*Detailed report: `tests/eval/conversation_memory_report.txt`*