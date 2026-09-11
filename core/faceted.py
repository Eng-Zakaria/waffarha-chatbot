"""
Faceted retrieval layer for the Waffarha Assistant.

Structured-first, semantic-fallback routing over the static RAG index's own
docs (self.docs) -- no live ClickHouse dependency at chat time. This is the
"intent router" layer the five improvement slices converge on:

  slice 1  merchant faceted retrieval   -- bilingual merchant alias table built
                                          from the index's own per-language
                                          merchant names, resolved mention ->
                                          canonical, offers ranked deterministically
                                          by sold_count/discount (payload-filter-
                                          style, over the in-memory offer docs).
  slice 2  category filter retrieval    -- section_id group lexicon ("food",
                                          "beauty", ...) -> category offers.
  slice 3  deterministic aggregation +  -- superlative/price-range answers computed
           negatives                      over the full catalog, plus deterministic
                                          refusals for unknown / offer-less merchants.
  slice 4  product diversity retrieval  -- bilingual product lexicon + per-merchant
                                          cap so "كشري" surfaces several restaurants,
                                          not one merchant's whole catalog.
  slice 5  ordinal/comparison           -- resolved by the engine's existing
                                          follow-up machinery (_extract_ordinals /
                                          _get_comparison_answer); this module only
                                          supplies the deterministically-ranked
                                          merchant/product offer pools they read from.

WHY A PARTNERS SNAPSHOT (identity source): core/faceted.py used to infer the
merchant table by pooling offer-doc "merchant" strings. The authoritative
merchant identity lives in ClickHouse main.dim_partners (part_id, part_name_en,
part_name_ar, status) -- exactly the EN/AR pair + live-status info the faceted
layer needs. index merchant names match part_name_en 100%, so a build-time
snapshot of dim_partners (see ingestion/sources/fetch_partners_clickhouse.py
--snapshot) replaces those heuristics: canonical = partner English name, AR name
and status come from one row, and config.MERCHANT_ALIASES chain through it.
Where the snapshot has no name, the legacy offer-doc fallback still applies.

WHY NOT A DIFFERENT STORE / MODEL (design): the eval failures are retrieval
STRATEGY, not embedding/vector-store quality. Qdrant already stores the exact
payload fields this layer filters on (merchant, section_id, sold_count, price,
discount), dense+BM25 hybrid stays as the semantic fallback, and at 9k offers an
in-process filter is sub-millisecond and keeps the bot fully offline-capable --
so the answer is "same substrate, smarter router", not "replace the substrate".
If server-mode ClickHouse access were ever guaranteed, these same queries could
run as SQL with zero router changes; the in-memory catalog mirrors that shape.
"""

import datetime
import difflib
import logging
import re

from core import config

log = logging.getLogger("waffarha-app")

# section_id -> category label. Mirrors rag_engine._SECTION_CATEGORY so this
# module stays import-safe (rag_engine imports faceted -> importing it back here
# at module load would be a cycle). Keep in sync with rag_engine.
SECTION_CATEGORY = {
    1: "Food & Beverage", 2: "Food & Beverage", 4: "Food & Beverage",
    5: "Shopping & Fashion", 6: "Food & Beverage", 7: "Beauty & Wellness",
    8: "Entertainment & Family", 9: "Travel & Activities",
    10: "Services & Automotive", 11: "Food & Beverage", 12: "Shopping & Fashion",
    15: "Beauty & Wellness", 17: "Entertainment & Family",
    18: "Travel & Activities", 19: "Services & Automotive", 20: "Food & Beverage",
    21: "Food & Beverage", 22: "Beauty & Wellness", 24: "Shopping & Fashion",
    25: "Entertainment & Family", 146: "Food & Beverage", 147: "Food & Beverage",
    148: "Entertainment & Family", 156: "Food & Beverage", 158: "Food & Beverage",
}


def normalize_arabic(text: str) -> str:
    """Collapses Arabic spelling variants (hamza forms -> plain alef, alef
    maksura -> ya). Same normalization build_index/rag_engine rely on."""
    return (text or "").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")


