"""Smoke test for core/faceted.py against the real index docs."""
import pickle
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config
from core.faceted import FacetedCatalog

DOCS = os.path.join(config.INDEX_DIR, "index", "BAAI__bge-m3", "qdrant", "docs.pkl")
with open(DOCS, "rb") as f:
    docs = pickle.load(f)
print(f"loaded {len(docs)} docs")

fc = FacetedCatalog(docs, aliases=config.MERCHANT_ALIASES,
                    offer_intent_words=config.__dict__.get("OFFER_INTENT_WORDS", set()))

tests = [
    "عروض كنتاكي",
    "عروض ماكدونالدز",
    "Pizza Hut offers",
    "عندي KFC خصومات",
    "عايز عروض من بيتزا هت",
    "عروض برجر كنج",
    "عروض أكل",
    "عروض قهوة",
    "كشري",
    "عايز شاورما",
    "فيه بيتزا فين؟",
    "عروض ستاربكس",
    "offers from fun kingdom",
    "ارخص حاجة",
    "عروض سوشي",
    "سباكة",
]
for q in tests:
    merchants = fc.resolve_merchants(q)
    cat = fc.resolve_category(q)
    prod = fc.resolve_product(q)
    unknown = fc.unknown_merchant_mention(q)
    print(f"\nQ: {q}")
    print(f"  merchants={merchants} category={cat} product={prod} unknown={unknown}")
    if merchants:
        m = merchants[0]
        offs = fc.offers_for_merchant(m, reply_lang="en", limit=3)
        print(f"  merchant('{m}'): {len(offs)} offers; first titles: {[o['metadata'].get('title') for o in offs[:2]]}")
    elif prod:
        offs = fc.offers_for_product(prod, reply_lang="en", limit=5)
        merchants_in = sorted({o.get('_merchant') for o in offs})
        print(f"  product('{prod}'): {len(offs)} offers, distinct merchants={len(merchants_in)}: {merchants_in[:6]}")
    elif cat:
        offs = fc.offers_for_category(cat, reply_lang="en", limit=3)
        print(f"  category('{cat}'): {len(offs)} offers")

# superlative sanity
print("\ncheapest:", [o['metadata'].get('title') for o in fc.cheapest("en") or []])
print("highest discount:", [o['metadata'].get('title') for o in fc.highest_discount("en") or []])
# price range
r = fc.in_price_range(0, 100, reply_lang="en", limit=5)
print("in_price_range(0,100):", len(r), [o['metadata'].get('price') for o in r[:5]])