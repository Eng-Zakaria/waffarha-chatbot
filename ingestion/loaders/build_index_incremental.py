"""
Incremental index builder for Waffarha Chatbot.

This script builds/updates the vector index incrementally by:
1. Comparing current data (offers_raw.json, faqs.json) with previously indexed data
2. Only embedding new or modified documents
3. Removing documents that no longer exist in the source data

This is much faster than rebuilding the entire index from scratch.

Usage:
    python ingest/build_index_incremental.py --backend faiss
    python ingest/build_index_incremental.py --backend chroma --embedding-model intfloat/multilingual-e5-small

The script maintains a manifest file (index_manifest.json) that tracks:
- Document hashes (content + metadata) for change detection
- Document IDs and their position in the index
- Last sync timestamp
"""
import argparse
import hashlib
import json
import os
import pickle
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional

from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core import config
from vectorstores.vectorstores import get_store


MANIFEST_FILENAME = "index_manifest.json"


def safe_name(model_name: str) -> str:
    return model_name.replace("/", "__").replace(":", "_")


def index_dir(embedding_model: str, backend: str) -> str:
    d = os.path.join(config.INDEX_DIR, "index", safe_name(embedding_model), backend)
    os.makedirs(d, exist_ok=True)
    return d


def manifest_path(embedding_model: str, backend: str) -> str:
    d = index_dir(embedding_model, backend)
    return os.path.join(d, MANIFEST_FILENAME)


def load_manifest(embedding_model: str, backend: str) -> Dict:
    """Load the index manifest, or return empty if doesn't exist."""
    path = manifest_path(embedding_model, backend)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "embedding_model": embedding_model,
        "backend": backend,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "documents": {},  # doc_id -> {hash, metadata, index_position}
        "stats": {
            "total_docs": 0,
            "faq_count": 0,
            "offer_count": 0
        }
    }


def save_manifest(embedding_model: str, backend: str, manifest: Dict):
    """Save the index manifest."""
    manifest["updated_at"] = datetime.now().isoformat()
    path = manifest_path(embedding_model, backend)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def compute_doc_hash(doc: Dict) -> str:
    """Compute a hash of the document content + metadata for change detection."""
    # Create a deterministic representation
    content = {
        "text": doc["text"],
        "metadata": {
            k: v for k, v in doc["metadata"].items()
            if k not in ("_indexed_at",)  # Exclude timestamp fields
        }
    }
    content_str = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:16]


# Reuse the doc building logic from build_index.py
def load_faqs() -> List[Dict]:
    path = os.path.join(config.INDEX_DIR, "faqs.json")
    with open(path, "r", encoding="utf-8") as f:
        faqs = json.load(f)

    payment_faqs_path = os.path.join(config.INDEX_DIR, "faqs_payment_methods.json")
    if os.path.exists(payment_faqs_path):
        with open(payment_faqs_path, "r", encoding="utf-8") as f:
            payment_faqs = json.load(f)
        faqs = faqs + payment_faqs

    purchasing_status_faqs_path = os.path.join(config.INDEX_DIR, "faqs_purchasing_status.json")
    if os.path.exists(purchasing_status_faqs_path):
        with open(purchasing_status_faqs_path, "r", encoding="utf-8") as f:
            purchasing_status_faqs = json.load(f)
        faqs = faqs + purchasing_status_faqs

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


def pick_field(offer: Dict, candidates: List[str], default=""):
    for key in candidates:
        if key in offer and offer[key] not in (None, ""):
            return offer[key]
    return default


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


def _extract_sold_count(offer: Dict):
    raw = offer.get("sold_coupons_count")
    if not raw:
        return None
    m = re.match(r"\s*(\d+)", str(raw))
    return int(m.group(1)) if m else None


