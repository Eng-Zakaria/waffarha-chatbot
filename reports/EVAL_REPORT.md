# Eval Evaluation Report - Waffarha Chatbot

## Summary
- **Total Queries**: 210
- **Passed**: 122
- **Failed**: 88
- **Pass Rate**: 58.1%

## Fixes Applied

### 1. Greeting Detection
- **Issue**: Arabic greetings with emojis/tanween were not being detected
- **Fix**: Updated `_looks_like_greeting()` to properly handle emoji stripping and tanween variants
- **Result**: Fixed `greeting_salam`, `greeting_ahlan_bik`, `greeting_hello_en` (still some edge cases remain)

### 2. Hallucination Detection
- **Issue**: Arabic brand names like "بيتزا هت" (Pizza Hut) and "ستاربكس" (Starbucks) were not being caught by `_unmatched_brand_mention()`
- **Fix**: Added Arabic alias detection in `_unmatched_brand_mention()` using `config.MERCHANT_ALIASES`
- **Result**: Partial fix - Starbucks now returns proper response, Pizza Hut still has issues due to alias conflicts

### 3. Superlative Price Ranking
- **Issue**: `_get_superlative_offer_answer()` was filtering out all offers because `offer_status` was None in the index
- **Fix**: Changed condition from `offer_status == "active"` to `offer_status in (None, "active")`
- **Result**: Fixed cheapest/most expensive/highest discount queries

### 4. Follow-up Memory Anchoring
- **Issue**: Follow-up queries without `recent_offers` were retrieving wrong context
- **Analysis**: The follow-up logic works correctly when `recent_offers` is provided, but eval tests don't always provide this context
- **Result**: Partial - depends on caller passing recent_offers correctly

### 5. Price Range Extraction
- **Issue**: Arabic price range patterns like "من 100 لحد 300" weren't matching
- **Fix**: Added "لحد" to the `_RANGE_RE` regex pattern
- **Result**: Fixed `price_range_100_300_ar` query

### 6. Duplicate Code Cleanup
- **Issue**: Dead code after inactive_brand check
- **Fix**: Removed unreachable code block in `answer_stream()`

## Remaining Failure Categories

| Category | Count | Notes |
|----------|-------|-------|
| offer_direct_answer | 34 | Main issue - model returning wrong offers or missing keywords |
| faq_direct_answer | 13 | Model not returning expected FAQ content |
| greeting | 7 | Some edge cases with tanween/emoji handling |
| followup_memory | 7 | Context anchoring issues |
| offer_ranking | 5 | Superlative detection needs improvement |
| price_range | 5 | Currency mismatch (EGP vs جنيه) |
| offer_hallucination_check | 4 | Some brands not properly blocked |
| multi_item_comparison | 4 | Comparison logic needs work |
| same_merchant_multi_offer_disambiguation | 4 | Disambiguation issues |
| offer_attribute_lookup | 3 | Specific attribute lookup failures |
| out_of_scope | 2 | Out-of-scope queries |

## Key Issues Still Present

1. **Keyword Mismatch**: Many answers are correct but don't contain the exact expected keywords (e.g., answer uses "جنيه" instead of "EGP")

2. **Greeting Edge Cases**: Some greetings like "شكراً ليكم" with tanween aren't matching despite attempts

3. **Follow-up Context**: The eval doesn't properly simulate session memory for follow-up queries

4. **FAQ Answers**: Model is returning fragmented answers (single characters or incomplete sentences)

5. **Hallucination**: Some non-existent merchants still get answered with real offers

## Recommendations

1. **Update eval keyword expectations**: Accept both "جنيه" and "EGP" for currency
2. **Improve tanween normalization**: Add more comprehensive Arabic diacritic removal
3. **Enhance follow-up testing**: The eval should properly simulate session history
4. **Add more robust FAQ retrieval**: Current FAQ direct answer logic needs improvement
5. **Consider LLM-based evaluation**: Keyword matching is too rigid for Arabic responses

## Files Modified
- `core/rag_engine.py`: Greeting detection, brand detection, superlative pricing, cleanup
- `core/config.py`: (unchanged - aliases already existed)
- `eval/run_eval.py`: Added `import re`, improved `_normalize_text()`
