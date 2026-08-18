"""
Builds a vector index over data/faqs.json + data/offers_raw.json.

Same doc-building logic as the original build_index.py -- what's new is that
the index target (FAISS / Chroma / both) and the embedding model are CLI
args, not hardcoded, so you can build multiple combinations for benchmarking
without editing config.py each time:

    python ingest/build_index.py --backend faiss
    python ingest/build_index.py --backend chroma
    python ingest/build_index.py --backend both --embedding-model intfloat/multilingual-e5-small

Each (embedding_model, backend) combination is written to its own directory
under data/index/<embedding_model>/<backend>/, so multiple builds coexist
and eval/run_matrix.py can point RagEngine at any of them.
"""
import argparse
import json
import os
import pickle
import re
import sys

from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
from vectorstores import get_store  # noqa: E402


def load_faqs() -> list:
    path = os.path.join(config.INDEX_DIR, "faqs.json")
    with open(path, "r", encoding="utf-8") as f:
        faqs = json.load(f)

    docs = []
    for faq in faqs:
        docs.append({
            "text": f"Q: {faq['question_en']}\nA: {faq['answer_en']}",
            "metadata": {
                "source": "faq", "id": faq["id"], "category": faq["category"],
                "lang": "en", "question": faq["question_en"], "answer": faq["answer_en"],
            },
        })
        docs.append({
            "text": f"سؤال: {faq['question_ar']}\nإجابة: {faq['answer_ar']}",
            "metadata": {
                "source": "faq", "id": faq["id"], "category": faq["category_ar"],
                "lang": "ar", "question": faq["question_ar"], "answer": faq["answer_ar"],
            },
        })
    return docs


def pick_field(offer: dict, candidates: list, default=""):
    for key in candidates:
        if key in offer and offer[key] not in (None, ""):
            return offer[key]
    return default


# NEW: offer_special_display carries real, varied signal (1652/1652/306/38/8/70
# offers tagged hot_deals/limited_time/ramadan/feast/valentine_offers/
# delivery_section respectively) that was previously discarded at ingest --
# neither stored in metadata nor embedded in the text, so a query like "any
# Ramadan deals?" had nothing to match against beyond generic embedding
# proximity. Translating each tag into a natural-language phrase and adding
# it to the embedded text (see load_offers below) means retrieve()'s existing
# lexical-bonus mechanism picks these up for free -- no changes needed to
# rag_engine.py or any of the tuned thresholds in config.py.
_TAG_PHRASES = {
    "hot_deals": ("Hot deal", "عرض ساخن"),
    "limited_time": ("Limited-time offer", "عرض لفترة محدودة"),
    "ramadan": ("Ramadan offer", "عرض رمضان"),
    "feast": ("Eid / feast offer", "عرض العيد"),
    "valentine_offers": ("Valentine's offer", "عرض فالنتاين"),
    "delivery_section": ("Delivery available", "متاح توصيل"),
}


def _tag_line(special_display: str, lang: str) -> str:
    if not special_display:
        return ""
    seen = []
    for raw in special_display.split(","):
        tag = raw.strip()
        if tag and tag not in seen:
            seen.append(tag)
    phrases = [_TAG_PHRASES[t][0 if lang == "en" else 1] for t in seen if t in _TAG_PHRASES]
    if not phrases:
        return ""
    label = "Tags" if lang == "en" else "الوسوم"
    sep = ", " if lang == "en" else "، "
    return f"{label}: {sep.join(phrases)}"


# NEW: sold_coupons_count (e.g. "18086 sold coupons") is real, varying data --
# unlike remaining_coupons_count, which is "0" for all 1664 records in the
# current feed and therefore not extracted at all. Pulled out as an int so
# rag_engine.py can answer "how many sold" / "is this popular" questions and
# give a real number instead of silence when someone asks about stock and
# there's no remaining-count to give them.
def _extract_sold_count(offer: dict):
    raw = offer.get("sold_coupons_count")
    if not raw:
        return None
    m = re.match(r"\s*(\d+)", str(raw))
    return int(m.group(1)) if m else None