def _load_type_prices() -> Dict:
    path = os.path.join(config.INDEX_DIR, "type_prices.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def _format_tier_line(tier: Dict, lang: str) -> str:
    name = tier.get(f"name_{lang}") or tier.get("name_en") or tier.get("name_ar") or ""
    price = tier.get("price")
    if not name or price in (None, ""):
        return ""

    currency = config.CURRENCY.get(lang, config.CURRENCY.get("en", "EGP")) \
        if isinstance(config.CURRENCY, dict) else config.CURRENCY

    before = tier.get("price_before_discount")
    discount = tier.get("discount")
    show_before = before not in (None, "", 0, "0") and str(before) != str(price)
    show_discount = discount not in (None, "", 0, "0", "0.0")

    line = f"- {name}: {price} {currency}"
    if show_before or show_discount:
        if lang == "ar":
            extra = []
            if show_before:
                extra.append(f"كانت {before} {currency}")
            if show_discount:
                extra.append(f"خصم {discount}%")
            line += " (" + "، ".join(extra) + ")"
        else:
            extra = []
            if show_before:
                extra.append(f"was {before} {currency}")
            if show_discount:
                extra.append(f"{discount}% off")
            line += " (" + ", ".join(extra) + ")"
    return line


def load_offers() -> List[Dict]:
    path = os.path.join(config.INDEX_DIR, "offers_raw.json")
    if not os.path.exists(path):
        print("No data/offers_raw.json found yet -- skipping offers (run ingest/fetch_offers.py first).")
        return []

    with open(path, "r", encoding="utf-8") as f:
        offers = json.load(f)

    type_prices = _load_type_prices()
    fc = config.OFFER_FIELD_CANDIDATES
    docs = []
    skipped_inactive = 0
    skipped_no_title = 0

    from datetime import datetime

    for offer in offers:
        status = offer.get(config.OFFER_STATUS_FIELD)
        if config.OFFER_ACTIVE_VALUES and status not in config.OFFER_ACTIVE_VALUES:
            skipped_inactive += 1
            continue

        expiry = pick_field(offer, fc["expiry"])
        if expiry:
            try:
                expiry_date = datetime.strptime(str(expiry).split(" ")[0], "%Y-%m-%d").date()
                if expiry_date < datetime.now().date():
                    skipped_inactive += 1
                    continue
            except (ValueError, TypeError):
                pass

        title = pick_field(offer, fc["title"])
        if not title:
            skipped_no_title += 1
            continue

        expiry = pick_field(offer, fc["expiry"])
        if expiry:
            expiry = re.split(r"[ T]", str(expiry))[0].strip()

        if expiry:
            try:
                from datetime import datetime
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                current_date = datetime.now().date()
                if expiry_date < current_date:
                    skipped_inactive += 1
                    continue
            except ValueError:
                pass

        offer_id = pick_field(offer, fc["id"])
        lang = offer.get("_lang", "en")

        partners = offer.get("partners")
        merchant = partners.get("part_name", "") if isinstance(partners, dict) else ""
        part_address = partners.get("address", "") if isinstance(partners, dict) else ""
        part_hours = partners.get("hours", "") if isinstance(partners, dict) else ""
        part_phone = partners.get("phone", "") if isinstance(partners, dict) else ""
        part_facebook = partners.get("facebook", "") if isinstance(partners, dict) else ""
        part_website = partners.get("website", "") if isinstance(partners, dict) else ""

        offer_fineprint_en = offer.get("offer_fineprint_en", "")
        offer_fineprint_ar = offer.get("offer_fineprint_ar", "")
        waffarha_advice_en = offer.get("waffarha_advice_en", "")
        waffarha_advice_ar = offer.get("waffarha_advice_ar", "")

        price = pick_field(offer, fc["price"])
        old_price = pick_field(offer, fc["old_price"])
        discount = pick_field(offer, fc["discount"])
        description = pick_field(offer, fc["description"])

        show_old_price = old_price and old_price not in (0, "0") and str(old_price) != str(price)
        show_discount = discount is not None and str(discount) != "" and str(discount) != "0" and str(discount) != "0.0"

        text_parts = [f"Offer: {title}"]
        if merchant:
            text_parts.append(f"Merchant: {merchant}")
        if part_address:
            label = "Address" if lang == "en" else "العنوان"
            text_parts.append(f"{label}: {part_address}")
        if part_hours:
            label = "Hours" if lang == "en" else "مواعيد العمل"
            text_parts.append(f"{label}: {part_hours}")
        if part_phone:
            label = "Phone" if lang == "en" else "التليفون"
            text_parts.append(f"{label}: {part_phone}")
        if part_facebook:
            text_parts.append(f"Facebook: {part_facebook}")
        if part_website:
            text_parts.append(f"Website: {part_website}")
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

        tiers = type_prices.get(str(offer_id), [])
        if tiers:
            tier_lines = [_format_tier_line(t, lang) for t in tiers]
            tier_lines = [t for t in tier_lines if t]
            if tier_lines:
                label = "Pricing options" if lang == "en" else "خيارات الأسعار"
                text_parts.append(f"{label}:")
                text_parts.extend(tier_lines)

        sold_count = _extract_sold_count(offer)

        fineprint = offer_fineprint_en if lang == "en" else offer_fineprint_ar
        advice = waffarha_advice_en if lang == "en" else waffarha_advice_ar
        if fineprint:
            label = "Terms & Conditions" if lang == "en" else "الشروط والأحكام"
            text_parts.append(f"{label}: {fineprint[:500]}")
        if advice:
            label = "Important Notes" if lang == "en" else "ملاحظات هامة"
            text_parts.append(f"{label}: {advice[:500]}")

        docs.append({
            "text": "\n".join(text_parts),
            "metadata": {
                "source": "offer", "id": offer_id, "title": title, "merchant": merchant,
                "price": price, "old_price": old_price if show_old_price else None,
                "discount": discount if (discount is not None and str(discount).strip() != "") else None, "expiry": expiry,
                "lang": lang, "section_id": offer.get("_section_id"),
                "sold_count": sold_count,
                "part_address": part_address, "part_hours": part_hours,
                "part_phone": part_phone, "part_facebook": part_facebook,
                "part_website": part_website,
                "offer_fineprint_en": offer_fineprint_en, "offer_fineprint_ar": offer_fineprint_ar,
                "waffarha_advice_en": waffarha_advice_en, "waffarha_advice_ar": waffarha_advice_ar,
            },
        })

    if skipped_inactive:
        print(f"Skipped {skipped_inactive} offer(s) with status not in {config.OFFER_ACTIVE_VALUES} or expired.")
    if skipped_no_title:
        print(f"Skipped {skipped_no_title} offer(s) with no usable title.")

    return docs


def build_all_docs() -> List[Dict]:
    """Build all documents from source files."""
    return load_faqs() + load_offers()


def detect_changes(current_docs: List[Dict], manifest: Dict) -> Tuple[List[Dict], List[str], List[Dict]]:
    """
    Compare current documents with manifest.
    Returns: (new_or_modified_docs, deleted_doc_ids, unchanged_docs)
    """
    manifest_docs = manifest.get("documents", {})
    current_by_id = {}

    # Build lookup of current docs by stable ID
    for doc in current_docs:
        source = doc["metadata"]["source"]
        doc_id = doc["metadata"]["id"]
        lang = doc["metadata"]["lang"]
        stable_id = f"{source}:{doc_id}:{lang}"
        current_by_id[stable_id] = doc

    new_or_modified = []
    unchanged = []
    current_ids = set(current_by_id.keys())
    manifest_ids = set(manifest_docs.keys())

    # Find new or modified
    for stable_id, doc in current_by_id.items():
        doc_hash = compute_doc_hash(doc)
        if stable_id not in manifest_docs:
            # New document
            new_or_modified.append(doc)
        elif manifest_docs[stable_id].get("hash") != doc_hash:
            # Modified document
            new_or_modified.append(doc)
        else:
            # Unchanged
            unchanged.append(doc)

    # Find deleted
    deleted_ids = list(manifest_ids - current_ids)

    return new_or_modified, deleted_ids, unchanged


def rebuild_index(embedding_model: str, backend: str, docs: List[Dict], embeddings) -> Dict:
    """Full index rebuild (fallback when incremental isn't possible)."""
    print(f"\n=== Full rebuild: embedding={embedding_model}  backend={backend} ===")
    d = index_dir(embedding_model, backend)
    docs_path = os.path.join(d, "docs.pkl")
    store_path = os.path.join(d, "index.faiss") if backend == "faiss" else d

    store = get_store(backend, persist_path=None if backend == "faiss" else store_path)
    store.build(embeddings, docs)
    if backend == "faiss":
        store.save(store_path)

    # Build BM25 index
    bm25_path = os.path.join(d, "bm25.pkl")
    try:
        from vectorstores.bm25_store import BM25Store
        bm25_store = BM25Store()
        bm25_store.build(docs)
        bm25_store.save(bm25_path)
        print(f"Saved BM25 index to {bm25_path}")
    except Exception as e:
        print(f"Warning: failed to build BM25 index: {e}")

    with open(docs_path, "wb") as f:
        pickle.dump(docs, f)

    # Create new manifest
    manifest = {
        "embedding_model": embedding_model,
        "backend": backend,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "documents": {},
        "stats": {
            "total_docs": len(docs),
            "faq_count": sum(1 for d in docs if d["metadata"]["source"] == "faq"),
            "offer_count": sum(1 for d in docs if d["metadata"]["source"] == "offer")
        }
    }

    for i, doc in enumerate(docs):
        source = doc["metadata"]["source"]
        doc_id = doc["metadata"]["id"]
        lang = doc["metadata"]["lang"]
        stable_id = f"{source}:{doc_id}:{lang}"
        manifest["documents"][stable_id] = {
            "hash": compute_doc_hash(doc),
            "metadata": doc["metadata"],
            "index_position": i
        }

    save_manifest(embedding_model, backend, manifest)
    print(f"Saved index to {store_path}")
    print(f"Saved docs to  {docs_path}")
    print(f"Saved manifest to {manifest_path(embedding_model, backend)}")

    return manifest


def incremental_update(embedding_model: str, backend: str,
                       new_or_modified: List[Dict], deleted_ids: List[str],
                       unchanged_docs: List[Dict], manifest: Dict) -> Dict:
    """Perform incremental update to the index."""
    print(f"\n=== Incremental update: embedding={embedding_model}  backend={backend} ===")
    print(f"  New/modified: {len(new_or_modified)}")
    print(f"  Deleted: {len(deleted_ids)}")
    print(f"  Unchanged: {len(unchanged_docs)}")

    d = index_dir(embedding_model, backend)
    docs_path = os.path.join(d, "docs.pkl")
    store_path = os.path.join(d, "index.faiss") if backend == "faiss" else d

    # Load existing index and docs
    store = get_store(backend, persist_path=None if backend == "faiss" else store_path)
    store.load(store_path)

    with open(docs_path, "rb") as f:
        existing_docs = pickle.load(f)

    # Build new docs list: unchanged + new/modified (re-embedded)
    # We need to re-embed new/modified docs
    all_docs = unchanged_docs + new_or_modified

    # Re-embed only new/modified docs
    if new_or_modified:
        print(f"  Encoding {len(new_or_modified)} new/modified documents...")
        model = SentenceTransformer(embedding_model, device=config.EMBEDDING_DEVICE)
        new_texts = [d["text"] for d in new_or_modified]
        new_embeddings = model.encode(
            new_texts, batch_size=64, show_progress_bar=True,
            normalize_embeddings=True, convert_to_numpy=True,
        ).astype("float32")

        # Add new embeddings to store
        store.add(new_embeddings, new_or_modified)

    # For deleted docs, we need to rebuild (FAISS doesn't support easy deletion)
    # For now, if there are deletions, do a full rebuild
    if deleted_ids:
        print(f"  Deletions detected ({len(deleted_ids)}), doing full rebuild...")
        # Re-encode all docs
        model = SentenceTransformer(embedding_model, device=config.EMBEDDING_DEVICE)
        all_texts = [d["text"] for d in all_docs]
        all_embeddings = model.encode(
            all_texts, batch_size=64, show_progress_bar=True,
            normalize_embeddings=True, convert_to_numpy=True,
        ).astype("float32")
        return rebuild_index(embedding_model, backend, all_docs, all_embeddings)

    # Save updated docs
    with open(docs_path, "wb") as f:
        pickle.dump(all_docs, f)

    # Save store (for FAISS, this saves the index)
    if backend == "faiss":
        store.save(store_path)

    # Update BM25 index
    bm25_path = os.path.join(d, "bm25.pkl")
    try:
        from vectorstores.bm25_store import BM25Store
        bm25_store = BM25Store()
        bm25_store.build(all_docs)
        bm25_store.save(bm25_path)
        print(f"Updated BM25 index")
    except Exception as e:
        print(f"Warning: failed to update BM25 index: {e}")

    # Update manifest
    manifest["documents"] = {}
    for i, doc in enumerate(all_docs):
        source = doc["metadata"]["source"]
        doc_id = doc["metadata"]["id"]
        lang = doc["metadata"]["lang"]
        stable_id = f"{source}:{doc_id}:{lang}"
        manifest["documents"][stable_id] = {
            "hash": compute_doc_hash(doc),
            "metadata": doc["metadata"],
            "index_position": i
        }
    manifest["stats"]["total_docs"] = len(all_docs)
    manifest["stats"]["faq_count"] = sum(1 for d in all_docs if d["metadata"]["source"] == "faq")
    manifest["stats"]["offer_count"] = sum(1 for d in all_docs if d["metadata"]["source"] == "offer")
    save_manifest(embedding_model, backend, manifest)

    print(f"Updated index saved to {store_path}")
    print(f"Updated docs saved to  {docs_path}")
    print(f"Updated manifest saved to {manifest_path(embedding_model, backend)}")

    return manifest


def main():
    parser = argparse.ArgumentParser(description="Incremental vector index builder")
    parser.add_argument("--backend", choices=["faiss", "chroma", "qdrant", "lancedb", "pgvector", "all"], default="faiss")
    parser.add_argument("--embedding-model", default=config.EMBEDDING_MODEL,
                         help="Any sentence-transformers model id. Overrides config.EMBEDDING_MODEL.")
    parser.add_argument("--force-full", action="store_true",
                         help="Force full rebuild instead of incremental update")
    args = parser.parse_args()

    backends = ["faiss", "chroma", "qdrant", "lancedb", "pgvector"] if args.backend == "all" else [args.backend]

    # Load all current documents
    print("Loading current documents from source files...")
    current_docs = build_all_docs()
    if not current_docs:
        print("No documents to index. Aborting.")
        return

    print(f"Loaded {len(current_docs)} total documents")

    for backend in backends:
        manifest = load_manifest(args.embedding_model, backend)

        if args.force_full or not manifest.get("documents"):
            print(f"\nNo existing manifest or --force-full specified, doing full rebuild for {backend}")
            model = SentenceTransformer(args.embedding_model, device=config.EMBEDDING_DEVICE)
            texts = [d["text"] for d in current_docs]
            embeddings = model.encode(
                texts, batch_size=64, show_progress_bar=True,
                normalize_embeddings=True, convert_to_numpy=True,
            ).astype("float32")
            rebuild_index(args.embedding_model, backend, current_docs, embeddings)
        else:
            # Detect changes
            new_or_modified, deleted_ids, unchanged_docs = detect_changes(current_docs, manifest)

            if not new_or_modified and not deleted_ids:
                print(f"\nNo changes detected for {backend}, index is up to date!")
                continue

            if deleted_ids:
                print(f"\nDeletions detected for {backend}, doing full rebuild...")
                model = SentenceTransformer(args.embedding_model, device=config.EMBEDDING_DEVICE)
                texts = [d["text"] for d in current_docs]
                embeddings = model.encode(
                    texts, batch_size=64, show_progress_bar=True,
                    normalize_embeddings=True, convert_to_numpy=True,
                ).astype("float32")
                rebuild_index(args.embedding_model, backend, current_docs, embeddings)
            else:
                incremental_update(args.embedding_model, backend,
                                 new_or_modified, deleted_ids, unchanged_docs, manifest)

    print("\nDone!")


if __name__ == "__main__":
    main()