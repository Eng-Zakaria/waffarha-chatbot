"""Regression test for the Phase-2 agent evidence-gate hole
(fix/routing-and-freshness).

Traced failure: a plan_parse_error on a branch-location query still rendered
offer cards (KFC + Pizza Hut) with zero address evidence, because
_finish_fallback unconditionally ran an ungrounded semantic search and
rendered whatever came back.

Method: mock the planner (no LLM needed) and force parse errors on varied
queries, plus a valid-plan-then-replan-failure path. Assert every turn ends
with zero evidence, zero card markers, and a non-empty clarify/fallback
response. Run: venv/Scripts/python -m pytest tests/acceptance/test_agent_gate.py -q
(engine load ~1-2 min once per session).
"""
import pytest

from agent.planner import PlanParseError

PARSE_ERROR_QUERIES = [
    ("فين فرع كنتاكي في مدينة نصر؟", "ar"),   # the traced location case
    ("الطلب بتاعي وصل ولا لسه؟", "ar"),       # personal phrasing
    ("is the offer actually valid?", "en"),   # off-topic validity probe
    ("عايز حاجة حلوة وخلاص", "ar"),           # ambiguous
    ("عايز عروض بيتزا", "ar"),                # well-formed offer query
    ("hair treatment offers", "en"),          # well-formed English query
]

CARD_MARKERS = ("🏷️", "عثرت على عروض مناسبة", "Found matching offers",
                "هذا هو العرض الذي سألت عنه", "Here is the offer you asked about")


@pytest.fixture(scope="module")
def engine():
    import core.app as appmod
    eng = appmod.get_agent_engine()
    orig = eng._planner
    yield eng
    eng._planner = orig


def _drain(engine, query, reply_lang):
    pieces = list(engine.answer_stream(
        query, reply_lang=reply_lang, history=[], recent_offers=[],
        user_id=None, identity=None))
    answer = ""
    for p in pieces:
        if isinstance(p, str):
            answer = p
    return answer, list(getattr(engine, "_last_evidence", []) or [])


@pytest.mark.parametrize("query,lang", PARSE_ERROR_QUERIES)
def test_parse_error_renders_zero_cards(engine, query, lang):
    def _raise(*a, **k):
        raise PlanParseError("forced parse error")
    engine._planner = _raise
    answer, evidence = _drain(engine, query, lang)
    assert evidence == [], "evidence leaked on plan_parse_error: %s" % (evidence,)
    assert answer, "empty reply on plan_parse_error"
    for m in CARD_MARKERS:
        assert m not in answer, "card marker %r in fallback reply" % (m,)


def _impossible_price_plan(query):
    return {"plan": {"tool": "search_offers",
                     "args": {"query": query, "price_range": [100000, 200000], "limit": 6},
                     "intent": "catalog", "goal": "forced gate failure",
                     "entities": {}, "constraints": {"price_range": [100000, 200000]},
                     "references": [], "known_information": [],
                     "missing_information": "", "next_action": "plan"}}


@pytest.mark.parametrize("query,lang", [
    ("luxury watches over budget", "en"),
    ("ساعات فاخرة غالية جدا", "ar"),
])
def test_replan_failure_renders_zero_cards(engine, query, lang):
    """Valid first plan (impossible price -> gate failure) then a parse error
    on replan: evidence collected under the failed plan must not render."""
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _impossible_price_plan(query)
        raise PlanParseError("forced replan parse error")

    engine._planner = _flaky
    answer, evidence = _drain(engine, query, lang)
    assert evidence == [], "stale evidence rendered after replan failure: %s" % (evidence,)
    assert answer, "empty reply on replan failure"
    for m in CARD_MARKERS:
        assert m not in answer, "card marker %r in fallback reply" % (m,)