# Store-decorator words that routinely prefix a real merchant name in a query
# but are not themselves merchant-y ("عروض مطعم كشري") -- never treated as names.
_DECORATION_WORDS = {
    "مطعم", "مطاعم", "كافيه", "كوفي", "فرع", "فروع", "عروض", "خصم", "خصومات",
    "كوبون", "كوبونات", "من", "عند", "عندكم", "عندك", "عندهم", "ل", "لـ", "في",
    "of", "from", "at", "by", "for", "the", "and", "offers", "deals", "coupons",
    "restaurant", "cafe", "branch", "offer", "deal", "coupon", "عايز", "عاوز",
    "عايزة", "عندي", "فين", "حابب", "حاب", "طب", "بس", "كده", "دلوقتي",
    "فيه", "في", "ايه", "إيه", "من", "عند", "عندكم", "المتاحة", "متاحة",
    "الحالية", "الجديدة", "المختلفة", "المتوفرة", "الاونلاين", "اونلاين",
    "أونلاين", "والمتاحة", "what", "which", "where", "when", "why", "who",
    "how", "do", "does", "did", "is", "are", "can", "have", "has", "dose",
    "you", "youre", "your", "there", "any", "some", "دلوقت", "مميزة",
    # ---- generic / colloquial filler words the unknown-merchant introducer
    # regex can swallow but are never a brand name ("عروض حلوة دلوقتي",
    # "عرض مستعجل", "العرض المناسب", "العرض ده لسه شغال", "إقامة ليلية")
    "حلوة", "مستعجل", "عاجل", "مناسب", "المناسب", "مناسبه", "مناسبة", "بالضبط", "ده", "دة",
    "لسه", "لسة", "شغال", "ساري", "قبل", "إقامة", "ليلة", "ليله", "ليلية",
    "نهارية", "نهاري", "يا", "معلم", "باشا", "غير",
    # imperative verbs ("هات عروض كفتة" = "bring me koshary deals") are
    # commands, never brand names -- otherwise "هات" was flagged unknown.
    "هات", "هاتي", "جيب", "جيبي", "وريني", "اديني", "اديني", "ديني", "عيني",
    # English request verbs / softeners the introducer regex can swallow
    # ("Show me offers please", "give me some deals"). Never brand names.
    "show", "me", "give", "tell", "need", "want", "looking", "find", "please",
    "us", "any", "appreciate", "now",
}

_DECORATION_NORM = {normalize_arabic(w) for w in _DECORATION_WORDS}


def _clean_name(raw: str) -> str:
    return re.sub(r"[؟?!.,،;:\s]+$", "", (raw or "").strip())


# ---------------------------------------------------------------------------
# Bilingual product lexicon: product key -> term variants (Arabic / English /
# Franco). Probably the largest single slice-4 asset. Order of variants matters
# only for length-based resolution (longest matching variant wins).
# ---------------------------------------------------------------------------
PRODUCT_LEXICON = {
    "koshary": ("كشري", "كشري طبق", "koshary", "koshari", "koshery", "kushari"),
    "pizza": ("بيتزا", "بيتز", "pizza", "بتزا"),
    "shawerma": ("شاورما", "شوارما", "shawerma", "shawarma", "شاورمة"),
    "burger": ("برجر", "برغر", "burger", "همبرجر", "هامبرجر", "hamburger", "بيف برجر"),
    "sushi": ("سوشي", "سوش", "sushi"),
    "tagen": ("طاجن", "تاجن", "طواجن", "tagen", "tawagen"),
    "grill": ("مشويات", "مشاوي", "مشوي", "مشوية", "grill", "grilled", "شواية", "شوي"),
    "pasta": ("باستا", "معكرونة", "مكرونة", "pasta", "سباغيتي", "spaghetti", "ماكروني"),
    "hawawshi": ("حواوشي", "هواشي", "hawawshi", "حواوش"),
    "kofta": ("كفتة", "كفته", "kofta", "كباب", "kebab", "كبدة", "kabda", "ايسكندرية برجر"),
    "fish": ("سمك", "مأكولات بحرية", "مأكولات بحريه", "seafood", "جمبري", "شرمب", "shrimp", "ربيان", "سلطعون"),
    "chicken": ("دجاج", "فراخ", "فروج", "بروست", "broast", "chicken", "فرايد تشيكن", "تشيكن"),
    "beef": ("لحم", "ستيك", "steak", "بيف", "beef", "انتراكوت", "لحم ضاني"),
    "breakfast": ("فطار", "فطور", "breakfast", "بيض", "eggs", "عسل", "حلاوة طحينية", "طعمية", "فول"),
    "dessert": ("حلويات", "حلوى", "حلا", "جاتوه", "جاتو", "كيك", "cake", "dessert", "كنافة", "konafa", "بقلاوة", "بسبوسة", "سندوتشات"),
    "icecream": ("ايس كريم", "أيس كريم", "آيس كريم", "ice cream", "جيلاتي", "gelato", "شفا", "ميلك شيك", "milkshake"),
    "juice": ("عصير", "عصاير", "عصائر", "juice", "سموثي", "smoothie", "موهيتو", "مشروبات باردة"),
    "coffee": ("قهوة", "قهوه", "coffee", "اسبريسو", "espresso", "كابتشينو", "cappuccino", "لاتيه", "latte", "نسكافيه", "ميلك شيك القهوة"),
    "tea": ("شاي", "tea", "نعناع", "شاي اخضر", "شاي أسود"),
    "noodle": ("نودلز", "نودل", "noodles", "نودالز", "اندومي", "شعرية"),
    "sandwich": ("سندوتش", "ساندوتش", "ساندويتش", "sandwich", "فول", "طعمية", "كبدة", "سجق"),
    "salad": ("سلطة", "سلاطة", "salad", "تابولي", "تبولة"),
    "oriental": ("مشاوي مشرقية", "مشرقات", "منقوشة", "فطاير", "فطائر", "فتة", "مليحية", "saj"),
    "sweets": ("حلا", "حلوي", "شيكولاتة", "شوكلاتة", "chocolate", "تويكس", "كندر"),
    "international": ("مأكولات عالمية", "ايطالي", "italian", "فرنساوي", "french", "بريطاني", "برازيلي", "مكسيكي", "indian", "هندي", "تايلاندي", "Japanese", "ياباني"),
}


