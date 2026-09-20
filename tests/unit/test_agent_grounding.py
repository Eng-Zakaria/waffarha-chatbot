"""
Deterministic grounding: planner-invented entities / price filters must be
dropped (and traced) when the user's own words do not corroborate them.

These tests never invoke Ollama or the index; they use a tiny fake FacetedCatalog
with the same resolve_* signature surface as core.faceted.FacetedCatalog.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


from agent.grounding import (
    bound_traceable,
    ground_search_args,
    grounded_category,
    grounded_merchants,
    grounded_product,
    numbers_in_text,
)


class _FakeFaceted:
    """Mirrors FacetedCatalog.resolve_* / merchants surface only."""

    def __init__(self, table):
        self._table = table  # query -> dict(merchants=[...], category=..., product=...)
        self.merchants = {m for row in table.values() for m in row.get("merchants", [])}

    def resolve_merchants(self, query):
        return list(self._table.get((query or "").strip().lower(), {}).get("merchants", []))

    def resolve_category(self, query):
        return self._table.get((query or "").strip().lower(), {}).get("category")

    def resolve_product(self, query):
        return self._table.get((query or "").strip().lower(), {}).get("product")


_PIZZA = _FakeFaceted({
    "عروض البيتزا وين القاها؟": {"product": "pizza"},
    "show me kfc offers": {"merchants": ["KFC"]},
    "cheaper kfc offers": {"merchants": ["KFC"]},
    "offers under 100 egp": {"merchants": [], "category": None, "product": None},
    "offers from zara": {"merchants": []},
    "buy pizza": {"product": "pizza"},
    "pizza within 100-150": {"product": "pizza"},
})


# ------------------------------------------------------------------ #
# numbers_in_text
# ------------------------------------------------------------------ #

def test_numbers_from_ascii_digits():
    assert numbers_in_text("offers under 300 EGP") == {300.0}


def test_numbers_from_arabic_indic_digits():
    assert numbers_in_text("عروض أقل من ١٨٩ جنيه") == {189.0}


def test_numbers_ignore_plain_words():
    assert numbers_in_text("cheapest offers right now") == set()


def test_numbers_empty_text():
    assert numbers_in_text(None) == set()


def test_bound_traceable_matches_literal_number():
    assert bound_traceable(100, [100.0])
    assert not bound_traceable(100, [59.5])
    assert not bound_traceable(None, [100.0])


# ------------------------------------------------------------------ #
# grounded_* resolvers (deterministic, catalog-driven)
# ------------------------------------------------------------------ #

def test_grounded_merchant_from_message():
    assert grounded_merchants("show me KFC offers", _PIZZA) == {"KFC"}


def test_grounded_category_none_when_message_has_no_category():
    assert grounded_category("show me KFC offers", _PIZZA) is None


def test_grounded_product_from_message():
    assert grounded_product("عروض البيتزا وين القاها؟", _PIZZA) == "pizza"


# ------------------------------------------------------------------ #
# ground_search_args -- the add, drop, trace contract
# ------------------------------------------------------------------ #

def test_keep_corroborated_merchant():
    args = {"merchant": "KFC", "query": "show me KFC offers", "limit": 5}
    cleaned, meta = ground_search_args(args, "show me KFC offers", _PIZZA)
    assert cleaned["merchant"] == "KFC"
    assert meta["dropped"] == []


def test_drop_invented_merchant_pizza_query():
    """The BLOCKING regression: Arabic pizza query -> planner claimed KFC.

    KFC appears nowhere in the user message, so the filter must be dropped
    and recorded in the trace."
    """
    args = {"merchant": "KFC", "query": "عروض البيتزا وين القاها؟", "limit": 6}
    cleaned, meta = ground_search_args(args, "عروض البيتزا وين القاها؟", _PIZZA)
    assert "merchant" not in cleaned
    assert "unconfirmed entity dropped: KFC" in meta["dropped"]
    assert meta["grounded"]["merchants"] == []
    assert meta["grounded"]["product"] == "pizza"


def test_drop_merchant_not_mentioned_at_all():
    args = {"merchant": "Zara", "query": "offers from Zara", "limit": 6}
    cleaned, meta = ground_search_args(args, "offers from Zara", _PIZZA)
    assert "merchant" not in cleaned
    assert "unconfirmed entity dropped: Zara" in meta["dropped"]


def test_keep_merchant_list_only_after_corroboration():
    args = {"merchant": ["KFC", "Pizza Hut"], "query": "cheaper KFC offers", "limit": 6}
    cleaned, meta = ground_search_args(args, "cheaper KFC offers", _PIZZA)
    assert cleaned["merchant"] == "KFC"
    assert "unconfirmed entity dropped: Pizza Hut" in meta["dropped"]


def test_keep_price_bound_traceable_to_message():
    args = {"price_range": [0, 100], "query": "offers under 100 EGP"}
    cleaned, meta = ground_search_args(args, "offers under 100 EGP", _PIZZA)
    # 100 is in the message; the invented lower bound 0 is dropped.
    assert cleaned["price_range"] == [None, 100.0]
    assert any("unconfirmed entity dropped" in d for d in meta["dropped"])


def test_drop_price_range_with_no_number_mentioned():
    args = {"price_range": [0, 310], "query": "cheaper KFC offers", "limit": 6}
    cleaned, meta = ground_search_args(args, "cheaper KFC offers", _PIZZA)
    assert "price_range" not in cleaned
    assert "unconfirmed entity dropped: price_range" in meta["dropped"]


def test_drop_product_not_in_message():
    args = {"product": "koshary", "query": "buy pizza", "limit": 5}
    cleaned, meta = ground_search_args(args, "buy pizza", _PIZZA)
    assert "unconfirmed entity dropped: koshary" in meta["dropped"]
    # the invented "koshary" is gone; the user's own "pizza" replaces it
    assert cleaned["product"] == "pizza"


def test_merge_corroborated_product_omitted_by_planner():
    """Regression: the user names a product the planner omitted entirely.

    "pizza within 100-150" must reach the tool call as a product filter, not
    degrade to a price-only facet search. Grounding corroborated the product
    already -- now it must be merged back into the args (without touching the
    corroborated price bounds the planner supplied)."""
    args = {"price_range": [100, 150], "limit": 5}
    cleaned, meta = ground_search_args(args, "pizza within 100-150", _PIZZA)
    assert cleaned["product"] == "pizza"
    assert cleaned["price_range"] == [100.0, 150.0]
    assert "corroborated entity merged: product=pizza" in meta["merged"]
    assert meta["dropped"] == []


def test_no_op_on_empty_planner_entities():
    args = {"limit": 5, "query": "show me KFC offers"}
    cleaned, meta = ground_search_args(args, "show me KFC offers", _PIZZA)
    assert cleaned == args
    assert meta["dropped"] == []


def test_planner_rewritten_query_replaced_by_user_text():
    """The semantic anchor is the user's words, never a planner rewrite that
    can smuggle an unconfirmed brand (Zara) into the tool call."""
    args = {"query": "Zara", "limit": 5}
    cleaned, meta = ground_search_args(args, "cheaper KFC offers", _PIZZA)
    assert cleaned["query"] == "cheaper KFC offers"
    assert "unconfirmed entity dropped: query=Zara" in meta["dropped"]


def test_query_kept_when_matches_user_text():
    args = {"query": "cheaper KFC offers", "limit": 6}
    cleaned, meta = ground_search_args(args, "cheaper KFC offers", _PIZZA)
    assert cleaned["query"] == "cheaper KFC offers"
    assert meta["dropped"] == []


def test_query_planner_rewrite_kept_for_arabic_anchor():
    """A planner rewrite that stays inside the user's kiosk (pizza mention)
    is kept — grounding only protects against *new* unconfirmed content."""
    args = {"query": "عروض البيتزا", "limit": 6}
    cleaned, meta = ground_search_args(args, "عروض البيتزا وين القاها؟", _PIZZA)
    assert cleaned["query"] == "عروض البيتزا"
    assert meta["dropped"] == []