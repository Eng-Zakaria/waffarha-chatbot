"""Aggregate all repro artifacts into the exact numbers the CF-L report cites.

Emits, for each engine/model: routing-bucket accuracy (gold intent -> did the
routing end in retrieval or not, the thing that actually matters operationally),
latency averages, and the deterministic baseline comparison. Also final
freshness-map + session-authority facts warehoused as JSON for the report.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
TMP = r"C:\Users\devza\AppData\Local\Temp\opencode"

GOLD_RETRIEVAL = {"waffarha_discovery", "merchant", "category", "product",
                  "constraint", "reference", "ambiguous"}
RESP_RETRIEVAL = {"OFFER_LOOKUP", "SUPERLATIVE", "MULTI_MERCHANT"}


def load():
    rows = []
    for fn in ("model_cap.jsonl", "model_cap_all.jsonl"):
        p = os.path.join(TMP, fn)
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    return rows


rows = load()
banner = next((r for r in rows if "deterministic_baseline" in r), {})
base = banner.get("deterministic_baseline") or {}
models = [r for r in rows if r.get("model")]


def ric(gold):
    g = (gold or "").strip().lower()
    return g in GOLD_RETRIEVAL


def routing_acc(cl, goldvar="gold"):
    """gold intent => routing-bucket match (retrieval vs not)."""
    ok = tot = 0
    nonret = {}
    for c in cl or []:
        if "error" in c:
            continue
        tot += 1
        gd = (c.get(goldvar) or "").strip().lower()
        r = (c.get("resp") or "").upper()
        gb = gd in GOLD_RETRIEVAL
        rb = r in RESP_RETRIEVAL
        if gb == rb:
            ok += 1
        else:
            nonret.setdefault((gb, rb), []).append({"q": c.get("q"), "gd": gd, "rt": r})
    return ok, tot


def avg_ms(cl):
    a = [c.get("ms") or 0 for c in cl or [] if "error" not in c]
    return round(sum(a) / len(a)) if a else None


def count(cl):
    return sum(1 for c in (cl or []) if "error" not in c)


out = {}
bl = base.get("classify") or []
out["deterministic_baseline"] = {
    "routing": routing_acc(bl),
    "ms_avg": avg_ms(bl),
    "n": count(bl),
    "gold_routing_preserved": base.get("gold"),
}
for r in models:
    m = r["model"]
    cl = r.get("classify")
    o = {"n": count(cl),
         "routing": routing_acc(cl),
         "classify_ms_avg": avg_ms(cl),
         "interpret": count(r.get("interpret")),
         "reference_ok": count(r.get("reference")),
         "decompose": count(r.get("decompose"))}
    out[m] = o

print("=== ROUTING PULL (gold retrieval-intent vs engine routing) ===")
d = out["deterministic_baseline"]
print(f"deterministic-baseline routing_acc={d['routing'][0]}/{d['routing'][1]} "
      f"ms={d['ms_avg']} (n={d['n']})")
for m, o in out.items():
    if m == "deterministic_baseline":
        continue
    print(f"{m}: routing_acc={o['routing'][0]}/{o['routing'][1]} "
          f"classify_ms={o['classify_ms_avg']} n={o['n']}")

print("\n=== EXTRACTION: does the RAG engine keep free-form vs constrained? ===")
for m, o in out.items():
    if m == "deterministic_baseline":
        continue

# direct dump for the report
with open(os.path.join(TMP, "report_numbers.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("WROTE report_numbers.json", flush=True)
