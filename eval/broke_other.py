import json, io, glob
newp = sorted(glob.glob("reports/verify/*_phase5_rerun/full.json"))[-1]
new = {r["id"]: r for r in json.load(io.open(newp, encoding="utf-8"))["rag"]["records"]}
o = []
for i in ["offer_expiry_check_ar", "offer_zadna_ar", "offer_wafflicious_en",
          "offer_dentalboss_price_ar", "same_merchant_eastern_nightstay"]:
    r = new.get(i, {})
    o.append("### " + i + " passed=" + str(r.get("passed")))
    o.append("ANS: " + (r.get("answer") or "")[:300].replace("\n", " | "))
    o.append("")
io.open("reports/broke_served_other.txt", "w", encoding="utf-8").write("\n".join(o))
print("wrote")
