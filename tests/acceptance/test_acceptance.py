"""Acceptance suite for the verification audit (branch audit/verification).

Reads the records produced by eval/turn_trace.py --cases
eval/acceptance_cases.json (eval/acceptance_traces.jsonl). Regenerate traces
first, then run:
    venv/Scripts/python eval/turn_trace.py --cases eval/acceptance_cases.json \\
        --out eval/acceptance_traces.jsonl --now 2026-09-21
    venv/Scripts/python -m pytest tests/acceptance/ -v

Each case has HARD expectations (asserted) and RECORD fields (captured in the
trace file for human review, not asserted here). Cases that fail today are
marked xfail with the observed reason -- the suite documents current gaps and
turns green as fixes land. Never weaken an expectation to make it pass.
"""
import json
import os

import pytest

TRACES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "eval", "acceptance_traces.jsonl")

ENGINES = ["cascade", "agent"]


def load():
    if not os.path.exists(TRACES):
        pytest.skip("trace file missing: run eval/turn_trace.py first: %s" % TRACES)
    recs = [json.loads(l) for l in open(TRACES, encoding="utf-8")]
    out = {}
    for r in recs:
        out[(r["case"], r["turn"], r["engine"])] = r
    return out


REC = load()

NON_SEARCH = ["greet_ya_hala", "greet_hala", "greet_hala_long", "greet_basha",
              "greet_ezayak", "greet_hello", "thanks_basha", "gibberish"]
FALSE_POS = ["fp_delete_account", "fp_hair", "fp_physio", "fp_gym",
             "fp_cart", "fp_valid"]

# (case, engine) -> observed reason for currently-failing HARD expectations.
# Resolved xfails (marker removed, kept here as record — cite phase commit):
# - ("loc_kfc_nasr", "cascade"): passed since Phase 0 (live catalog-handle,
#   zero cards; answer is an honest no-location message, no verified address
#   data used). See reports/verification_report.md Phase 0 addendum.
# - ("fp_gym", "agent"), ("fp_valid", "agent"): passed since Phase 2
#   (STRICT_AGENT_FALLBACK: plan_parse_error fallback serves zero cards).
# - ("followup_compare", "agent"): fallback now renders nothing (Phase 2), so
#   the subset test SKIPs instead of failing.
# - ("greet_ya_hala", both), ("greet_hala_long", both): passed since Phase 3
#   (greeting normalization: vocative strip + elongation collapse; both hit
#   greeting/agent-safety pre-retrieval with zero cards).
# - ("fp_cart", "cascade"), ("fp_valid", "cascade"): passed since Phase 3
#   (token-boundary closing match kills the "done"⊂"abandoned" and literal
#   "actual" false positives; both now reach catalog-handle).
# - ("fp_valid:lang", "agent"): passed since Phase 2+3 (fallback serves zero
#   cards with an English reply).
XFAIL = {
    ("greet_basha", "cascade"): "retrieval runs (embedding-only strict floor refuses at 0.444); zero cards served",
    ("greet_basha", "agent"): "5 offer cards via tool:search_offers; 'باشا' not stripped",
    # Phase 3: closing FP dead ("no more than" no longer matches); still zero
    # structured cards because the live catalog path answers in text.
    ("offer_under100", "cascade"): "catalog-handle text answer, zero structured cards",
    ("fp_delete_account", "cascade"): "out-of-scope refusal; task expects it answered (retrieve_faq on agent)",
    ("fp_hair", "cascade"): "out-of-scope refusal; task expects it answered",
    ("fp_hair", "agent"): "3 offer cards via tool:search_offers despite OUT_OF_SCOPE hint",
    ("fp_physio", "cascade"): "out-of-scope refusal; task expects it answered",
    ("fp_physio", "agent"): "4 offer cards via tool:search_offers despite OUT_OF_SCOPE hint",
    ("fp_gym", "cascade"): "out-of-scope refusal; task expects it answered",
    ("fp_cart", "agent"): "3 offer cards via tool:search_offers",
    ("loc_kfc_nasr", "agent"): "1 offer card via successful-plan search_offers; no address data",
    # Phase 0 (live catalog): cascade now answers via catalog-handle with zero
    # cards ("Couldn't find location info" — no verified address data used).
    ("offer_pizza", "cascade"): "live catalog-handle text answer, zero structured cards (incl. expired 2021 row as current)",
    # Phase 0 (live catalog): agent turn-2 compare fell to relax_failed and
    # rendered 5 unrelated expired cards outside the turn-1 set. Phase 2
    # (STRICT_AGENT_FALLBACK): fallback renders nothing, so the subset test
    # SKIPs instead (see test). Cascade skips: live catalog turn-1 stores
    # no evidence.
    # Phase 1: agent has no explicit-validity path (scoped to cascade); it
    # serves live-only cards for validity questions instead of honest framing.
    ("valid_mado_en", "agent"): "no validity path on agent; serves live cards without expired framing",
    ("valid_arabiata_ar", "agent"): "no validity path on agent; serves live cards without expired framing",
    ("valid_mado_en:lang", "agent"): "reply_lang=ar for English query",
}


