"""Verify Qdrant collection stats."""
import os
import sys
import json
import pickle
from qdrant_client import QdrantClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Inspect meta.json for collection name
meta_path = "data/index/BAAI__bge-m3/qdrant/meta.json"
with open(meta_path) as f:
    meta = json.load(f)
collections = meta["collections"]
name = list(collections.keys())[0]
print(f"Collection name: {name}")
print(f"Vector size: {collections[name]['vectors']['size']}")
print(f"Distance: {collections[name]['vectors']['distance']}")

# Load docs.pkl
with open("data/index/BAAI__bge-m3/qdrant/docs.pkl", "rb") as f:
    docs = pickle.load(f)
print(f"docs.pkl records: {len(docs)}")

# Count by source
from collections import Counter
sources = Counter(d["metadata"]["source"] for d in docs)
print(f"Sources: {dict(sources)}")

# Count merchants represented
merchants = Counter()
for d in docs:
    if d["metadata"]["source"] == "offer":
        m = d["metadata"].get("merchant") or d["metadata"].get("partner")
        if m:
            merchants[m] += 1
print(f"Unique merchants in index: {len(merchants)}")

# Check key merchants
print("\nKey merchants present:")
for target in ["KFC", "McDonald", "Pizza Hut", "Domino", "Burger King", "Costa", "Espressolab", "Sira", "Maro", "Hamam"]:
    matches = [m for m in merchants if target.lower() in m.lower()]
    flag = "FOUND" if matches else "MISSING"
    print(f"  [{flag}] {target}: {matches[:5]}")

# Check qdrant collection point count
client = QdrantClient(path="data/index/BAAI__bge-m3/qdrant")
info = client.get_collection(name)
print(f"\nQdrant points: {info.points_count}")
print(f"Qdrant status: {info.status}")