import json, io, glob
newp = sorted(glob.glob("reports/verify/*_phase5_rerun/full.json"))[-1]
new = {r["id"]: r for r in json.load(io.open(newp, encoding="utf-8"))["rag"]["records"]}
broke = ["category_education_ar", "date_night_ar", "offer_amani_beauty_ar",
         "offer_byoot_bay_ar", "offer_course_it_ar", "offer_dentalboss_price_ar",
         "offer_entertainment_couple_ar", "offer_expiry_check_ar",
         "offer_gym_kora_ar", "offer_gym_solo_ar", "offer_nail_spa_ar",
         "offer_spa_ar", "offer_sport_futsal_ar", "offer_wafflicious_en",
         "offer_zadna_ar", "offer_zero_discount_various_ar",
         "price_range_over_1000_ar", "same_merchant_eastern_nightstay",
         "same_merchant_oasis_dayuse", "stock_check_pharaonic_ar", "typo_yaya_ar"]
REFUSAL_BITS = ["مفيش عندي معلومات", "مفيش عندنا عروض", "support@waffarha.com",
                "no offers", "No offers", "couldn't find", "Couldn't find"]
o = []
for i in broke:
    ans = new.get(i, {}).get("answer") or ""
    kind = "honest-refusal/empty" if any(b in ans for b in REFUSAL_BITS) else "served-other"
    o.append("%s | %s | top=%s" % (
        i, kind, (new.get(i, {}).get("top_source"), new.get(i, {}).get("top_id"))))
io.open("reports/broke_split.txt", "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
