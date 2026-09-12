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
from core import config
from vectorstores.vectorstores import get_store


def load_faqs() -> list:
    path = os.path.join(config.INDEX_DIR, "faqs.json")
    with open(path, "r", encoding="utf-8") as f:
        faqs = json.load(f)

    # NEW: ingest/fetch_payment_methods_clickhouse.py --write produces this
    # file in the exact same raw record shape as faqs.json (id/question_en/
    # answer_en/category/question_ar/answer_ar/category_ar), sourced from
    # main.dim_payment_methods instead of hand-curated -- so it's simply
    # concatenated in here rather than needing its own loader/doc-building
    # logic. Optional and additive: nothing breaks if this file doesn't
    # exist (e.g. it hasn't been generated yet, or a deploy doesn't use it).
    payment_faqs_path = os.path.join(config.INDEX_DIR, "faqs_payment_methods.json")
    if os.path.exists(payment_faqs_path):
        with open(payment_faqs_path, "r", encoding="utf-8") as f:
            payment_faqs = json.load(f)
        faqs = faqs + payment_faqs
        print(f"Loaded {len(payment_faqs)} payment-method FAQ record(s) from {payment_faqs_path}")

    # NEW: ingest/fetch_purchasing_status_clickhouse.py --write produces this
    # file, same raw record shape and same optional/additive contract as
    # faqs_payment_methods.json above -- sourced from
    # main.dim_purchasing_status, filtered to customer-facing status names
    # only (see that script's module docstring for why the filter is on
    # name shape rather than the status column).
    purchasing_status_faqs_path = os.path.join(config.INDEX_DIR, "faqs_purchasing_status.json")
    if os.path.exists(purchasing_status_faqs_path):
        with open(purchasing_status_faqs_path, "r", encoding="utf-8") as f:
            purchasing_status_faqs = json.load(f)
        faqs = faqs + purchasing_status_faqs
        print(f"Loaded {len(purchasing_status_faqs)} purchasing-status FAQ record(s) from {purchasing_status_faqs_path}")

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