def _xfail(case, engine):
    reason = XFAIL.get((case, engine))
    if reason:
        pytest.xfail(reason)


def _closing_like(answer):
    a = answer.lower()
    return ("شكر" in answer or "welcome" in a or "anything else" in a)


@pytest.mark.parametrize("case", NON_SEARCH)
@pytest.mark.parametrize("engine", ENGINES)
def test_non_search_no_cards_no_retrieval(case, engine):
    _xfail(case, engine)
    r = REC[(case, 0, engine)]
    assert len(r["cards"]) == 0, "expected zero offer cards, got %d (%s)" % (
        len(r["cards"]), r["exit_gate"])
    assert r["retrieval_ran"] is False, "expected no retrieval/tool call (%s)" % r["exit_gate"]


@pytest.mark.parametrize("engine", ENGINES)
def test_offer_pizza_has_unexpired_cards(engine):
    _xfail("offer_pizza", engine)
    r = REC[("offer_pizza", 0, engine)]
    assert len(r["cards"]) >= 1, "expected at least 1 card"
    assert r["expired_count"] == 0, "expired cards served: %d" % r["expired_count"]


@pytest.mark.parametrize("engine", ENGINES)
def test_offer_under100_price_ceiling(engine):
    _xfail("offer_under100", engine)
    r = REC[("offer_under100", 0, engine)]
    assert not _closing_like(r["answer_prefix"]), "thanks/closing reply: %r" % r["answer_prefix"][:80]
    assert len(r["cards"]) >= 1, "expected cards"
    import re
    for c in r["cards"]:
        m = re.search(r"\d+(\.\d+)?", str(c["price"]))
        assert m and float(m.group(0)) <= 100, "card over 100: %r" % (c["price"],)


@pytest.mark.parametrize("case", FALSE_POS)
@pytest.mark.parametrize("engine", ENGINES)
def test_false_positives_answered_not_refused(case, engine):
    _xfail(case, engine)
    r = REC[(case, 0, engine)]
    assert r["exit_gate"] not in ("out-of-scope", "out-of-scope-guardrail"), \
        "refused: %s" % r["exit_gate"]
    assert len(r["cards"]) == 0, "offer cards on non-offer query: %d" % len(r["cards"])
    assert not _closing_like(r["answer_prefix"]), "thanks/closing reply"


@pytest.mark.parametrize("engine", ENGINES)
def test_followup_compare_subset(engine):
    t0 = REC[("followup_compare", 0, engine)]
    t1 = REC[("followup_compare", 1, engine)]
    if len(t0["cards"]) < 2:
        pytest.skip("turn 1 returned fewer than 2 cards")
    ids0 = {c["id"] for c in t0["cards"]}
    ids1 = [c["id"] for c in t1["cards"]]
    if not ids1:
        pytest.skip("turn 2 fallback rendered nothing (Phase-2 zero-card fallback)")
    assert set(ids1) <= ids0, "turn 2 added cards outside turn 1: %s vs %s" % (ids1, ids0)
    if engine == "cascade":
        assert t1["retrieval_ran"] is False, "fresh retrieval on cascade follow-up"


@pytest.mark.parametrize("engine", ENGINES)
def test_location_no_offer_cards(engine):
    _xfail("loc_kfc_nasr", engine)
    r = REC[("loc_kfc_nasr", 0, engine)]
    assert len(r["cards"]) == 0, "offer cards on location query: %d (%s)" % (
        len(r["cards"]), r["exit_gate"])


@pytest.mark.parametrize("engine", ENGINES)
def test_personal_without_identity_no_offer_cards(engine):
    r = REC[("personal_order", 0, engine)]
    assert len(r["cards"]) == 0, "offer cards on personal query: %d" % len(r["cards"])


@pytest.mark.parametrize("case", ["valid_mado_en", "valid_arabiata_ar"])
@pytest.mark.parametrize("engine", ENGINES)
def test_explicit_validity_honest_no_cards(case, engine):
    _xfail(case, engine)
    r = REC[(case, 0, engine)]
    assert len(r["cards"]) == 0, "live-style cards on expired offer: %d" % len(r["cards"])
    ans = r["answer_prefix"]
    assert ("انتهى" in ans or "expired" in ans.lower()), \
        "no honest expired framing: %r" % ans[:120]


def test_reply_language_matches_query():
    for (case, turn, engine), r in sorted(REC.items()):
        if (case + ":lang", engine) in XFAIL:
            continue
        want = "ar" if (r["query_lang"] == "ar" or any(ord(c) > 127 for c in r["query"])) else "en"
        assert r["reply_lang"] == want, "%s/%s: want %s got %s" % (case, engine, want, r["reply_lang"])


@pytest.mark.parametrize("engine", ENGINES)
def test_reply_language_known_gap(engine):
    for case in ("valid_mado_en",):
        _xfail(case + ":lang", engine)
    r2 = REC[("valid_mado_en", 0, engine)]
    assert r2["reply_lang"] == "en", "want en got %s" % r2["reply_lang"]