# ---------------------------------------------------------------------------
# Bilingual category lexicon (generic category words, NOT specific products).
# Resolves "عروض أكل", "food offers", "قهوة" (as a category) etc. to a
# section_id group. Product-specific words live in PRODUCT_LEXICON instead.
# ---------------------------------------------------------------------------
CATEGORY_LEXICON = {
    "food": (
        "أكل", "اكل", "طعام", "فود", "مطعم", "مطاعم", "مأكولات", "سناكس",
        "food", "restaurant", "restaurants", "dining", "meal", "meals", "eats",
        "قائمة", "menu", "قهوة", "قهوه", "coffee", "كافيه", "cafe", "مشروبات",
        "drinks", "drink",
    ),
    "shopping": (
        "تسوق", "شوبنج", "فاشون", "موضة", "ازياء", "الأزياء", "ملابس",
        "shopping", "fashion", "clothes", "clothing",
        "هدايا", "جيفت", "gift", "gifts", "gifting",
    ),
    "beauty": (
        "جمال", "تجميل", "spa", "ماساج", "massage", "قص شعر",
        "حلاقة", "beauty", "salon", "عناية", "ميك اب", "makeup",
        "بشرة", "جلد", "جلده", "skin", "بتانا",
    ),
    "entertainment": (
        "ترفيه", "تسليه", "تسلية", "سنيما", "سينما", "cinema", "فيلم", "بوينج",
        "بولينج", "bowling", "لعبة", "العاب", "games", "كيد", "زونة", "entertainment",
        "kids", "منتزه", "مدينة ملاهي", "amusement",
    ),
    "travel": (
        "سفر", "سافاري", "سفاري", "safari", "رحلات", "رحلة", "travel", "trip",
        "تخييم", "camping", "هليكوبتر", "helicopter", "قارب", "boat", "يخت",
        "جت سكي", "jet ski",
        "فنادق", "فندق", "hotel", "hotels", "إقامة", "استراحة", "lodging",
    ),
    "services": (
        "سيارات", "auto", "car", "cars", "services", "خدمات", "تليفونات",
        "فودافون", "موبايل", "mobile", "توصيل", "delivery", "غسيل",
        "car wash", "مكافحة", "حشرات", "تكييف", "air condition",
    ),
}

# category key -> display label (matches _section_category labels).
CATEGORY_LABEL = {
    "food": "Food & Beverage",
    "shopping": "Shopping & Fashion",
    "beauty": "Beauty & Wellness",
    "entertainment": "Entertainment & Family",
    "travel": "Travel & Activities",
    "services": "Services & Automotive",
}


def _category_of(section_id) -> str:
    return SECTION_CATEGORY.get(section_id, "Food & Beverage")


