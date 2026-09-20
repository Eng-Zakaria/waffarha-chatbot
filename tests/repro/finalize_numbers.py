"""Final aggregation of the two model-capacity artifacts into report-citable numbers.

Reads model_cap.jsonl (banner+deterministic baseline + llama3.2 + qwen2.5:3b) and
model_cap_all.jsonl (banner + command-r7b-arabic) and prints a single JSON blurb
that the report can quote verbatim. Also computes the routing-bucket accuracy
(gold intent => did the right thing happen: retrieval vs no-retrieval), which is
the honest metric here because the engine's enum space (OFFER_LOOKUP, SUPERLATIVE...)
is a DIFFERENT label space than the taxonomy labels (waffarha_discovery, merchant...).
"""
import json
import os
import sys
import collections

sys.stdout.reconfigure(encoding="utf-8")
TMP = r"C:\Users\devza\AppData\Local\Temp\opencode"

PATH_MAIN = os.path.join(TMP, "model_cap.jsonl")
PATH_ALL = os.path.join(TMP, "model_cap_all.jsonl")

GOLD_RETRIEVAL_NEEDED = {
    "waffarha_discovery", "merchant", "category", "product", "constraint",
    "reference", "ambiguous", "derived_constraint", "merchant_multi",
}
ENGINE_RETRIEVAL = {"OFFER_LOOKUP", "SUPERLATIVE", "MULTI_MERCHANT", "OFFER_LOOKUP",
                    "ENTITLEMENT_GATE"}


def load(fn):
    rows = []
    if os.path.exists(fn):
        for line in open(fn, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: S110
                continue
    return rows


def classify_payload(r):
    """Extract the list of per-case classify results from a row (works for
    both banner-style rows with an embedded enum key and model rows)."""
    return r.get("classify") or r.get("classify_intent") or []


def routing_ok(gold, resp):
    """gold is our taxonomy label; resp is engine enum. Both map onto
    retrieval-needed-vs-not. Return (same_bucket, gold_bucket, resp_bucket)."""
    g = (gold or "").strip().lower()
    r = (resp or "").strip().upper()
    gb = g in {x.lower() for x in GOLD_RETRIEVAL_NEEDED}
    rb = r in ENGINE_RETRIEVAL
    return (gb == rb), gb, rb


def summarize(rows):
    """rows: list of json lines from one jsonl (banner first, then models)."""
    banner = rows[0] if rows else {}
    banner_meta = {
        "dates": banner.get("dates"),
        "models_known": (banner.get("models") or [])[:6],
    }
    deterministic = banner.get("deterministic_baseline") or {}
    det_cls = deterministic.get("classify") or []
    det_acc = det_ext = None
    ok = tot = 0
    for c in det_cls:
        if "error" in c:
            continue
        tot += 1
        same, _, _ = routing_ok(c.get("gold"), c.get("resp"))
        if same:
            ok += 1
    det_acc = (ok, tot)
    det_ms = [c.get("ms") or 0 for c in det_cls if "error" not in c]
    models = {}
    for r in rows[1:]:
        name = r.get("model") or r.get("model_tag")
        if not name:
            continue
        cl = classify_payload(r)
        ok2 = tot2 = 0
        for c in cl:
            if "error" in c:
                continue
            tot2 += 1
            same, _, _ = routing_ok(c.get("gold"), c.get("resp"))
            if same:
                ok2 += 1
        models[name] = {
            "routing_acc": "%d/%d" % (ok2, tot2),
            "routing_frac": round(ok2 / max(tot2, 1), 4),
            "cl_rows": len(cl),
            "cl_ms_avg": round(sum(c.get("ms") or 0 for c in cl if "error" not in c) /
                               max(sum(1 for c in cl if "error" not in c), 1), 0),
            "extract_free_rows": len(r.get("extract_free") or []),
            "extract_con_rows": len(r.get("extract_constrained") or []),
            "free_exact": sum(1 for c in (r.get("extract_free") or [])
                              if (c.get("resp") or "") == (c.get("gold") or "")),
            "con_exact": sum(1 for c in (r.get("extract_constrained") or [])
                             if (c.get("resp") or "") == (c.get("gold") or "")),
            "interpret_rows": len(r.get("interpret") or []),
            "reference_rows": len(r.get("reference") or []),
            "decompose_rows": len(r.get("decompose") or []),
        }
    return banner_meta, {"classify": det_acc, "ms_avg": det_ms and
                         round(sum(det_ms) / len(det_ms), 0) or None}, models


main_meta, dt_main, mm_main = summarize(load(PATH_MAIN))
all_meta, dt_all, mm_all = summarize(load(PATH_ALL))
out = {
    "artifact_main": os.path.basename(PATH_MAIN),
    "artifact_all": os.path.basename(PATH_ALL),
    "banner_dates": main_meta["dates"],
    "banner_models": main_meta["models_known"],
    "deterministic_baseline_classify": dt_main["classify"],
    "deterministic_baseline_ms_avg": dt_main["ms_avg"],
    "models_main": mm_main,
    "models_all": mm_all,
}
print(json.dumps(out, ensure_ascii=False, indent=1))
with open(os.path.join(TMP, "report_numbers.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("WROTE report_numbers.json")
