"""Agent tools backed by the existing deterministic catalog capabilities:

- search_offers: structured-first, offline pull of offers via FacetedCatalog
  (merchant / product / category / price-range), with a hybrid semantic
  retrieval fallback when nothing structured resolves. Same deterministic
  ranking (sold_count / discount) and same data the old cascade used.
- get_offer: fetch a single remembered offer/FAQ doc by id (RagEngine docs).

No tool generates SQL, accesses the filesystem, or touches arbitrary HTTP:
all data access goes through the RagEngine / FacetedCatalog layer in
ToolContext.facade, which stays the authoritative source of business logic.
"""
from __future__ import annotations

from typing import ClassVar

from agent.tools import Tool, ToolContext, ToolResult


def _price_of(meta: dict) -> float | None:
    """Deterministic price extraction from a doc's metadata (same approach
    rag_engine uses for price-range filtering)."""
    raw = meta.get("price")
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", "").strip() or "0")
    except (TypeError, ValueError):
        return None


def _matches_price_span(item: dict, price) -> bool:
    if not price:
        return True
    lo, hi = price
    p = _price_of(item.get("metadata", {}))
    if p is None:
        return False
    if lo is not None and p < lo:
        return False
    return not (hi is not None and p > hi)


def _concrete_span(price):
    """FacetedCatalog price filters require concrete numbers; the planner
    may emit open ranges ([min] or [min, null]). Map None -> unbounded but
    concrete so the faceted layer accepts it."""
    if not price:
        return None
    lo, hi = price
    return [lo if lo is not None else 0.0, hi if hi is not None else 1e18]


def _dedup_offers(items: list) -> list:
    seen = set()
    out = []
    for it in items:
        key = it.get("metadata", {}).get("id")
        if key is None:
            out.append(it)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


