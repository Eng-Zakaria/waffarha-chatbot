import pickle

with open('data/index/intfloat__multilingual-e5-large/faiss/docs.pkl', 'rb') as f:
    docs = pickle.load(f)

from collections import defaultdict
merchant_offers = defaultdict(list)
for d in docs:
    if d['metadata'].get('source') == 'offer':
        m = d['metadata'].get('merchant')
        if m:
            merchant_offers[m].append(d)

# Find merchants with 3+ offers
with open('merchant_debug.txt', 'w', encoding='utf-8') as out:
    for m, offers in sorted(merchant_offers.items(), key=lambda x: -len(x[1])):
        if len(offers) >= 3:
            out.write(f'{m}: {len(offers)} offers\n')
            for o in offers[:5]:
                meta = o['metadata']
                out.write(f'  ID={meta.get("id")}: {meta.get("title", "")[:60]}... Expiry={meta.get("expiry")} Lang={meta.get("lang")}\n')
            out.write('\n')