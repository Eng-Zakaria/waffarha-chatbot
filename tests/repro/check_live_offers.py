"""Show the truly-live offers in ClickHouse (active status + not expired)."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

from core import config

now = datetime.datetime.now()
today = now.strftime("%Y-%m-%d")
client = config.get_clickhouse_client()

sql = f"""
    SELECT o.offer_id, o.mobile_offer_title_en, o.mobile_offer_title_ar,
           o.offer_status, o.offer_expire_date, o.coupon_expire_date,
           o.offer_value, o.actual_value, o.offer_discount,
           p.part_name_en, p.part_name_ar
    FROM main.dim_offers AS o
    LEFT JOIN main.dim_partners AS p ON o.part_id = p.part_id
    WHERE o.deleted_at IS NULL
    AND o.offer_status = 'active'
    AND o.offer_expire_date >= '{today}'
"""
rows = [dict(r) for r in client.query(sql).named_results()]

print(f"Current date: {today}")
print(f"Truly live offers (active + not expired by date): {len(rows)}")
print()

for r in rows:
    title = r.get("mobile_offer_title_en") or r.get("mobile_offer_title_ar") or "?"
    merchant = r.get("part_name_en") or r.get("part_name_ar") or "?"
    exp = r.get("offer_expire_date")
    coupon_exp = r.get("coupon_expire_date")
    print(f"  ID={r['offer_id']:>6} | {merchant[:30]:<30} | {title[:40]:<40}")
    print(f"         expires={exp}  coupon_expires={coupon_exp}  value={r['actual_value']}  discount={r['offer_discount']}%")
    print()

# Also check: how many active-status offers have coupon_expire_date in the future?
sql2 = f"""
    SELECT count() AS cnt
    FROM main.dim_offers
    WHERE deleted_at IS NULL
    AND offer_status = 'active'
    AND offer_expire_date < '{today}'
    AND coupon_expire_date >= '{today}'
"""
rows2 = [dict(r) for r in client.query(sql2).named_results()]
print(f"Active + offer expired BUT coupon still valid: {rows2[0]['cnt']}")