class SearchOffersTool(Tool):
    """Find offers matching a merchant, category, product and/or price range.

    Structured first (FacetedCatalog, offline, deterministic ranking by
    sold_count/discount); when nothing resolves, falls back to hybrid
    semantic retrieval. Unknown merchants are reported as a deterministic
    negative, never fabricated."""

    name = "search_offers"
    purpose = ("Find Waffarha offers by merchant, category, product, or price "
               "range. Returns real offers with price/discount/sold ranking.")
    input_schema: ClassVar[dict] = {
        "query": {"type": "str", "optional": True},
        "merchant": {"type": "str", "optional": True},
        "category": {"type": "str", "optional": True},
        "product": {"type": "str", "optional": True},
        "price_range": {"type": "number_range", "optional": True},
        "exclude": {"type": "str_list", "optional": True},
        "limit": {"type": "int", "optional": True},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        facade = ctx.facade
        faceted = getattr(facade, "faceted", None)
        limit = max(1, min(int(args.get("limit") or 6), 12))
        price = args.get("price_range")
        exclude = set(args.get("exclude") or [])
        lang = ctx.reply_lang

        if faceted is None:
            return ToolResult(
                ok=False, error="faceted catalog unavailable",
                summary="search_offers needs the in-memory catalog",
            )

        merchant = args.get("merchant")
        product = args.get("product")
        category = args.get("category")
        query = args.get("query") or ctx.query
        price = _concrete_span(args.get("price_range"))

        # --- structured resolution ---------------------------------------
        canonical = None
        if merchant:
            resolved = faceted.resolve_merchants(merchant)
            canonical = resolved[0] if resolved else None

        items = []
        if canonical:
            items = faceted.offers_for_merchant(
                canonical, lang, limit, exclude_ids=exclude or None,
                price_filter=price)
            provenance = f"faceted:merchant={canonical}"
        elif product:
            key = _known_key(faceted, "product", product)
            if key:
                items = faceted.offers_for_product(
                    key, lang, limit, exclude_ids=exclude or None,
                    price_filter=price)
                provenance = f"faceted:product={key}"
            else:
                return ToolResult(
                    ok=True, items=[], summary=f"no offers for product '{product}'",
                    note={"kind": "no_matches", "product": product},
                )
        elif category:
            key = _known_key(faceted, "category", category)
            if key:
                items = faceted.offers_for_category(key, lang, limit, price_filter=price)
                provenance = f"faceted:category={key}"
            else:
                return ToolResult(
                    ok=True, items=[], summary=f"no offers for category '{category}'",
                    note={"kind": "no_matches", "category": category},
                )
        elif price:
            items = faceted.in_price_range(price[0], price[1], lang, limit)
            provenance = f"faceted:price={price}"
        else:
            # --- hybrid semantic fallback --------------------------------
            raw = facade.retrieve(
                query, top_k=max(limit * 2, 8), history=ctx.history,
                recent_offers=ctx.recent_offers,
                normalized_query=ctx.normalized_query or None)
            for r in raw:
                meta = r.get("metadata", {})
                if meta.get("source") != "offer":
                    continue
                item = {"metadata": meta, "_retrieval_score": r.get("combined_score")}
                if _matches_price_span(item, price) and (not exclude or meta.get("id") not in exclude):
                    items.append(item)
            items = _dedup_offers(items)[:limit]
            provenance = "hybrid:semantic"

        # deterministic negative for an unknown merchant mention
        if not items and not canonical and product is None and category is None:
            unknown = faceted.unknown_merchant_mention(query or merchant or "")
            if unknown:
                return ToolResult(
                    ok=True, items=[], summary=f"no known merchant '{unknown}'",
                    note={"kind": "unknown_merchant", "merchant": unknown},
                )

        items = _dedup_offers(items)[:limit]
        return ToolResult(
            ok=bool(items) or not items,  # ok even for a clean "no matches" negative
            items=items,
            summary=f"{len(items)} offer(s) via {provenance}"
            + (f" (price {price})" if price else ""),
            note={"kind": "matches" if items else "no_matches",
                  "provenance": provenance, "relaxable_price": price is not None},
        )


def _known_key(faceted, kind: str, value: str):
    """Best-effort: `value` may already be a canonical key (merchant/resolver
    output) or free text that needs resolving. Returns the key to pass to the
    faceted accessors, or None."""
    if kind == "product":
        known = getattr(faceted, "_product_offers", {})
        if value in known:
            return value
        return faceted.resolve_product(value)
    if kind == "category":
        known = getattr(faceted, "_category_offers", {})
        if value in known:
            return value
        return faceted.resolve_category(value)
    return value


class GetOfferTool(Tool):
    """Fetch a single offer/FAQ doc by id (used to resolve a referenced or
    previously-shown offer into a grounded card)."""

    name = "get_offer"
    purpose = ("Fetch one specific offer (or FAQ) by its id, e.g. to answer "
               "about an offer the user already mentioned or saw earlier.")
    input_schema: ClassVar[dict] = {
        "id": {"type": "str", "optional": False},
        "source": {"type": "str", "optional": True},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        facade = ctx.facade
        raw_id = str(args.get("id") or "").strip()
        if not raw_id:
            return ToolResult(ok=False, summary="get_offer needs an id", error="missing id")
        source = str(args.get("source") or "offer").strip() or "offer"
        if ":" in raw_id and raw_id.split(":", 1)[0] in ("offer", "faq"):
            source, raw_id = raw_id.split(":", 1)
        doc = facade._lookup_doc(source, raw_id, lang_hint=ctx.reply_lang)
        if doc is None:
            return ToolResult(
                ok=False, summary=f"{source}:{raw_id} not found",
                error=f"no {source} doc with id {raw_id}",
            )
        item = {"metadata": dict(doc.get("metadata", {})), "tool": self.name}
        return ToolResult(
            ok=True, items=[item],
            summary=f"fetched {source}:{raw_id}",
            note={"kind": "match", "provenance": f"lookup:{source}"},
        )


def register_catalog_tools(registry):
    """Register the Stage-2 offline catalog tools on a ToolRegistry."""
    registry.register(SearchOffersTool())
    registry.register(GetOfferTool())
    return registry