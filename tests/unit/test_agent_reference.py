#!/usr/bin/env python3
"""Fast, offline unit tests for the Stage-3 deterministic reference resolver
(agent/reference.py). No Ollama, no index, no LLM calls: the resolution must
be pure function of (query, stored recent_offers, faceted) -- the same stored
state the old cascade's _resolve_followup_targets used."""
import os
import sys
from typing import ClassVar

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


from agent.reference import (
    _looks_like_comparison,
    _looks_like_other_offer,
    corroborate_references,
    extract_price_range,
    resolve_reference,
)


def _offer(oid, merchant="KFC", price="150"):
    return {"metadata": {"source": "offer", "id": oid,
                         "merchant": merchant, "price": price,
                         "title": f"offer {oid}"}}


_STORED = [_offer("10", "KFC", "150"), _offer("11", "KFC", "300"),
           _offer("12", "زادنا", "90")]


class _FakeFaceted:
    merchants: ClassVar[set[str]] = {"KFC", "زادنا"}

    def resolve_merchants(self, name):
        return [m for m in self.merchants if str(m) in name]


def _resolve(query, recent=None):
    return resolve_reference(query, recent if recent is not None else _STORED,
                             _FakeFaceted())


def test_no_state_is_new_topic():
    r = resolve_reference("أرخص عروض", [], _FakeFaceted())
    assert r["kind"] == "none"
    assert r["verdict"] == "NEW_TOPIC"
    assert r["reason_code"] == "no_state"


def test_blank_query_is_new_topic():
    r = _resolve("   ")
    assert r["kind"] == "none"
    assert r["verdict"] == "NEW_TOPIC"


def test_merchant_naming_points_at_stored_offers():
    r = _resolve("اريد معلومات عن عروض KFC")
    assert r["kind"] == "same_offer"
    assert r["verdict"] == "SAME_OFFER"
    assert r["reason_code"] == "merchant"
    assert set(r["target_ids"]) == {"10", "11"}


def test_merchant_not_in_stored_state_is_fresh_topic():
    r = resolve_reference("عايز عروض ماكدونالدز", _STORED, _FakeFaceted())
    assert r["kind"] == "none"
    assert r["verdict"] == "NEW_TOPIC"


def test_ordinal_pointer_to_stored_offer():
    r = _resolve("التاني بكام")
    assert r["kind"] == "ordinal"
    assert r["target_ids"] == ["11"]


def test_neg_ordinal_last_pointer():
    r = _resolve("الاخير ده؟")
    assert r["kind"] == "ordinal"
    assert r["target_ids"] == ["12"]


def test_price_pointer_scopes_to_latest_shown():
    r = _resolve("ده سعره كام قبل الخصم؟")
    assert r["kind"] == "same_offer"
    assert r["reason_code"] == "price_pointer"
    assert r["target_ids"] == ["10"]


def test_compare_implied_uses_top_two_stored():
    r = _resolve("قارن بين العروضين")
    assert r["kind"] == "compare"
    assert r["reason_code"] == "compare_implied"
    assert r["target_ids"] == ["10", "11"]


def test_other_offer_implied_anchors_source_merchant():
    r = _resolve("عايز عروض تانية")
    assert r["kind"] == "other_offer"
    assert r["verdict"] == "OTHER_OFFER_SAME_SOURCE"
    assert r["reason_code"] == "other_implied"
    assert r["anchor_merchant"] == "KFC"


def test_merchant_other_offer_anchors_that_merchant():
    r = _resolve("غير عروض KFC")
    assert r["kind"] == "other_offer"
    assert r["anchor_merchant"] == "KFC"


def test_generic_cross_reference_points_at_latest_shown():
    r = _resolve("العرض اللي فات ده؟")
    assert r["kind"] == "same_offer"
    assert r["reason_code"] == "cross_ref"
    assert r["target_ids"] == ["10"]


def test_self_contained_query_is_new_topic():
    r = _resolve("عايز عروض بيتزا")
    assert r["kind"] == "none"
    assert r["verdict"] == "NEW_TOPIC"
    assert r["reason_code"] == "self_contained"


def test_single_stored_offer_cannot_compare():
    r = _resolve("قارن بينهم", recent=[_offer("10", "KFC")])
    assert r["kind"] == "none"
    assert r["reason_code"] == "compare_no_partner"


def test_extract_price_range_single_bound():
    assert extract_price_range("عروض تحت 200 جنيه") == (0, 200)
    assert extract_price_range("offers above 300 EGP") == (300, float("inf"))
    assert extract_price_range("between 100 and 400") == (100, 400)


def test_comparison_and_other_offer_phrase_helpers():
    assert _looks_like_comparison("ايهما أفضل؟")
    assert _looks_like_other_offer("عروض تانية")
    assert not _looks_like_comparison("عروض عادية")
    assert not _looks_like_other_offer("التاني بكام")


def test_corroborate_keeps_only_plan_refs_backed_by_user_words():
    resolved = _resolve("التاني بكام")
    kept, dropped = corroborate_references(["التاني", "that coupon"],
                                           "التاني بكام", resolved)
    assert kept == ["التاني"]
    assert dropped == ["that coupon"]


def test_corroborate_drops_all_refs_when_query_is_self_contained():
    resolved = _resolve("عايز عروض بيتزا")
    kept, dropped = corroborate_references(["that coupon", "هالخصم"],
                                           "عايز عروض بيتزا", resolved)
    assert kept == []
    assert dropped == ["that coupon", "هالخصم"]