# NEW: ingest/fetch_type_price_clickhouse.py --write produces this file --
# {str(offer_id): [tier_dict, ...]}, already filtered to status=1 (currently
# purchasable) tiers only -- see that script's module docstring for why
# status=1 vs 0 is the right filter. Optional and additive, same pattern as
# faqs_payment_methods.json above: nothing breaks if it doesn't exist yet.
def _load_type_prices() -> dict:
    path = os.path.join(config.INDEX_DIR, "type_prices.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    n_tiers = sum(len(v) for v in data.values())
    print(f"Loaded {n_tiers} pricing tier(s) across {len(data)} offer(s) from {path}")
    return data


# NEW: renders one tier as one line of embedded text. Falls back to the
# other language's name if this language's is missing (same defensive
# pattern as fetch_payment_methods_clickhouse.py's _build_faq_records) so a
# gap in one language's data doesn't just silently drop that tier. Arabic
# uses actual Arabic words for "was"/"off" rather than the mixed English-
# in-Arabic phrasing the base Price: line above it already has (see
# config.py's CURRENCY comment) -- new code doesn't need to repeat that.
def _format_tier_line(tier: dict, lang: str) -> str:
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


def _tier_meta(tiers: list) -> dict:
    """Aggregates a dim_type_price tier list into offer-level metadata:
    min/max purchasable price, tier count, and the LATEST tier expiry date.
    Empty dict when no usable tiers are present. min/max are floats; the
    expiry is a "YYYY-MM-DD" string (ISO dates compare correctly with <, so
    string max() is a safe 'latest' pick).
    """
    if not tiers:
        return {}
    prices = []
    expiries = []
    for t in tiers:
        p = t.get("price")
        if p is not None and p != "" and str(p).strip() != "":
            try:
                pf = float(p)
            except (TypeError, ValueError):
                pf = None
            if pf is not None and pf > 0:
                prices.append(pf)
        e = t.get("expire_date")
        if e:
            s = re.split(r"[ T]", str(e))[0].strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
                expiries.append(s)
    out = {"n_tiers": len(tiers)}
    if prices:
        out["min_price"] = min(prices)
        out["max_price"] = max(prices)
    if expiries:
        out["tier_expiry"] = max(expiries)
    return out


def _effective_expiry(summary: str, tier_meta: dict) -> str:
    """Offer-level 'valid until' date: the later of the summary
    dim_offers.offer_expire_date and the latest tier expiry. The site shows
    the tier-level date when tiers exist (offer 8291: summary 2026-08-31 vs
    tiers valid until 2026-09-30), so the summary alone makes the bot answer
    with a stale cutoff. Taking the later date is also the lenient choice for
    the expired-offer filter -- never drops an offer a tier is still selling.
    Returns the input summary unchanged when there's no tier expiry."""
    tier = (tier_meta or {}).get("tier_expiry")
    if not tier:
        return summary
    if not summary:
        return tier
    return max(str(summary)[:10], str(tier)[:10])


def load_offers() -> list:
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

        offer_id = pick_field(offer, fc["id"])
        lang = offer.get("_lang", "en")

        # NEW: resolve the dim_type_price tiers BEFORE expiry filtering. The
        # offer-level summary date (dim_offers.offer_expire_date) can lag the
        # dates the purchasable tiers actually run until (offer 8291: summary
        # 2026-08-31 vs tiers valid until 2026-09-30 -- the site shows the
        # tier date), so the expired-offer filter AND the displayed "Valid
        # until" must share ONE effective date or the bot answers with a
        # stale cutoff.
        tiers = type_prices.get(str(offer_id), [])
        tier_meta = _tier_meta(tiers)

        expiry = pick_field(offer, fc["expiry"])
        if expiry:
            expiry = re.split(r"[ T]", str(expiry))[0].strip()
        expiry = _effective_expiry(expiry, tier_meta)

        # Check if offer has expired (against the effective date above)
        if expiry and not config.INCLUDE_EXPIRED_OFFERS:
            try:
                from datetime import datetime
                expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
                current_date = datetime.now().date()
                if expiry_date < current_date:
                    skipped_inactive += 1  # Count expired offers as inactive
                    continue
            except ValueError:
                # If date parsing fails, continue processing the offer
                pass

        title = pick_field(offer, fc["title"])
        if not title:
            skipped_no_title += 1
            continue

        partners = offer.get("partners")
        merchant = partners.get("part_name", "") if isinstance(partners, dict) else ""
        # NEW: partner contact/location fields, folded in additively (see
        # fetch_offers_clickhouse.py's partner join) -- only present at all
        # for a currently-active partner, and only per-field when that
        # field is actually populated, so an offer with none of these just
        # renders exactly as before.
        part_address = partners.get("address", "") if isinstance(partners, dict) else ""
        part_hours = partners.get("hours", "") if isinstance(partners, dict) else ""
        part_phone = partners.get("phone", "") if isinstance(partners, dict) else ""
        part_facebook = partners.get("facebook", "") if isinstance(partners, dict) else ""
        part_website = partners.get("website", "") if isinstance(partners, dict) else ""
        
        # NEW: offer fine-print/terms from dim_offers (offer_fineprint_en/ar, waffarha_advice_en/ar)
        # These provide important terms and conditions that users often ask about
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

        # NEW: fold active dim_type_price tiers into the embedded text --
        # see _load_type_prices/_format_tier_line above. Purely additive to
        # the single Price: line: offers with no tier data (the majority --
        # only ~83% of offers with ANY dim_type_price rows, and far fewer
        # than that of all offers, actually have multiple tiers) render
        # exactly as before.
        if tiers:
            tier_lines = [_format_tier_line(t, lang) for t in tiers]
            tier_lines = [t for t in tier_lines if t]
            if tier_lines:
                label = "Pricing options" if lang == "en" else "خيارات الأسعار"
                text_parts.append(f"{label}:")
                text_parts.extend(tier_lines)

        sold_count = _extract_sold_count(offer)

        # NEW: add fine-print/terms to text for embedding
        fineprint = offer_fineprint_en if lang == "en" else offer_fineprint_ar
        advice = waffarha_advice_en if lang == "en" else waffarha_advice_ar
        if fineprint:
            label = "Terms & Conditions" if lang == "en" else "الشروط والأحكام"
            text_parts.append(f"{label}: {fineprint[:500]}")  # truncate for embedding
        if advice:
            label = "Important Notes" if lang == "en" else "ملاحظات هامة"
            text_parts.append(f"{label}: {advice[:500]}")  # truncate for embedding

        docs.append({
            "text": "\n".join(text_parts),
            "metadata": {
                "source": "offer", "id": offer_id, "title": title, "merchant": merchant,
                "price": price, "old_price": old_price if show_old_price else None,
                "discount": discount if (discount is not None and str(discount).strip() != "") else None, "expiry": expiry,
                "lang": lang, "section_id": offer.get("_section_id"),
                "sold_count": sold_count,  # NEW -- see _extract_sold_count
                # NEW -- partner contact/location, see fetch_offers_clickhouse.py.
                # Empty string (not None) when absent, matching how the other
                # optional string fields (merchant, etc.) already behave here.
                "part_address": part_address, "part_hours": part_hours,
                "part_phone": part_phone, "part_facebook": part_facebook,
                "part_website": part_website,
                # NEW -- offer fine-print/terms from dim_offers
                "offer_fineprint_en": offer_fineprint_en, "offer_fineprint_ar": offer_fineprint_ar,
                "waffarha_advice_en": waffarha_advice_en, "waffarha_advice_ar": waffarha_advice_ar,
                # NEW -- aggregated dim_type_price tiers (see _tier_meta above).
                # Lets the answer layer render a multi-tier offer like the site
                # does ("from 95 to 495 EGP, 18 options") instead of one summary
                # price. `tiers` is the raw snapshot list so the card formatter
                # has both language names + per-tier expiry.
                "tiers": tiers, "n_tiers": tier_meta.get("n_tiers", 0),
                "min_price": tier_meta.get("min_price"), "max_price": tier_meta.get("max_price"),
                "tier_expiry": tier_meta.get("tier_expiry"),
            },
        })

    if skipped_inactive:
        print(f"Skipped {skipped_inactive} offer(s) with status not in {config.OFFER_ACTIVE_VALUES} or expired.")
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

    # NEW: build BM25 lexical index for hybrid retrieval (one per embedding model,
    # shared across all backends). Saved as bm25.pkl in the same directory.
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
    print(f"Saved index to {store_path}")
    print(f"Saved docs to  {docs_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["faiss", "chroma", "qdrant", "lancedb", "pgvector", "all"], default="faiss")
    parser.add_argument("--embedding-model", default=config.EMBEDDING_MODEL,
                         help="Any sentence-transformers model id. Overrides config.EMBEDDING_MODEL.")
    parser.add_argument("--batch-size", type=int, default=64,
                         help="Encode batch size. Lower it (e.g. 16) when running on a small GPU.")
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
        texts, batch_size=args.batch_size, show_progress_bar=True,
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