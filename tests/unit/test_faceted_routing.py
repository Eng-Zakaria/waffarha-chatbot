#!/usr/bin/env python3
"""Unit tests for the faceted routing layer (core/faceted.py).

Uses a small synthetic offer corpus (en/ar doc pairs per offer id) so the
tests are fast and deterministic -- no index, no Ollama. The integration
smoke against the real 9,142-doc index lives in scripts/test_faceted_lib.py.
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.faceted import FacetedCatalog


def _doc(oid, lang, merchant, sec, text, price=None, old=None, discount=None, sold=0, expiry=None):
    return {
        "metadata": {
            "source": "offer",
            "id": oid,
            "lang": lang,
            "merchant": merchant,
            "section_id": sec,
            "title": text,
            "text": text,
            "price": price,
            "old_price": old,
            "discount": discount,
            "expiry": expiry,
            "sold_count": str(sold) if sold else "",
            "url": f"https://x.test/{oid}",
        },
        "text": f"{merchant} {text}",
    }


def _pair(oid, en_m, ar_m, sec, en_text, ar_text, **kw):
    return [_doc(oid, "en", en_m, sec, en_text, **kw),
            _doc(oid, "ar", ar_m, sec, ar_text, **kw)]


def _build_catalog():
    docs = []
    docs += _pair(1, "KFC", "دجاج كنتاكي", 1, "Zinger sandwich combo meal",
                  "وجبة كومبو زنجر", price=100, old=160, discount=39, sold=900)
    docs += _pair(2, "KFC", "دجاج كنتاكي", 2, "Fried chicken combo meal",
                  "وجبة دجاج فرايد", price=200, old=300, discount=58, sold=800)
    docs += _pair(3, "McDonald's", "ماكدونالدز", 1, "Big sandwich with fries",
                  "ساندوتش مع بطاطس", price=150, old=350, discount=57, sold=700)
    docs += _pair(4, "Pizza Hut", "بيتزا هت", 6, "Medium pizza with salad",
                  "بيتزا وسط مع سلطة", price=250, old=385, discount=35, sold=600)
    docs += _pair(5, "Pizza Hut", "بيتزا هت", 6, "Large pizza combo meal",
                  "وجبة بيتزا كبير", price=90, old=220, discount=41, sold=500)
    docs += _pair(6, "Pizza Hut", "بيتزا هت", 6, "Pizza party box for three",
                  "علبة حفلة بيتزا", price=300, old=375, discount=20, sold=10)
    docs += _pair(7, "Pizza Station", "بيتزا ستاشن", 6, "Thin crust pizza",
                  "بيتزا قشرة رقيقة", price=120, old=170, discount=30, sold=50)
    docs += _pair(8, "Koshary ElTahrir", "كشري التحرير", 1, "Koshary plate",
                  "طبق كشري بالكامل", price=50, old=60, discount=10, sold=400)
    docs += _pair(9, "Fun Kingdom", "فن كينجدم", 8, "Arcade games and skating",
                  "ألعاب وتزلج", price=400, old=640, discount=38, sold=300)
    return FacetedCatalog(
        docs,
        aliases={
            "kfc": "KFC",
            "كنتاكي": "دجاج كنتاكي",
            "starbucks": "Starbucks",
            "ستاربكس": "Starbucks",
            "بيتزا هت": "Pizza Hut",
            "pizzahut": "Pizza Hut",
        },
        offer_intent_words={"عروض", "عرض", "خصم", "خصومات", "offers",
                            "وفر", "بكام", "عندكم"},
        faq_guard_words={"ازاي", "كيف", "استرجع", "ترجيع", "how"},
    )


def _count_per_merchant(entries):
    return Counter(e["_merchant"] for e in entries)


def test_merchant_alias_chaining_arabic():
    """slice 1: Arabic alias value chains to the English canonical."""
    c = _build_catalog()
    assert c.resolve_merchants("عروض كنتاكي") == ["KFC"]
    assert c._canonical("كنتاكي") == "KFC"
    assert c._canonical("دجاج كنتاكي") == "KFC"
    assert c.display_name("KFC", "ar") == "دجاج كنتاكي"
    assert len(c.offers_for_merchant("KFC", "en")) == 2


def test_merchant_multi_mention_order():
    c = _build_catalog()
    got = c.resolve_merchants("عروض KFC و Pizza Hut")
    assert got[0] == "KFC"
    assert got[1] == "Pizza Hut"


def test_phantom_offerless_merchant():
    """slice 3: a merchant with no offers still resolves, with 0 offers."""
    c = _build_catalog()
    assert c.resolve_merchants("عروض ستاربكس") == ["Starbucks"]
    assert c.offers_for_merchant("Starbucks", "en") == []


def test_category_label_mapping():
    """slice 2: resolve_category returns the key; offers_* maps key->label."""
    c = _build_catalog()
    assert c.resolve_category("عروض أكل") == "food"
    entries = c.offers_for_category("food", "en", limit=10)
    assert len(entries) == 8  # everything except Fun Kingdom (entertainment sec8)
    assert {e["_merchant"] for e in entries} == {
        "KFC", "McDonald's", "Pizza Hut", "Pizza Station", "Koshary ElTahrir"}
    assert "Fun Kingdom" not in {e["_merchant"] for e in entries}
    assert c.resolve_category("عروض بلاستيك سباكة") is None


def test_product_diversity_cap():
    """slice 4: offers_for_product caps per merchant for diversity."""
    c = _build_catalog()
    one = c.offers_for_product("pizza", "en", limit=6, top_per_merchant=1)
    assert len(one) == 2
    assert {e["_merchant"] for e in one} == {"Pizza Hut", "Pizza Station"}
    two = c.offers_for_product("pizza", "en", limit=6, top_per_merchant=2)
    assert len(two) == 3                     # capped at 2 from Pizza Hut
    assert _count_per_merchant(two)["Pizza Hut"] == 2


def test_deterministic_superlatives():
    """slice 3: cheapest / most expensive / highest discount / price range."""
    c = _build_catalog()
    assert c.cheapest("en")[0]["metadata"]["price"] == 50        # Koshary
    assert c.most_expensive("en")[0]["metadata"]["price"] == 400  # Fun Kingdom
    assert c.highest_discount("en")[0]["metadata"]["discount"] == 58  # KFC fried
    pr = c.in_price_range(90, 300, "en")
    assert all(90 <= e["metadata"]["price"] <= 300 for e in pr)


def test_superlatives_show_expired_offers():
    """Expired offers are ALWAYS eligible (catalog is shown as-is): only
    impossible >100% discount rows are excluded from the discount ranking."""
    docs = []
    # expired 0-price freebie IS the cheapest (expired offers stay visible)
    docs += _pair(9001, "Dental Boss", "دنتال بوس", 4,
                  "Free dental examination", "كشف أسنان مجاني",
                  price=0, old=100, discount=100, sold=1, expiry="2017-11-15")
    docs += _pair(9002, "Health Clinic", "كلينك", 4,
                  "Doctor checkup", "كشف عند طبيب",
                  price=35, old=70, discount=50, sold=400, expiry="2099-12-31")
    # garbage >100% discount row (old price wrongly doubled) can't win
    docs += _pair(9003, "Happy Dolphin", "هابي دولفين", 1,
                  "Sunset lunch cruise", "غداء على النيل",
                  price=275, old=414, discount=139, sold=10, expiry="2099-12-31")
    # legit 85% row is the real max once >100% rows are dropped
    docs += _pair(9004, "Sweet Corner", "ركن الحلويات", 1,
                  "Pastry box", "علبة معجنات",
                  price=150, old=1000, discount=85, sold=500, expiry="2099-12-31")
    docs += _pair(9005, "Dental Boss", "دنتال بوس", 4,
                  "Cleaning session", "جلسة تنظيف",
                  price=75, old=150, discount=50, sold=300, expiry="2099-12-31")
    c = FacetedCatalog(docs)
    # expired 0-price freebie shown as cheapest (per "always show" policy)
    assert c.cheapest("en")[0]["metadata"]["id"] == 9001
    assert c.cheapest("en")[0]["metadata"]["price"] == 0
    assert c.most_expensive("en")[0]["metadata"]["price"] == 275
    assert c.highest_discount("en")[0]["metadata"]["id"] == 9004  # 85, not 139
    # merchant-scoped superlative still scopes to the merchant (expiry ignored)
    db = c.cheapest("en", merchant="Dental Boss")
    assert len(db) == 1 and db[0]["metadata"]["id"] == 9001


def test_price_filter_on_merchant():
    entries = _build_catalog().offers_for_merchant(
        "KFC", "en", price_filter=(90, 110))
    assert len(entries) == 1
    assert entries[0]["metadata"]["price"] == 100


def test_unknown_merchant_negatives():
    """slice 3: deterministic unknown-merchant detection."""
    c = _build_catalog()
    assert c.unknown_merchant_mention("عروض من مطعم الجندل") == "الجندل"
    assert c.unknown_merchant_mention("offers from Star Lounge") == "Star Lounge"
    assert c.unknown_merchant_mention("عروض برنش بار") == "برنش بار"
    # generic / product / category words are NOT merchants
    assert c.unknown_merchant_mention("العروض المتاحة دلوقتي") is None
    assert c.unknown_merchant_mention("عروض قهوة") is None
    assert c.unknown_merchant_mention("ازاي اشتري كوبون") is None
    assert c.unknown_merchant_mention("السلام عليكم") is None
    # known merchants (incl. phantom) are NOT unknown
    assert c.unknown_merchant_mention("عروض ستاربكس") is None  # resolves -> known


def test_intent_and_faq_guards():
    c = _build_catalog()
    assert c.has_offer_intent("عروض كنتاكي")
    assert not c.has_offer_intent("السلام عليكم")
    assert c.has_faq_guard("ازاي اشتري كوبون")
    assert not c.has_faq_guard("عروض كنتاكي")


def test_partners_snapshot_identity():
    """dim_partners snapshot: authoritative EN/AR pair + status/part_id for
    known partners; offer>partner linking; phantom for partners with no offers
    (and aliases); legacy fallback for merchants the snapshot doesn't cover."""
    def _build_partners_catalog():
        docs = []
        docs += _pair(1, "KFC", "دجاج كنتاكي", 1, "Zinger sandwich combo",
                      "وجبة كومبو زنجر", price=100, old=160, discount=39, sold=900)
        docs += _pair(2, "KFC", "دجاج كنتاكي", 1, "Fried chicken combo meal",
                      "وجبة دجاج فرايد", price=200, old=300, discount=58, sold=800)
        docs += _pair(7, "Pizza Station", "بيتزا ستاشن", 6, "Thin crust pizza",
                      "بيتزا قشرة رقيقة", price=120, old=170, discount=30, sold=50)
        partners = [
            {"part_id": 993, "name_en": "KFC", "name_ar": "دجاج كنتاكي", "status": "active"},
            {"part_id": 2117, "name_en": "Trio Lounge", "name_ar": "تريو لاونج", "status": "active"},
            {"part_id": 9999, "name_en": "Old Burger Co", "name_ar": "أولد برجر", "status": "inactive"},
        ]
        return FacetedCatalog(
            docs,
            aliases={"kfc": "KFC", "كنتاكي": "دجاج كنتاكي",
                     "starbucks": "Starbucks", "ستاربكس": "Starbucks"},
            partners=partners,
        )

    c = _build_partners_catalog()
    # partner identity wins over doc-inferred names
    info = c._canonical_info["KFC"]
    assert info.get("en") == "KFC" and info.get("ar") == "دجاج كنتاكي"
    assert info.get("part_id") == 993 and info.get("status") == "active"
    assert c.is_live("KFC") is True
    assert c._canonical("كنتاكي") == "KFC"
    assert len(c.offers_for_merchant("KFC", "en")) == 2

    # partner with zero offers in the index -> resolves, 0 offers, still "live"
    assert c.resolve_merchants("عروض تريو لاونج") == ["Trio Lounge"]
    assert c.offers_for_merchant("Trio Lounge", "en") == []
    assert c.is_live("Trio Lounge") is True

    # non-live partner flagged
    assert c.is_live("Old Burger Co") is False

    # alias-phantom merchant NOT in partners -> resolves but is_live() is None
    assert c.resolve_merchants("عروض ستاربكس") == ["Starbucks"]
    assert c.offers_for_merchant("Starbucks", "en") == []
    assert c.is_live("Starbucks") is None

    # merchant not covered by the snapshot -> legacy fallback still works
    assert c._canonical("Pizza Station") == "Pizza Station"
    assert len(c.offers_for_merchant("Pizza Station", "en")) == 1
    assert c.is_live("Pizza Station") is None

    # no orphans after offer>partner linking
    assert all(o.get("merchant_canonical") for o in c.offers.values())


