from ingest.catalog_queries import is_catalog_query, _detect_intent

tests = [
    'Tell me about the Asian Wok offer',
    'Tell me about KFC offers',
    'KFC offers',
    'offers under 200 EGP',
    'cheapest offer',
    'my coupons',
    'How do I register',
    'What KFC offers do you have',
    'Show me KFC deals',
    'offers from KFC',
    'deals at Pizza Hut',
]

for t in tests:
    print(f'{t!r}: catalog={is_catalog_query(t)}, intent={_detect_intent(t)}')