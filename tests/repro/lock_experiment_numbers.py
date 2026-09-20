"""Lock experiment numbers (Ex-D categories + freshness/followup latency) for CF-L.

Reads model_cap.jsonl + model_cap_all.jsonl (banner+models) and
session_probe_out.txt (freshness + followup latency rows), computes the
routing-bucket metrics and prints a compact, report-citable table.
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
TMP = r"C:\Users\devza\AppData\Local\Temp\opencode"

PATH_A = os.path.join(TMP, "model_cap.jsonl")
PATH_B = os.path.join(TMP, "model_cap_all.jsonl")
PROBE = os.path.join(TMP, "session_probe_out.txt")

MODELS = {
    "llama3.2:latest",
    "qwen2.5:3b-instruct",
    "command-r7b-arabic:latest",
}

BUCKET_RETRIEVAL = {"waffarha_discovery", "merchant", "category", "product",
                    "constraint", "reference", "ambiguous"}
RESP_RETRIEVAL = {"OFFER_LOOKUP", "SUPERLATIVE", "MULTI_MERCHANT"}


def normlabel(s):
    return (s or "").strip().strip("\ufeff").replace("\u200f", "").strip()


def main():
    rows = []
    for p in (PATH_A, PATH_B):
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    models = {}
    baseline = None
    for r in rows:
        if r.get("model"):
            models[r["model"]] = r
        if r.get("deterministic_baseline"):
            baseline = r["deterministic_baseline"]

    print("Ontology/format probes saved for:", ", ".join(sorted(models)) or "(none)")

    def bucket_acc(cases):
        ok = tot = 0
        for c in cases or []:
            gold = normlabel(c.get("gold"))
            resp = normlabel(c.get("resp"))
            if not gold:
                tot += 1
                continue
            tot += 1
            gb = gold in BUCKET_RETRIEVAL
            rb = resp in RESP_RETRIEVAL
            if gb == rb:
                ok += 1
        return ok, tot

    def ms_avg(cases, key="ms"):
        v = [c.get(key) or 0 for c in cases or [] if c.get(key)]
        return round(sum(v) / len(v)) if v else None

    print("--- classify (40) routing-bucket acc | latency ms ---")
    for name in sorted(models):
        res = models[name]
        print(f"  {name}: {bucket_acc(res.get('classify'))}  "
              f"avg={ms_avg(res.get('classify'))}ms  "
              f"rows={len(res.get('classify') or [])}")

    if baseline:
        print("  DETERMINISTIC_BASELINE_3735:")
        print(f"    classify {bucket_acc(baseline.get('classify'))}  "
              f"avg={ms_avg(baseline.get('classify'))}ms")
        print(f"    extract {bucket_acc(baseline.get('extract'))}")
        print(f"    interpret_n={len(baseline.get('interpret') or [])} "
              f"reference_n={len(baseline.get('reference') or [])} "
              f"decompose_n={len(baseline.get('decompose') or [])}")

    # freshness / followup latency from session probe
    print("--- Ex-B-support: freshness + followup latency (live servers) ---")
    if os.path.exists(PROBE):
        for line in open(PROBE, encoding="utf-8"):
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                o = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(o, dict):
                continue
            if o.get("engine") in ("rag", "agent") and o.get("step"):
                print(f"  S{o.get('step')} [{o.get('engine')}] "
                      f"ms={o.get('ms')} q={o.get('q','')[:36]!r}")
            cat = o.get("cat")
            if cat and "q" in o:
                print(f"  taxonomy[{cat}] q={o['q'][:30]!r} "
                      f"rag_ms={o.get('rag',{}).get('ms')} "
                      f"agent_ms={o.get('agent',{}).get('ms')}")


if __name__ == "__main__":
    main()