def load_offers() -> list:
    path = os.path.join(config.INDEX_DIR, "offers_raw.json")
    if not os.path.exists(path):
        print("No data/offers_raw.json found yet -- skipping offers (run ingest/fetch_offers.py first).")
        return []

    with open(path, "r", encoding="utf-8") as f:
        offers = json.load(f)

    fc = config.OFFER_FIELD_CANDIDATES
    docs = []
    skipped_inactive = 0
    skipped_no_title = 0

    for offer in offers:
        status = offer.get(config.OFFER_STATUS_FIELD)
        if config.OFFER_ACTIVE_VALUES and status not in config.OFFER_ACTIVE_VALUES:
            skipped_inactive += 1
            continue

        title = pick_field(offer, fc["title"])
        if not title:
            skipped_no_title += 1
            continue

        description = pick_field(offer, fc["description"])
        price = pick_field(offer, fc["price"])
        old_price = pick_field(offer, fc["old_price"])
        discount = pick_field(offer, fc["discount"])
        expiry = pick_field(offer, fc["expiry"])
        # NEW: raw expiry is "YYYY-MM-DD HH:MM:SS" (confirmed consistent across
        # all 1664 records) but the time-of-day is never meaningful here --
        # every offer expires at midnight or some arbitrary batch-job time, not
        # a real cutoff hour a user would care about. Truncated once here, at
        # the metadata source, so the shorter date flows consistently into the
        # embedded text, the fact checklist, and the direct-answer card
        # without needing to touch rag_engine.py at all.
        if expiry:
            expiry = re.split(r"[ T]", str(expiry))[0].strip()
        offer_id = pick_field(offer, fc["id"])
        lang = offer.get("_lang", "en")

        partners = offer.get("partners")
        merchant = partners.get("part_name", "") if isinstance(partners, dict) else ""

        show_old_price = old_price and old_price not in (0, "0") and str(old_price) != str(price)
        show_discount = discount and str(discount) not in ("0", "0.0")

        text_parts = [f"Offer: {title}"]
        if merchant:
            text_parts.append(f"Merchant: {merchant}")
        if description and description != title:
            text_parts.append(f"Description: {description}")
        if price:
            currency = config.CURRENCY.get(lang, config.CURRENCY.get("en", "EGP")) \
                if isinstance(config.CURRENCY, dict) else config.CURRENCY
            price_line = f"Price: {price} {currency}"
            if show_old_price:
                price_line += f" (was {old_price} {currency})"
            if show_discount:
                price_line += f", {discount}% off"
            text_parts.append(price_line)
        if expiry:
            text_parts.append(f"Valid until: {expiry}")

        tag_line = _tag_line(offer.get("offer_special_display", ""), lang)
        if tag_line:
            text_parts.append(tag_line)

        sold_count = _extract_sold_count(offer)

        docs.append({
            "text": "\n".join(text_parts),
            "metadata": {
                "source": "offer", "id": offer_id, "title": title, "merchant": merchant,
                "price": price, "old_price": old_price if show_old_price else None,
                "discount": discount if show_discount else None, "expiry": expiry,
                "lang": lang, "section_id": offer.get("_section_id"),
                "sold_count": sold_count,  # NEW -- see _extract_sold_count
            },
        })

    if skipped_inactive:
        print(f"Skipped {skipped_inactive} offer(s) with status not in {config.OFFER_ACTIVE_VALUES}.")
    if skipped_no_title:
        print(f"Skipped {skipped_no_title} offer(s) with no usable title.")

    return docs


def safe_name(model_name: str) -> str:
    return model_name.replace("/", "__").replace(":", "_")


def index_dir(embedding_model: str, backend: str) -> str:
    d = os.path.join(config.INDEX_DIR, "index", safe_name(embedding_model), backend)
    os.makedirs(d, exist_ok=True)
    return d


def build_for(embedding_model: str, backend: str, docs: list, embeddings):
    print(f"\n=== embedding={embedding_model}  backend={backend} ===")
    d = index_dir(embedding_model, backend)
    docs_path = os.path.join(d, "docs.pkl")
    store_path = os.path.join(d, "index.faiss") if backend == "faiss" else d

    # faiss doesn't take a persist_path (it uses save()/load() with store_path directly).
    # Every OTHER backend needs one: chroma/qdrant/lancedb require it outright (they'll
    # raise ValueError without it, like you just saw for qdrant), and pgvector silently
    # accepts None and falls back to a single shared table name -- which would make
    # every embedding model's pgvector build overwrite the same table instead of getting
    # its own, with no error to warn you.
    store = get_store(backend, persist_path=None if backend == "faiss" else store_path)
    store.build(embeddings, docs)
    if backend == "faiss":
        store.save(store_path)

    with open(docs_path, "wb") as f:
        pickle.dump(docs, f)
    print(f"Saved index to {store_path}")
    print(f"Saved docs to  {docs_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["faiss", "chroma", "qdrant", "lancedb", "pgvector", "all"], default="faiss")
    parser.add_argument("--embedding-model", default=config.EMBEDDING_MODEL,
                         help="Any sentence-transformers model id. Overrides config.EMBEDDING_MODEL.")
    args = parser.parse_args()

    docs = load_faqs() + load_offers()
    if not docs:
        print("No documents to index. Aborting.")
        return

    print(f"Loading embedding model: {args.embedding_model} (first run downloads it) ...")
    model = SentenceTransformer(args.embedding_model, device=config.EMBEDDING_DEVICE)

    # Encode ONCE per embedding model, not once per backend -- build_for() used to
    # call model.encode() itself, so `--backend all` silently re-embedded the whole
    # corpus 5 times (once per faiss/chroma/qdrant/lancedb/pgvector). On CPU that's
    # the single biggest time sink in the whole pipeline for no reason: the same
    # (docs, embeddings) pair is valid for every backend.
    print(f"Encoding {len(docs)} chunks with {args.embedding_model} ...")
    texts = [d["text"] for d in docs]
    embeddings = model.encode(
        texts, batch_size=64, show_progress_bar=True,
        normalize_embeddings=True, convert_to_numpy=True,
    ).astype("float32")

    backends = ["faiss", "chroma", "qdrant", "lancedb", "pgvector"] if args.backend == "all" else [args.backend]
    for backend in backends:
        build_for(args.embedding_model, backend, docs, embeddings)

    n_faqs = sum(1 for d in docs if d["metadata"]["source"] == "faq")
    n_offers = sum(1 for d in docs if d["metadata"]["source"] == "offer")
    print(f"\nDone. {n_faqs} FAQ chunks + {n_offers} offer chunks = {len(docs)} total, per backend built.")


if __name__ == "__main__":
    main()