def test_merchant_subtoken_containment():
    """A merchant matched inside the text span of a LONGER matching merchant
    ("بابا" inside "بابا جونز") is a sub-token false positive -- must resolve
    to the one real brand, never two headers for one brand."""
    docs = []
    docs += _pair(1, "Papa John's", "بابا جونز", 6, "Pepperoni pizza",
                  "بيتزا بيبروني", price=180, old=260, discount=31, sold=120)
    docs += _pair(2, "Papa John's", "بابا جونز", 6, "Cheese sticks",
                  "أصابع جبنة", price=90, old=140, discount=36, sold=80)
    docs += _pair(3, "Baba", "بابا", 1, "Fresh juice", "عصير طازة",
                  price=60, old=90, discount=34, sold=40)
    c = FacetedCatalog(
        docs,
        aliases={"بابا جونز": "Papa John's", "papa johns": "Papa John's",
                 "بابا": "Baba"},
    )
    assert c.resolve_merchants("عايز عروض بابا جونز") == ["Papa John's"]
    assert c.resolve_merchants("عروض بابا") == ["Baba"]
    assert c.resolve_merchants("بابا جونز وكشري") == ["Papa John's"]
    assert len(c.offers_for_merchant("Papa John's", "en")) == 2


def test_imperative_filler_not_merchant():
    """Command verbs like "هات/جيب/وريني" are fillers, not brands -- the
    unknown-merchant negative must not fire on "هات عروق كفتة وسوشي"."""
    c = _build_catalog()
    assert c.resolve_merchants("هات عروق كفتة وسوشي") == []
    assert c.unknown_merchant_mention("هات عروق كفتة وسوشي") is None
    assert c.unknown_merchant_mention("جيب عروض كنتاكي") is None