class FacetedCatalog:
    """In-process structured catalog over the RAG index's offer docs.

    Built once at engine load from the SAME docs the embeddings were built from
    (self.docs), so merchant anonymity/prices/availability always match what
    semantic retrieval could return -- no drift from a separately-fetched table.

    slice 1:  merchants        FacetedCatalog.resolve_merchants + offers_for_merchant
    slice 2:  categories       FacetedCatalog.resolve_category + offers_for_category
    slice 3:  aggregation      FacetedCatalog.cheapest / most_expensive / highest_discount
                               + in_price_range + unknown_merchant_mention
    slice 4:  products         FacetedCatalog.resolve_product + offers_for_product
    """

    def __init__(self, docs: list, aliases: dict = None, offer_intent_words=None,
                 faq_guard_words=None, partners: list = None):
        self._aliases = {str(k).lower(): v for k, v in (aliases or {}).items()}
        self._offer_intent_words = offer_intent_words or set()
        self._faq_guard_words = faq_guard_words or set()
        # canonical -> {"part_id", "status"} from the dim_partners snapshot.
        self._partner_part_id = {}
        self._partner_status = {}

        # --- build offer pool (dedup by offer id, keep both langs) ----------
        # keyed by (offer_id -> {"id", "merchant", "sections": set, "sold": int,
        # "discount": float|None, "price": float|None, "en": doc|None, "ar": doc|None,
        # "combined_text": str})
        self.offers = {}
        en_merchant_by_id = {}
        ar_merchant_by_id = {}
        for doc in docs:
            meta = doc.get("metadata", {})
            if meta.get("source") != "offer":
                continue
            oid = meta.get("id")
            if oid is None:
                continue
            lang = meta.get("lang", "en")
            merchant = (meta.get("merchant") or "").strip()
            rec = self.offers.setdefault(oid, {
                "id": oid, "merchant": merchant, "en": None, "ar": None,
                "sold": _as_int(meta.get("sold_count")),
                "discount": _as_float(meta.get("discount")),
                "price": _as_float(meta.get("price")),
                "expiry": meta.get("expiry"),
                "sections": {meta.get("section_id")},
                "text": "",
            })
            if lang == "ar":
                rec["ar"] = doc
                ar_merchant_by_id[oid] = merchant
            else:
                rec["en"] = doc
                en_merchant_by_id[oid] = merchant
            rec["text"] += " " + (doc.get("text") or "")
        self.offers = {oid: r for oid, r in self.offers.items() if r["en"] or r["ar"]}

        # Pair English/Arabic merchant names per offer id -> alias groups.
        # A merchant's canonical form is its English name when present.
        # With a `partners` snapshot (dim_partners build-time dump) the
        # identity table comes from there -- authoritative EN/AR pair + live
        # status -- otherwise (or for names the snapshot doesn't cover) it is
        # reconstructed from the offer docs as before.
        name_to_canonical = {}
        canonical_info = {}  # canonical -> {"en","ar","aliases": set, "sections": set}
        by_partner = {p.get("part_id"): p for p in (partners or [])} if partners else None
        if by_partner is not None:
            skipped = 0
            for p in by_partner.values():
                en = (p.get("name_en") or "").strip()
                ar = (p.get("name_ar") or "").strip()
                if not en and not ar:
                    skipped += 1
                    continue
                canonical = en or ar
                info = canonical_info.setdefault(canonical, {"aliases": set(), "sections": set()})
                if not info.get("en") and en:
                    info["en"] = en
                if not info.get("ar") and ar:
                    info["ar"] = ar
                if ar:
                    info["aliases"].add(ar)
                if en:
                    info["aliases"].add(en)
                status = (p.get("status") or "unknown").strip() or "unknown"
                info["part_id"] = p.get("part_id")
                info["status"] = status
                if ar:
                    name_to_canonical[ar] = canonical
                if en:
                    name_to_canonical[en] = canonical
                self._partner_part_id[canonical] = p.get("part_id")
                self._partner_status[canonical] = status
            if skipped:
                log.warning("partners snapshot: %d row(s) without any name skipped", skipped)

        def _register(canonical, en_name, ar_name):
            info = canonical_info.setdefault(canonical, {"aliases": set(), "sections": set()})
            if not info.get("en") and en_name:
                info["en"] = en_name
            if not info.get("ar") and ar_name:
                info["ar"] = ar_name
            info["aliases"].update(n for n in (en_name, ar_name) if n)
            if ar_name:
                name_to_canonical[ar_name] = canonical
            if en_name:
                name_to_canonical[en_name] = canonical

        def _partner_canon(name):
            """Resolve a merchant token against the partners table only (used
            for offer>partner linking when a snapshot is present)."""
            if not name or by_partner is None:
                return None
            low = name.lower()
            if low in name_to_canonical:
                c = name_to_canonical[low]
                return c if c in self._partner_part_id else None
            norm = normalize_arabic(low)
            for k in name_to_canonical:
                if normalize_arabic(k.lower()) == norm:
                    c = name_to_canonical[k]
                    return c if c in self._partner_part_id else None
            return None

        # First pass: group ids by (en_name, ar_name) pair.
        unmatched_ids = {oid for oid, r in self.offers.items()}
        for oid, rec in self.offers.items():
            en_nm = (en_merchant_by_id.get(oid) or rec["merchant"] or "").strip()
            ar_nm = (ar_merchant_by_id.get(oid) or rec["merchant"] or "").strip()
            canonical = _partner_canon(en_nm) or _partner_canon(ar_nm) or (en_nm or ar_nm)
            if not canonical:
                unmatched_ids.discard(oid)
                continue
            if canonical not in self._partner_part_id:
                _register(canonical, en_nm, ar_nm)
            else:
                # partner-backed canonical: keep partner's EN/AR names official,
                # but the offer's own doc names are still useful as aliases.
                info = canonical_info.setdefault(canonical, {"aliases": set(), "sections": set()})
                info["aliases"].update(n for n in (en_nm, ar_nm) if n)
                if ar_nm:
                    name_to_canonical.setdefault(ar_nm, canonical)
                if en_nm:
                    name_to_canonical.setdefault(en_nm, canonical)
            rec["merchant_canonical"] = canonical
            unmatched_ids.discard(oid)

        for oid in unmatched_ids:
            rec = self.offers[oid]
            nm = (rec["merchant"] or "").strip()
            if nm:
                rec["merchant_canonical"] = nm
                _register(nm, nm, nm)

        # Alias map from config: alias -> canonical. Alias VALUES are chained
        # through the name map too ("starbucks" -> "Starbucks", where the real
        # canonical built from the docs is the same pair) and, when a value
        # names a merchant with NO offers in the index, kept as a "phantom"
        # canonical so offer-less merchants still resolve AND produce a
        # deterministic negative answer instead of a fabricated offer.
        self._merchant_names = set()
        for canonical, info in canonical_info.items():
            self._merchant_names.update(info["aliases"])

        def _canonical_value(value):
            if value in name_to_canonical:
                return name_to_canonical[value]
            for k in name_to_canonical:
                if k.lower() == value.lower():
                    return name_to_canonical[k]
            return value

        for alias, value in self._aliases.items():
            canon = _canonical_value(value)
            name_to_canonical[alias] = canon
            info = canonical_info.setdefault(canon, {"aliases": set(), "sections": set()})
            info["aliases"].add(alias)
            self._merchant_names.add(alias)

        self._name_to_canonical = name_to_canonical
        self._canonical_info = canonical_info
        self.merchants = set(canonical_info.keys())

        # canonical -> list of offer recs (sorted lazily).
        by_merchant = {}
        for oid, rec in self.offers.items():
            by_merchant.setdefault(rec.get("merchant_canonical"), []).append((oid, rec))
        self._merchant_offers = {
            m: sorted(recs, key=lambda t: (t[1]["sold"], t[1]["discount"] or 0), reverse=True)
            for m, recs in by_merchant.items()
        }

        # category (label) -> sorted offer recs.
        by_category = {}
        for oid, rec in self.offers.items():
            sid = next((s for s in (rec.get("sections") or {}) if s is not None), None)
            base = _category_of(sid) if sid is not None else "Food & Beverage"
            by_category.setdefault(base, []).append((oid, rec))
        self._category_offers = {
            c: sorted(recs, key=lambda t: (t[1]["sold"], t[1]["discount"] or 0), reverse=True)
            for c, recs in by_category.items()
        }

        # product -> [offer rec] inverted index (scanned at load over the
        # combined per-lang text; ~18k docs x products, one-time).
        self._product_offers = {}
        for key, variants in PRODUCT_LEXICON.items():
            low = [v.lower() for v in variants]
            hits = []
            for oid, rec in self.offers.items():
                t = rec["text"].lower()
                if any(v in t for v in low):
                    hits.append((oid, rec))
            self._product_offers[key] = sorted(
                hits, key=lambda t: (t[1]["sold"], t[1]["discount"] or 0), reverse=True
            )
        self._product_variants_low = {
            key: [v.lower() for v in variants]
            for key, variants in PRODUCT_LEXICON.items()
        }
        self._category_variants_low = {
            key: [v.lower() for v in variants]
            for key, variants in CATEGORY_LEXICON.items()
        }

        log.info(
            "FacetedCatalog ready: %d offers, %d merchants, %d categories, %d products",
            len(self.offers), len(self.merchants),
            len(self._category_offers), len(self._product_offers),
        )

    # -- helpers ------------------------------------------------------------

    def has_offer_intent(self, query: str) -> bool:
        q = (query or "").lower()
        return any(w in q for w in self._offer_intent_words)

    def has_faq_guard(self, query: str) -> bool:
        q = (query or "").lower()
        return any(w in q for w in self._faq_guard_words)

    def display_name(self, canonical: str, lang: str = "en") -> str:
        canonical = self._canonical(canonical)
        info = self._canonical_info.get(canonical) or {}
        if lang == "ar":
            return info.get("ar") or info.get("en") or canonical
        return info.get("en") or info.get("ar") or canonical

    def is_live(self, canonical: str):
        """True/False when the canonical came from the dim_partners snapshot
        (True = its status is in config.PARTNERS_STATUS_LIVE), None otherwise
        (offer-doc-only merchant or phantom alias)."""
        canonical = self._canonical(canonical)
        status = self._partner_status.get(canonical)
        if status is None:
            return None
        return status in (getattr(config, "PARTNERS_STATUS_LIVE", None) or {"active"})

    def _canonical(self, name: str) -> str:
        """Normalizes any known merchant name/alias to its canonical key."""
        if not name:
            return name or ""
        low = name.lower()
        if low in self._name_to_canonical:
            return self._name_to_canonical[low]
        norm = normalize_arabic(low)
        for k in self._name_to_canonical:
            if normalize_arabic(k.lower()) == norm:
                return self._name_to_canonical[k]
        return name

    def _resolve_one(self, name: str):
        """Resolve a single (cleaned) name token to a canonical merchant or None."""
        if not name or len(name) < 3:
            return None
        n = _clean_name(name)
        low = n.lower()
        if not low or low in _DECORATION_WORDS:
            return None
        # exact known name / alias map
        if low in self._name_to_canonical:
            return self._name_to_canonical[low]
        if low in self._aliases:
            return self._canonical(self._aliases[low]) or self._aliases[low]
        # normalized Arabic substring match against known names
        norm = normalize_arabic(low)
        for known in sorted(self._merchant_names, key=len, reverse=True):
            if len(known) < 4:
                continue
            kn = normalize_arabic(known.lower())
            if norm == kn or (len(norm) >= 4 and (norm in kn or kn in norm)):
                return self._canonical(known) or known
            # word-boundary latin
            if re.search(rf"(?:\b|^){re.escape(kn)}(?:\b|$)", norm):
                return self._canonical(known) or known
        # typos (conservative): token looks like a single-word brand
        close = difflib.get_close_matches(low, [n.lower() for n in self._merchant_names],
                                          n=1, cutoff=getattr(config, "MERCHANT_FUZZY_MATCH_CUTOFF", 0.8))
        if close:
            return self._canonical(close[0]) or close[0]
        return None

    def resolve_merchants(self, query: str) -> list:
        """Returns canonical merchant names mentioned in the query (dedup,
        in mention order). Combines exact/alias/substring/fuzzy resolution
        over the bilingual alias table."""
        q = (query or "").strip()
        if not q:
            return []
        found = []
        matched_spans = []  # (start, end) in `low`/`norm_q` space, for containment pruning
        seen = set()
        low = q.lower()

        # 1) alias keys (config aliases) -- cheap, high precision
        for alias in sorted(self._aliases.keys(), key=len, reverse=True):
            if len(alias) >= 3 and re.search(rf"(?:\b|^){re.escape(alias)}(?:\b|$)", low):
                canonical = self._canonical(self._aliases[alias]) or self._aliases[alias]
                if canonical not in seen:
                    seen.add(canonical)
                    span = (low.index(alias), low.index(alias) + len(alias))
                    matched_spans.append((canonical, span))
                    found.append(canonical)

        # 2) known names (longest-first substring / boundary)
        candidates = []
        for name in self._merchant_names:
            if len(name) < 4:
                continue
            candidates.append((len(name), normalize_arabic(name.lower())))
        candidates.sort(reverse=True)
        norm_q = normalize_arabic(low)
        for ln, kn in candidates:
            if ln < 4:
                continue
            if kn in _DECORATION_NORM:
                continue
            if kn in norm_q:
                canonical = self._canonical(self._name_to_canonical.get(kn) or kn)
                if canonical in self.merchants and canonical not in seen:
                    seen.add(canonical)
                    p = norm_q.index(kn)
                    matched_spans.append((canonical, (p, p + len(kn))))
                    found.append(canonical)

        # 3) prune: a merchant matched inside the text span of a LONGER
        # matching merchant ("عايز عروض بابا جونز" also matches plain merchant
        # "بابا") is a sub-token false positive -- drop it, never answer two
        # headers for one brand.
        span_map = {c: s for c, s in matched_spans}
        pruned = []
        for c in found:
            s, e = span_map[c]
            if any(other_c != c and (os <= s and e <= oe and (oe - os) > (e - s))
                   for other_c, (os, oe) in span_map.items()):
                continue
            pruned.append(c)

        return pruned

    def resolve_category(self, query: str) -> str | None:
        """Resolves a generic category mention ("food offers", "عروض أكل") to
        a category label, or None. Product mentions are intentionally ignored
        here (they belong to resolve_product)."""
        low = normalize_arabic((query or "").lower())
        best = None
        best_len = 0
        for key, variants in self._category_variants_low.items():
            for v in variants:
                if v in low and len(v) > best_len:
                    best, best_len = key, len(v)
        return best

    def resolve_product(self, query: str) -> str | None:
        """Resolves a product mention ("كشري", "hawawshi", "بيتزا") to a
        product key, or None. Longest variant wins."""
        low = normalize_arabic((query or "").lower())
        best = None
        best_len = 0
        for key, variants in self._product_variants_low.items():
            for v in variants:
                n = normalize_arabic(v)
                if n and n in low and len(n) > best_len:
                    best, best_len = key, len(n)
        return best

    # -- offer accessors ----------------------------------------------------

    def _recs_to_entries(self, recs, reply_lang: str, limit: int,
                         exclude_ids=None, price_filter=None) -> list:
        """Converts (oid, rec) pairs into engine-shaped {"metadata": doc} entries
        in rank order, picking the metadata that matches reply_lang."""
        out = []
        for oid, rec in recs:
            if limit is not None and len(out) >= limit:
                break
            if exclude_ids and oid in exclude_ids:
                continue
            if price_filter:
                lo, hi = price_filter
                p = rec["price"]
                if p is None or not (lo <= p <= hi):
                    continue
            doc = rec.get(reply_lang) or rec.get("en") or rec.get("ar")
            if doc is None:
                continue
            out.append({"metadata": doc.get("metadata", {}), "_merchant": rec.get("merchant_canonical")})
        return out

    def offers_for_merchant(self, canonical: str, reply_lang: str = "en",
                            limit: int = 6, exclude_ids=None, price_filter=None) -> list:
        canonical = self._canonical(canonical)
        recs = self._merchant_offers.get(canonical, [])
        return self._recs_to_entries(recs, reply_lang, limit, exclude_ids, price_filter)

    def offers_for_category(self, category: str, reply_lang: str = "en",
                            limit: int = 6, price_filter=None) -> list:
        label = CATEGORY_LABEL.get(category, category)
        recs = self._category_offers.get(label, [])
        return self._recs_to_entries(recs, reply_lang, limit, None, price_filter)

    def offers_for_product(self, product: str, reply_lang: str = "en",
                           limit: int = 6, top_per_merchant: int = 2,
                           exclude_ids=None, price_filter=None) -> list:
        """Product offers with a per-merchant diversity cap: each merchant
        contributes at most `top_per_merchant` recs, then the merged pool is
        re-ranked by sold_count/discount so the reply shows several DIFFERENT
        restaurants rather than one merchant's whole catalog."""
        pool = self._product_offers.get(product, [])
        per_merchant = {}
        order = []
        for oid, rec in pool:
            m = rec.get("merchant_canonical") or ""
            if m not in per_merchant:
                per_merchant[m] = []
                order.append(m)
            if len(per_merchant[m]) < top_per_merchant:
                per_merchant[m].append((oid, rec))
        merged = []
        for m in order:
            merged.extend(per_merchant[m])
        merged.sort(key=lambda t: (t[1]["sold"], t[1]["discount"] or 0), reverse=True)
        return self._recs_to_entries(merged, reply_lang, limit, exclude_ids, price_filter)

    # -- deterministic aggregation (slice 3) --------------------------------

    def _superlative_pool(self, value_fn, merchant=None):
        """[(value, oid, rec)] over the offer pool, optionally scoped to one
        canonical merchant. value_fn maps a rec -> comparable number or None."""
        canon = self._canonical(merchant) if merchant else None
        out = []
        for oid, rec in self.offers.items():
            if canon and rec.get("merchant_canonical") != canon:
                continue
            v = value_fn(rec)
            if v is not None:
                out.append((v, oid, rec))
        return out

    def cheapest(self, reply_lang: str = "en", merchant: str = None):
        # NOTE: expired/0-price offers are intentionally NOT filtered out --
        # catalog offers are shown as-is, whatever their expiry says. Only
        # the optional merchant scope limits the pool.
        pool = self._superlative_pool(lambda r: r["price"], merchant)
        if not pool:
            return None
        _, oid, rec = min(pool, key=lambda t: t[0])
        return self._recs_to_entries([(oid, rec)], reply_lang, 1)

    def most_expensive(self, reply_lang: str = "en", merchant: str = None):
        pool = self._superlative_pool(lambda r: r["price"], merchant)
        if not pool:
            return None
        _, oid, rec = max(pool, key=lambda t: t[0])
        return self._recs_to_entries([(oid, rec)], reply_lang, 1)

    def highest_discount(self, reply_lang: str = "en", merchant: str = None):
        # Impossible >100% rows (old_price >=2x list price) are data bugs and
        # are excluded from the "highest discount" ranking -- everything else,
        # expired or not, is eligible.
        pool = self._superlative_pool(
            lambda r: r["discount"] if (r["discount"] is not None and r["discount"] <= 100) else None,
            merchant)
        pool = [t for t in pool if not _rec_is_expired(t[2])] or pool
        if not pool:
            return None
        _, oid, rec = max(pool, key=lambda t: t[0])
        return self._recs_to_entries([(oid, rec)], reply_lang, 1)

    def in_price_range(self, lo: float, hi: float, reply_lang: str = "en",
                       limit: int = 6) -> list:
        """All offers whose price lands in [lo, hi], best-deal-first."""
        recs = []
        for oid, rec in self.offers.items():
            p = rec["price"]
            if p is not None and lo <= p <= hi:
                recs.append((p, oid, rec))
        recs.sort(key=lambda t: t[0])
        return self._recs_to_entries([(o, r) for _, o, r in recs[:limit * 4]],
                                     reply_lang, limit)

    # -- deterministic negatives (slice 3) ----------------------------------

    _INTRODUCER_RES = [
        re.compile(r"(?:عروض|خصومات|كوبونات|عرض|خصم)\s*(?:من|عند|لـ|ل)?\s*([^\s؟?.,،]+(?:\s+[^\s؟?.,،]+){0,2})"),
        re.compile(r"عند\s+([^\s؟?.,،]+(?:\s+[^\s؟?.,،]+){0,2})\s+عروض"),
        re.compile(r"^([^\s؟?.,،]+(?:\s+[^\s؟?.,،]+){0,2})\s+عروض(?:\s|$)"),
        re.compile(r"(?:offers?|deals?|coupons?)\s+(?:from|at|by|of|for)\s+([A-Za-z][\w&'’\s]{1,28})"),
        re.compile(r"^([A-Za-z&'’\s]{2,28}?)\s+(?:offers?|deals?|coupons?)(?:\s|$)"),
    ]

    def unknown_merchant_mention(self, query: str) -> str | None:
        """Returns a merchant-y token that the query names via an introducer
        pattern but that resolves to NO known merchant, product, or category --
        the case to answer with a deterministic "we don't have offers from this
        merchant" rather than letting embeddings fabricate. None when the token
        is actually a known merchant/product/category or no token was found."""
        q = (query or "").strip()
        if not q:
            return None
        low = q.lower()
        known_merchants_norm = {normalize_arabic(n.lower()) for n in self._merchant_names}

        def _strip_clitics(word):
            """Peels attached Arabic proclitics so a clitic-bound noun isn't
            reported as an unknown merchant brand. Conservative by design:
            - Latin/mixed words ("لdate night"): strip leading و/ف/ك/ل/ب/لل.
            - Pure Arabic: strip ONLY the "لل" (لـ+ال) bound preposition, never
              "ال" or single l/b/w/k -- those belong to real Arabic brand names
              ("الجندل") and must survive intact.
            So "للبشرة" -> "بشرة", "لdate night" -> "date night", but
            "الجندل"/"كنتاكي" stay untouched."""
            w = normalize_arabic(word)
            has_latin = any("\u0041" <= ch <= "\u007a" or "\u00c0" <= ch <= "\u024f" for ch in w)
            clitics = r"^(لل|ال|و|ف|ك|ل|ب)(?=\S)" if has_latin else r"^(لل)(?=\S)"
            while True:
                m = re.match(clitics, w)
                if not m:
                    break
                w = w[m.end():]
                if not w:
                    break
            return w

        def _core(cand):
            """Strip leading AND trailing decoration words ('مطعم', 'كافيه',
            'حلوة', 'إقامة', ...) so a freshly-coined brand name after them is
            still recognized as unknown, while kept-together filler phrases
            (e.g. 'المناسب', 'بديعة دلوقتي') collapse to '' instead of being
            reported as an unknown merchant. Returns '' when the candidate is
            decoration-only (-> skip)."""
            words = cand.split()
            while words and normalize_arabic(words[0].lower()) in _DECORATION_NORM:
                words = words[1:]
            while words and normalize_arabic(words[-1].lower()) in _DECORATION_NORM:
                words = words[:-1]
            if words:
                stripped = _strip_clitics(words[0].lower())
                if stripped != normalize_arabic(words[0].lower()):
                    if stripped:
                        words[0] = stripped
                    else:
                        words = words[1:]
            return " ".join(words)

        for rx in self._INTRODUCER_RES:
            m = rx.search(q)
            if not m:
                continue
            cand = _core(_clean_name(m.group(1)))
            if not cand or len(cand) > 30 or cand.lower() in _DECORATION_WORDS:
                continue
            if cand.lower() in self._aliases or cand in self._merchant_names:
                continue
            cand_norm = normalize_arabic(cand.lower())
            if cand_norm in known_merchants_norm:
                continue
            # product / category words are legit, not unknown merchants
            if self.resolve_product(cand) or self.resolve_category(cand):
                continue
            # a known merchant name contained inside the candidate is "known"
            if any(k in cand_norm for k in known_merchants_norm if len(k) >= 4):
                continue
            # latin lowercase-only token (not a capitalized brand) is not a merchant
            if re.fullmatch(r"[a-z0-9 '&.,%]+", cand) and not re.search(r"[A-Z]", cand):
                continue
            return cand
        return None


def _as_int(v):
    try:
        return int(str(v).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(re.sub(r"[^\d.]", "", str(v)))
    except ValueError:
        return None


_EXPIRY_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y",
    "%d-%m-%Y", "%d %b %Y", "%b %d, %Y", "%d %B %Y",
)


def _expiry_date(value):
    """Best-effort parse of an offer expiry string -> datetime.date | None.
    Unparseable values are treated as 'unknown' (NOT expired)."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        pass
    for fmt in _EXPIRY_FORMATS:
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _rec_is_expired(rec):
    e = rec.get("expiry")
    if not e:
        return False
    d = _expiry_date(e)
    return d is not None and d < datetime.date.today()