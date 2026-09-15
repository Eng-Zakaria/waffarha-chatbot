"""
Stage-3 cascade tools: deterministic wrappers over the old answer-stream
capabilities folded into the typed tool set.

- retrieve_faq:         FAQ topic routing + direct-answer scoring (the old
                        `_route_faq_topic` / `_faq_topic_answer` /
                        `_get_faq_direct_answer` paths).
- compare_offers:       multi-card comparison over >=2 resolved offers (the
                        old `_get_comparison_answer` behavior).
- superlative_offer:    cheapest / most expensive / highest discount via the
                        FacetedCatalog extremum accessors (the old
                        `_get_superlative_offer_answer` path).
- catalog:              `scope=personal` -> user-scoped coupon service (gated
                        by config.PERSONAL_QUERIES_ENABLED, deterministic
                        negatives otherwise); `scope=all` -> the Stage-2
                        structured catalog search.

Every tool is safe-failure + provenance-tagged and produces a deterministic
"text" block the engine can render directly, plus the evidence items for the
evidence gate/trace. No tool generates SQL, touches the filesystem, or writes
arbitrary HTTP.

IMPORTANT (import discipline): core.rag_engine takes ~7s to import and pulls
heavy deps. The stage-2 unit suite imports agent.tools; therefore this module
must NOT import core.rag_engine at module scope. The few module-level
helpers we reuse (e.g. _route_faq_topic) are fetched lazily through `_r()`,
which also keeps the table of contents identical to the shipped cascade.
"""
from __future__ import annotations

from typing import ClassVar

from agent.tools import Tool, ToolContext, ToolResult


def _r():
    """Deferred import of core.rag_engine (heavy). Cached by importlib."""
    import core.rag_engine
    return core.rag_engine


def _lazy(facade, name, module_attr=None):
    """Return bound callable from the facade if present, else the module-level
    helper (deferred). None if neither exists."""
    bound = getattr(facade, name, None)
    if callable(bound):
        return bound
    if module_attr:
        return getattr(_r(), module_attr, None)
    return None


def _metadata(entry: dict) -> dict:
    return entry.get("metadata", {}) if isinstance(entry, dict) else {}


def _stored_by_id(recent_offers: list, rid) -> dict | None:
    rid = str(rid or "").strip()
    if not rid:
        return None
    for o in recent_offers or []:
        mid = str(_metadata(o).get("id") or "")
        if mid == rid or mid.endswith(":" + rid) or rid.endswith(":" + mid):
            return o
    return None


def _card_text(facade, entry: dict, lang: str) -> str:
    """Deterministic emoji card via the facade renderer when available (real
    engine), else a plain, fact-only bullet so offline tests/fakes stay safe."""
    cards = getattr(facade, "_offer_card_blocks", None)
    if callable(cards):
        try:
            block = cards([entry], lang)
            if block:
                return block[0]
        except Exception:  # noqa: BLE001, S110 -- never let a renderer crash the tool
            pass
    meta = _metadata(entry)
    title = (meta.get("title") or meta.get("question")
             or meta.get("merchant") or meta.get("id") or "")
    price = meta.get("price")
    price_txt = f" — {price} EGP" if isinstance(price, (int, float)) else ""
    return f"- {title}{price_txt}"


