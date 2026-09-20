"""Deterministic reproduction of the structured constraint-loss bug:
'عايز بيتزا من 100 لـ150' should return PIZZA offers inside 100-150 EGP, but a
search_offers call with {query, price_range} and NO merchant/product/category
runs `faceted.in_price_range(100,150)` over the WHOLE catalog -- pizza
constraint silently lost, unrelated offers returned.

Fully offline: builds FacetedCatalog from the real index docs.pkl exactly as
RagEngine does. No RagEngine construction, no Ollama, no retrieval calls.

Run:  python tests/repro/bug_pizza_loss.py
"""
import os
import pickle
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from core.faceted import FacetedCatalog  # noqa: E402


def _price(meta) -> float | None:
    raw = meta.get("price")
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip() or "0")
    except (TypeError, ValueError):
        return None


def _is_pizza_offer(item) -> bool:
    meta = item.get("metadata", {})
    blob = f"{meta.get('title') or ''} {meta.get('merchant') or ''}"
    return bool(re.search(r"pizza|بيتز", blob, re.IGNORECASE))


def main():
    index_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..",
                             "data", "index", "BAAI__bge-m3", "qdrant")
    docs_path = os.path.join(index_dir, "docs.pkl")
    if not os.path.exists(docs_path):
        print(f"FATAL: index docs not found at {docs_path}")
        print("Expected layout: data/index/BAAI__bge-m3/qdrant/docs.pkl")
        return 2

    with open(docs_path, "rb") as f:
        docs = pickle.load(f)
    faceted = FacetedCatalog(docs)

    query = "عايز بيتزا من 100 لـ150"
    lo, hi = 100.0, 150.0
    print("=" * 78)
    print("BUG REPRO: structured constraint lost in search_offers")
    print(f"  query      = {query!r}")
    print(f"  price_range= [{lo}, {hi}]")
    print("=" * 78)

    # 1. The deterministic understanding capability that SHOULD have fired.
    pizza_key = faceted.resolve_product(query)
    print(f"\n[1] faceted.resolve_product(query)    -> {pizza_key!r}")
    print(f"    (the deterministic product resolver CAN see 'pizza'; it just")
    print(f"     is never consulted to FILL the entity when the planner misses it)")

    # 2. The failure-mode tool call: {query, price_range}, product missing.
    result = faceted.in_price_range(lo, hi, "ar", 8)
    print(f"\n[2] search_offers(query, price_range) (planner missed product)")
    print(f"    -> faceted.in_price_range({lo:.0f}, {hi:.0f}) over the WHOLE catalog")
    print(f"    first {len(result)} offers by price:")
    pizza = sum(1 for it in result if _is_pizza_offer(it))
    for it in result:
        meta = it.get("metadata", {})
        p = _price(meta)
        mark = "PIZZA" if _is_pizza_offer(it) else "      "
        print(f"    [{mark}] {meta.get('merchant')}: {meta.get('title')} @ {p:.0f} EGP")

    # 3. What the correct path (product + price) would have returned.
    correct = faceted.offers_for_product(pizza_key, "ar", limit=6, price_filter=[lo, hi]) \
        if pizza_key else []
    print(f"\n[3] offers_for_product('pizza', price in [{lo:.0f},{hi:.0f}]) -> {len(correct)} offers")
    for it in correct:
        meta = it.get("metadata", {})
        p = _price(meta)
        print(f"    [PIZZA] {meta.get('merchant')}: {meta.get('title')} @ {p:.0f} EGP")

    n_all = 8
    n_correct = len(correct)
    print("\n" + "=" * 78)
    print("RESULT")
    print("=" * 78)
    print(f"  AS RAN      : {n_all} offers, {pizza} pizza / {n_all}  "
          f"-> {pizza}/{n_all} still pizza (constraint SHREDDED)" if pizza < n_all
          else f"  AS RAN      : {n_all} offers, {pizza}/{n_all} pizza")
    print(f"  AS IT SHOULD: product constraint preserved -> {n_correct} real pizza offers")
    bug = pizza < n_correct and n_all > 0
    print(f"  BUG REPRODUCED: {'YES' if bug else 'no'}")
    return 0 if bug else 1


if __name__ == "__main__":
    raise SystemExit(main())