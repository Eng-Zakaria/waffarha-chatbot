"""Quick doc count + expiry stats from the qdrant docs.pkl."""
import datetime
import pickle
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.stdout.reconfigure(encoding="utf-8")

docs_path = os.path.join("data", "index", "BAAI__bge-m3", "qdrant", "docs.pkl")
with open(docs_path, "rb") as f:
    docs = pickle.load(f)

now = datetime.datetime.now()
sources = {}
expired = 0
active = 0
no_date = 0
total = len(docs)

for d in docs:
    m = d.get("metadata", {})
    src = m.get("source", "?")
    sources[src] = sources.get(src, 0) + 1
    if src == "offer":
        exp = m.get("expiry") or m.get("valid_until") or m.get("offer_expire_date")
        if exp:
            try:
                exp_dt = datetime.datetime.strptime(str(exp)[:10], "%Y-%m-%d")
                if exp_dt < now:
                    expired += 1
                else:
                    active += 1
            except (ValueError, TypeError):
                no_date += 1
        else:
            no_date += 1

print(f"Total docs in qdrant index: {total}")
print("By source:")
for k, v in sorted(sources.items()):
    print(f"  {k}: {v}")
print(f"\nOffer expiry breakdown:")
print(f"  Expired:  {expired}")
print(f"  Active:   {active}")
print(f"  No date:  {no_date}")
