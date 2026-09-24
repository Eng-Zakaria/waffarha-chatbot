import json, io, pickle, datetime
now = datetime.date(2026, 9, 24)
docs = pickle.load(io.open("data/index/BAAI__bge-m3/qdrant/docs.pkl", "rb"))
byid = {}
for d in docs:
    m = d.get("metadata", {})
    byid.setdefault((m.get("source"), str(m.get("id"))), m)
queries = {q["id"]: q for q in json.load(io.open("eval/queries.json", encoding="utf-8"))}
broke = ["category_education_ar", "date_night_ar", "offer_amani_beauty_ar",
         "offer_byoot_bay_ar", "offer_course_it_ar", "offer_dentalboss_price_ar",
         "offer_entertainment_couple_ar", "offer_expiry_check_ar",
         "offer_gym_kora_ar", "offer_gym_solo_ar", "offer_nail_spa_ar",
         "offer_spa_ar", "offer_sport_futsal_ar", "offer_wafflicious_en",
         "offer_zadna_ar", "offer_zero_discount_various_ar",
         "price_range_over_1000_ar", "same_merchant_eastern_nightstay",
         "same_merchant_oasis_dayuse", "stock_check_pharaonic_ar", "typo_yaya_ar"]
o = []
for i in broke:
    q = queries.get(i, {})
    exp = (q.get("expected_source"), q.get("expected_id"))
    m = byid.get((exp[0], str(exp[1])), {})
    ex = (m.get("expiry") or "")[:10]
    try:
        st = "expired" if datetime.date.fromisoformat(ex) < now else "live"
    except ValueError:
        st = "null-or-garbled"
    o.append("%s | expected=%s | doc-expiry=%s %s" % (i, exp, ex, st))
io.open("reports/broke_expiry.txt", "w", encoding="utf-8").write("\n".join(o))
print("\n".join(o))
