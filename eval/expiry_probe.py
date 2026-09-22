"""Served-path expiry probe: does any freshness gate execute on the real path?

Loads the same singletons the servers use (core.app.get_engine), wraps the
freshness helpers with pure observers (call counters only), and runs
representative served-path calls:
  1. RagEngine.retrieve (the llm-* path) on an offer query
  2. FacetedCatalog.in_price_range / cheapest (faceted + superlative paths)
  3. SearchOffersTool price branch (agent tool path, the _fresh_only caller)
Records whether each freshness helper executed and how many expired items
each path returned. Read-only observers; no behavior change.

Usage:
    venv/Scripts/python eval/expiry_probe.py --out eval/expiry_probe.json
Env:
    AUDIT_NOW=YYYY-MM-DD  (default: today)
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _parse_expiry(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="eval/expiry_probe.json")
    args = ap.parse_args()
    now = datetime.date.fromisoformat(os.environ.get("AUDIT_NOW", "2026-09-21"))

    calls = {"fresh_only": 0, "rec_is_expired": 0}
    out = {"now": str(now), "paths": {}}

    import core.app as appmod
    engine = appmod.get_engine()
    print("engine ready", flush=True)

    import core.faceted as F
    import agent.tools.catalog_tools as ct

    orig_fresh = ct._fresh_only

    def fresh_wrapper(items, reference_date=None):
        calls["fresh_only"] += 1
        return orig_fresh(items, reference_date)

    ct._fresh_only = fresh_wrapper
    orig_rec = F._rec_is_expired

    def rec_wrapper(rec):
        calls["rec_is_expired"] += 1
        return orig_rec(rec)

    F._rec_is_expired = rec_wrapper
    try:
        # 1. retrieve() on an offer query
        cands = engine.retrieve("عايز عروض بيتزا")
        exp = sum(1 for d in cands
                  if (lambda m: (lambda e: e is not None and e < now)(
                      _parse_expiry(m.get("expiry"))))(d.get("metadata", {})))
        out["paths"]["retrieve"] = {
            "freshness_helper_executed": "n/a (no helper; see source)",
            "n_candidates": len(cands),
            "n_expired": exp,
        }

        # 2. faceted price + superlative paths
        fac = engine.faceted
        before = dict(calls)
        in_range = fac.in_price_range(0, 100, reply_lang="en")
        out["paths"]["in_price_range"] = {
            "rec_is_expired_calls": calls["rec_is_expired"] - before["rec_is_expired"],
            "n_items": len(in_range),
            "n_expired": sum(1 for e in in_range
                             if (lambda m: (lambda x: x is not None and x < now)(
                                 _parse_expiry((m.get("metadata", {}) or m).get("expiry"))))(e)),
        }
        before = dict(calls)
        cheap = fac.cheapest(reply_lang="en") or []
        out["paths"]["cheapest"] = {
            "rec_is_expired_calls": calls["rec_is_expired"] - before["rec_is_expired"],
            "n_items": len(cheap),
        }

        # 3. agent tool price branch (the _fresh_only caller)
        from agent.facade import rag_facade
        from agent.tools import ToolRegistry
        from agent.tools.catalog_tools import register_catalog_tools
        reg = register_catalog_tools(ToolRegistry())
        from agent.tools import ToolContext
        before = dict(calls)
        res = reg.call("search_offers", ToolContext(
            facade=rag_facade(engine), reply_lang="en", query="pizza under 100",
            normalized_query="pizza under 100", history=[], recent_offers=[],
            user_id=None), {"query": "pizza", "price_range": [0, 100]})
        items = getattr(res, "items", None) or []
        out["paths"]["tool_price_branch"] = {
            "fresh_only_calls": calls["fresh_only"] - before["fresh_only"],
            "n_items": len(items),
            "ok": getattr(res, "ok", None),
        }
    finally:
        ct._fresh_only = orig_fresh
        F._rec_is_expired = orig_rec

    out["calls"] = calls
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000], flush=True)


if __name__ == "__main__":
    main()
