#!/usr/bin/env python
# -*- coding: utf-8 -*-
from ingest.catalog_queries import is_catalog_query, _MERCHANT_RE, _SUPERLATIVE_RE, _FAQ_RE, _MERCHANT_PATTERNS, _SUPERLATIVE_PATTERNS, _FAQ_PATTERNS
import re

print("=== Merchant Patterns ===")
for i, p in enumerate(_MERCHANT_PATTERNS):
    print(f"  {i}: {p}")

print("\n=== Superlative Patterns ===")
for i, p in enumerate(_SUPERLATIVE_PATTERNS):
    print(f"  {i}: {p}")

print("\n=== FAQ Patterns ===")
for i, p in enumerate(_FAQ_PATTERNS):
    print(f"  {i}: {p}")

# Debug each test
tests = [
    ('كم سعر عرض كنتاكي؟', 'should be catalog'),
    ('KFC price', 'should be catalog'),
    ('What is Waffarha', 'should NOT be catalog - FAQ'),
    ('How much is KFC offer', 'should be catalog'),
    ('How much', 'should NOT be catalog - generic'),
    ('كم الخصم على ماكدونالدز دلوقتي؟', 'should be catalog'),
    ('Mixed: discount بتاع KFC كام؟', 'should be catalog'),
    ('What is the cheapest offer', 'should be catalog - superlative'),
]

for query, note in tests:
    q_lower = query.lower()
    print(f"\n--- Testing: {query!r} ---")

    # Check merchant patterns
    merchant_match = False
    for i, re_obj in enumerate(_MERCHANT_RE):
        m = re_obj.search(q_lower)
        if m:
            print(f"  Merchant pattern {i} MATCHED: {m.groups()}")
            merchant_match = True
            break
    if not merchant_match:
        print(f"  NO merchant pattern matched")

    # Check superlative patterns
    super_match = False
    for i, re_obj in enumerate(_SUPERLATIVE_RE):
        if re_obj.search(q_lower):
            print(f"  Superlative pattern {i} MATCHED")
            super_match = True
            break
    if not super_match:
        print(f"  NO superlative pattern matched")

    # Check FAQ patterns
    faq_match = False
    for i, re_obj in enumerate(_FAQ_RE):
        if re_obj.search(q_lower):
            print(f"  FAQ pattern {i} MATCHED: {_FAQ_PATTERNS[i]}")
            faq_match = True
            break

    result = is_catalog_query(query)
    status = 'PASS' if result else 'FAIL'
    print(f"Result: catalog={result} ({note})")