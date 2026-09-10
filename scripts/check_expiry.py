"""Check expiration dates of active offers."""
import json
from datetime import datetime, date

with open("data/offers_raw.json", "r", encoding="utf-8") as f:
    offers = json.load(f)

active = [o for o in offers if o.get("offer_status") == "active"]
print(f"Total active offer dicts: {len(active)}")

# Check expiry dates
expired = 0
no_expiry = 0
future = 0
today = date.today()
print(f"Today: {today}")

expiry_dates = []
for o in active:
    exp = o.get("offer_expire_date")
    if not exp:
        no_expiry += 1
        continue
    try:
        exp_date = datetime.strptime(str(exp).split(" ")[0], "%Y-%m-%d").date()
        expiry_dates.append(exp_date)
        if exp_date < today:
            expired += 1
        else:
            future += 1
    except:
        pass

print(f"No expiry date: {no_expiry}")
print(f"Expired: {expired}")
print(f"Future (not expired): {future}")
print(f"Unique active IDs with future expiry: {len(set(o['offer_id'] for o in active if o.get('offer_expire_date')))}")

# Show some expiry date ranges
if expiry_dates:
    print(f"Earliest expiry: {min(expiry_dates)}")
    print(f"Latest expiry: {max(expiry_dates)}")
    
    # Distribution by year-month
    from collections import Counter
    ym = Counter(d.strftime("%Y-%m") for d in expiry_dates)
    print(f"\nExpiry distribution (top 15):")
    for ym_key, count in ym.most_common(15):
        print(f"  {ym_key}: {count}")
