import json, io
old = {r["id"]: r for r in json.load(
    io.open("reports/verify/20260922_095930_verify_full/full.json",
            encoding="utf-8"))["rag"]["records"]}
import glob
newp = sorted(glob.glob("reports/verify/*_phase5_rerun/full.json"))[-1]
new = {r["id"]: r for r in json.load(io.open(newp, encoding="utf-8"))["rag"]["records"]}
o = []
for i in ["offer_spa_ar", "offer_gym_solo_ar", "typo_yaya_ar",
          "category_education_ar", "price_range_over_1000_ar",
          "offer_nonexistent_starbucks"]:
    a, b = old.get(i, {}), new.get(i, {})
    o.append("### " + i)
    o.append("BEFORE passed=%s top=%s checks=%s" % (
        a.get("passed"), (a.get("top_source"), a.get("top_id")), a.get("checks")))
    o.append("BEFORE ans: " + (a.get("answer") or "")[:220].replace("\n", " | "))
    o.append("AFTER  passed=%s top=%s checks=%s" % (
        b.get("passed"), (b.get("top_source"), b.get("top_id")), b.get("checks")))
    o.append("AFTER  ans: " + (b.get("answer") or "")[:220].replace("\n", " | "))
    o.append("")
io.open("reports/broke_samples.txt", "w", encoding="utf-8").write("\n".join(o))
print("wrote broke_samples")
