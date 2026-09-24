"""Categorize full-pipeline (RAG) eval failures (Phase 5, reporting only).

Usage:
    venv/Scripts/python eval/categorize_full.py <full.json> <out-prefix>
Writes <out-prefix>_breakdown.txt (ASCII-safe) and <out-prefix>_categories.json.

One category per failed record, first match wins:
  error/empty        -> answer empty (other bucket)
  leak               -> scaffolding_leak present (other bucket)
  language-mismatch  -> Arabic script in query XOR answer
  routing/intent     -> expected_source check failed
  retrieval-quality  -> expected_id check failed (right source, wrong doc)
  generation/wording -> keywords missing and/or forbidden hit (no cards)
  card-rendering     -> retrieval right (id ok) but keywords fail WITH cards
  other              -> anything else (incl. passed=False with all checks true)
"""
import json
import re
import sys

AR_RE = re.compile(r"[\u0600-\u06FF]")
CARD_RE = re.compile("\U0001F3B7")


def categorize(rec):
    ans = rec.get("answer") or ""
    if not ans.strip():
        return "other/error-empty"
    if rec.get("scaffolding_leak"):
        return "other/leak"
    checks = rec.get("checks") or {}
    if rec.get("passed") is False:
        q, a = rec.get("query") or "", ans
        if bool(AR_RE.search(q)) != bool(AR_RE.search(a)):
            return "language-mismatch"
        if checks.get("expected_source") is False:
            return "routing/intent"
        if checks.get("expected_id") is False:
            return "retrieval-quality"
        if checks.get("forbidden_keywords") is False:
            return "generation/wording (forbidden-hit)"
        if checks.get("expected_keywords") is False:
            if CARD_RE.search(a):
                return "card-rendering"
            return "generation/wording"
    return "other/unclassified-fail"


def main():
    src, out_prefix = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    rag = data["rag"]
    recs = rag["records"]
    queries = {}
    try:
        with open("eval/queries.json", encoding="utf-8") as f:
            for q in json.load(f):
                queries[q.get("id")] = q
    except Exception:
        pass
    buckets = {}
    for r in recs:
        if r.get("passed"):
            continue
        cat = categorize(r)
        q = queries.get(r.get("id"), {})
        buckets.setdefault(cat, []).append({
            "id": r.get("id"),
            "category": r.get("category") or q.get("category"),
            "query": (r.get("query") or "")[:120],
            "answer": (r.get("answer") or "")[:200],
            "top": (r.get("top_source"), r.get("top_id")),
            "failed_checks": sorted(k for k, v in (r.get("checks") or {}).items() if v is False),
        })
    total = len(recs)
    n_pass = sum(1 for r in recs if r.get("passed"))
    lines = ["pass=%d/%d" % (n_pass, total), ""]
    for cat in sorted(buckets):
        items = buckets[cat]
        lines.append("== %s: %d" % (cat, len(items)))
        for it in items[:8]:
            lines.append("  - [%s] %s | top=%s fails=%s" % (
                it["id"], it["query"].replace("\n", " "),
                it["top"], ",".join(it["failed_checks"])))
        if len(items) > 8:
            lines.append("  ... +%d more" % (len(items) - 8))
        lines.append("")
    with open(out_prefix + "_breakdown.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(out_prefix + "_categories.json", "w", encoding="utf-8") as f:
        json.dump({"pass": n_pass, "total": total,
                   "counts": {k: len(v) for k, v in buckets.items()},
                   "items": buckets}, f, ensure_ascii=False, indent=1)
    print("pass=%d/%d categories=%s" % (
        n_pass, total, {k: len(v) for k, v in sorted(buckets.items())}))


if __name__ == "__main__":
    main()