class RetrieveFaqTool(Tool):
    """Answer a policy/how-to/status question from the FAQ corpus w/o offers.

    Tries the deterministic topic rules first (`_route_faq_topic`); when none
    matches, falls back to hybrid retrieval + the FAQ direct-answer score
    gates. Returns NO offer cards -- just a grounded FAQ answer, or a clean
    negative so the engine can fall through to semantic search."""

    name = "retrieve_faq"
    purpose = ("Answer policy/how-to/status questions (refunds, payments, "
               "coupon use, order status) from the FAQ corpus. Use when the "
               "user asks 'how do I...' / 'what is the policy' / a status "
               "meaning, not when they want offers.")
    input_schema: ClassVar[dict] = {
        "query": {"type": "str", "optional": True},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        facade = ctx.facade
        query = (args.get("query") or ctx.query or "").strip()
        if not query:
            return ToolResult(
                ok=False, summary="retrieve_faq needs a query", error="missing query")

        norm = getattr(facade, "normalize_arabizi_and_arabic", None)
        nq = norm(query) if callable(norm) else query

        # 1. deterministic topic rules
        route_fn = _lazy(facade, "_route_faq_topic", "_route_faq_topic")
        faq_answer = _lazy(facade, "_faq_topic_answer")
        if route_fn is not None:
            try:
                route = route_fn(query, nq)
            except Exception:  # noqa: BLE001
                route = None
            if route:
                rule_name, faq_id, bilingual = route
                if faq_answer is not None:
                    try:
                        text = faq_answer(faq_id, ctx.reply_lang, bilingual)
                    except Exception:  # noqa: BLE001
                        text = None
                    if text:
                        return ToolResult(
                            ok=True,
                            items=[{"metadata": _faq_meta(faq_id, text)}],
                            summary=f"faq topic matched ({rule_name})",
                            note={"kind": "faq_answer",
                                  "provenance": f"faq_topic:{rule_name}",
                                  "text": text},
                        )

        # 2. hybrid retrieval + direct-answer score gates
        retrieve = getattr(facade, "retrieve", None)
        direct = _lazy(facade, "_get_faq_direct_answer")
        if callable(retrieve) and direct is not None:
            try:
                retrieved = retrieve(
                    query, top_k=8, history=ctx.history,
                    recent_offers=ctx.recent_offers,
                    normalized_query=nq or None)
                top_id = _metadata(retrieved[0]).get("id") if retrieved else faq_id_if(route)
                answer = direct(retrieved, ctx.reply_lang, query=query,
                                multi_item=None, followup_verdict="NEW_TOPIC")
            except Exception:  # noqa: BLE001
                answer = None
                top_id = None
            if answer:
                return ToolResult(
                    ok=True,
                    items=[{"metadata": _faq_meta(top_id or "faq", answer)}],
                    summary="faq direct answer",
                    note={"kind": "faq_answer", "provenance": "faq:direct",
                          "text": answer},
                )

        return ToolResult(
            ok=True, items=[],
            summary="no FAQ answer matched",
            note={"kind": "no_match", "provenance": "faq:none"})


def faq_id_if(route) -> str | None:
    try:
        return route[1] if route else None
    except Exception:  # noqa: BLE001
        return None


def _faq_meta(faq_id, answer: str) -> dict:
    return {"source": "faq", "id": str(faq_id), "answer": answer, "title": ""}


class CompareOffersTool(Tool):
    """Compare >=2 offers side by side (multi-card render). The offers come
    from stored session state (offer_ids) or from one resolved merchant."""

    name = "compare_offers"
    purpose = ("Compare two or more offers side by side. Pass offer_ids that "
               "reference offers already discussed this session, or a merchant "
               "whose top 2 offers should be compared.")
    input_schema: ClassVar[dict] = {
        "offer_ids": {"type": "str_list", "optional": True},
        "merchant": {"type": "str", "optional": True},
        "limit": {"type": "int", "optional": True},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        facade = ctx.facade
        entries = []
        origin = None
        ids = [str(x) for x in (args.get("offer_ids") or []) if str(x).strip()]
        if ids:
            for rid in ids:
                hit = _stored_by_id(ctx.recent_offers, rid)
                if hit is not None:
                    entries.append(hit)
            origin = "ids:" + ",".join(ids)

        if len(entries) < 2 and args.get("merchant"):
            faceted = getattr(facade, "faceted", None)
            merchant = str(args["merchant"]).strip()
            if faceted is not None:
                try:
                    resolved = faceted.resolve_merchants(merchant) or []
                    scope = resolved[0] if len(resolved) == 1 else None
                    if scope is None and (faceted.merchants or {}) and merchant in faceted.merchants:
                        scope = merchant
                    if scope:
                        entries = faceted.offers_for_merchant(
                            scope, ctx.reply_lang, min(int(args.get("limit") or 2), 2))
                        origin = f"merchant:{scope}"
                except Exception:  # noqa: BLE001, S110 -- resolver must never crash the tool
                    pass
        if not entries:
            entries = ctx.recent_offers and ctx.recent_offers[:2] or []
            origin = origin or "session:top2"

        if len(entries) < 2:
            return ToolResult(
                ok=True, items=[],
                summary="need 2+ offers to compare",
                note={"kind": "no_match", "provenance": "compare:none"})

        render = _lazy(facade, "_get_comparison_answer")
        if render is not None:
            try:
                text = render(entries, ctx.reply_lang)
            except Exception:  # noqa: BLE001
                text = None
            if not text:
                text = None
        else:
            text = None
        if text is None:
            intro = ("Here's a comparison of the offers:" if ctx.reply_lang == "en"
                     else "هنا مقارنة بين العروض:")
            text = intro + "\n\n" + "\n\n".join(
                _card_text(facade, e, ctx.reply_lang) for e in entries)
            if not text.strip():
                return ToolResult(ok=True, items=[], summary="nothing to compare",
                                  note={"kind": "no_match"})
        return ToolResult(
            ok=True, items=entries,
            summary=f"compared {len(entries)} offers",
            note={"kind": "compare", "provenance": origin or "compare:session",
                  "text": text})


class SuperlativeOfferTool(Tool):
    """Return the catalog's true extremum offer (cheapest / most expensive /
    highest discount), optionally scoped to one merchant or category."""

    name = "superlative_offer"
    purpose = ("Answer 'cheapest / most expensive / highest discount' questions "
               "by scanning the full catalog deterministically (never top-k). "
               "Optional merchant or category scope when the user names one.")
    input_schema: ClassVar[dict] = {
        "direction": {"type": "str",
                      "values": ["cheapest", "most_expensive", "highest_discount"],
                      "optional": False},
        "merchant": {"type": "str", "optional": True},
        "category": {"type": "str", "optional": True},
    }

    _INTRO: ClassVar[dict[str, dict[str, str]]] = {
        "cheapest": {"en": "Here's the cheapest one available right now:",
                     "ar": "ده أرخص عرض متاح دلوقتي:"},
        "most_expensive": {"en": "Here's the most expensive one available right now:",
                           "ar": "ده أغلى عرض متاح دلوقتي:"},
        "highest_discount": {"en": "Here's the offer with the highest discount right now:",
                             "ar": "ده العرض اللي عليه أعلى خصم دلوقتي:"},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        facade = ctx.facade
        faceted = getattr(facade, "faceted", None)
        direction = str(args.get("direction") or "").strip()
        if direction not in self._INTRO:
            return ToolResult(
                ok=False, summary="bad superlative direction",
                error=f"direction must be one of {sorted(self._INTRO)}")
        if faceted is None:
            return ToolResult(
                ok=False, summary="faceted catalog unavailable",
                error="superlative_offer needs the in-memory catalog")

        method = {
            "cheapest": faceted.cheapest,
            "most_expensive": faceted.most_expensive,
            "highest_discount": faceted.highest_discount,
        }[direction]

        scope = None
        category = None
        if args.get("merchant"):
            try:
                resolved = faceted.resolve_merchants(str(args["merchant"])) or []
                scope = resolved[0] if len(resolved) == 1 else None
            except Exception:  # noqa: BLE001
                scope = None
        if not scope and not args.get("category") and (faceted.merchants or {}):
            # no explicit scope: let the catalog resolver decide from the query
            pass
        if args.get("category"):
            try:
                category = faceted.resolve_category(str(args["category"]))
            except Exception:  # noqa: BLE001
                category = None

        try:
            entries = method(ctx.reply_lang, merchant=scope, category=category)
        except Exception:  # noqa: BLE001
            entries = None
        if not entries:
            return ToolResult(
                ok=True, items=[], summary=f"no offer for {direction}",
                note={"kind": "no_match",
                      "provenance": f"superlative:{direction}"})

        intro = self._INTRO[direction][ctx.reply_lang]
        text = intro + "\n" + _card_text(facade, entries[0], ctx.reply_lang)
        return ToolResult(
            ok=True, items=entries,
            summary=f"{direction} offer",
            note={"kind": "match", "provenance": f"superlative:{direction}",
                  "text": text})


class CatalogTool(Tool):
    """Search the offer catalog. scope=all behaves like search_offers;
    scope=personal is the user-scoped coupon/catalog service, gated by
    config.PERSONAL_QUERIES_ENABLED with deterministic negatives otherwise."""

    name = "catalog"
    purpose = ("Search Waffarha's catalog. scope=all finds offers by "
               "merchant/category/product/price like search_offers; scope=personal "
               "answers about the user's own coupons/status when they're signed in.")
    input_schema: ClassVar[dict] = {
        "scope": {"type": "str", "values": ["personal", "all"], "optional": True},
        "query": {"type": "str", "optional": True},
        "merchant": {"type": "str", "optional": True},
        "category": {"type": "str", "optional": True},
        "product": {"type": "str", "optional": True},
        "price_range": {"type": "number_range", "optional": True},
        "limit": {"type": "int", "optional": True},
    }

    def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        scope = str(args.get("scope") or "all").strip().lower()
        if scope == "personal":
            return self._personal(ctx, args.get("query") or ctx.query)
        inner_args = {k: v for k, v in args.items() if k != "scope"}
        from agent.tools.catalog_tools import SearchOffersTool
        result = SearchOffersTool().run(ctx, inner_args)
        result.note = dict(result.note or {})
        result.note["tool"] = "catalog"
        return result

    def _personal(self, ctx: ToolContext, query: str) -> ToolResult:
        query = (query or ctx.query or "").strip()
        if not ctx.user_id:
            return ToolResult(
                ok=True, items=[], summary="personal catalog needs a signed-in user",
                note={"kind": "no_user", "provenance": "personal:none"})
        try:
            from core import config
            enabled = bool(config.PERSONAL_QUERIES_ENABLED)
        except Exception:  # noqa: BLE001
            enabled = False
        if not enabled:
            return ToolResult(
                ok=True, items=[], summary="personal catalog disabled",
                note={"kind": "personal_disabled", "provenance": "personal:none"})
        try:
            from personal.personal_queries import (
                PersonalQueryService,
                is_personal_query,
            )
        except Exception:  # noqa: BLE001
            return ToolResult(
                ok=False, summary="personal service unavailable",
                error="personal_package_unavailable")
        if not is_personal_query(query):
            return ToolResult(
                ok=True, items=[],
                summary="not a personal-catalog question",
                note={"kind": "not_personal", "provenance": "personal:none"})
        try:
            out = PersonalQueryService().handle(query, int(ctx.user_id), ctx.reply_lang)
        except Exception:  # noqa: BLE001
            return ToolResult(
                ok=False, summary="personal query failed", error="personal_error")
        text = str((out or {}).get("answer") or "").strip()
        if not text:
            return ToolResult(ok=True, items=[], summary="no personal data",
                              note={"kind": "no_match", "provenance": "personal:none"})
        return ToolResult(
            ok=True, items=[{"metadata": {"source": "personal",
                                          "id": "personal", "answer": text}}],
            summary="personal catalog answer",
            note={"kind": "personal", "provenance": "personal", "text": text})


def register_cascade_tools(registry):
    """Register the Stage-3 cascade tools on a ToolRegistry."""
    registry.register(RetrieveFaqTool())
    registry.register(CompareOffersTool())
    registry.register(SuperlativeOfferTool())
    registry.register(CatalogTool())
    return registry