#!/usr/bin/env python3
"""Fast, offline unit tests for the Stage-2 planner JSON contract
(agent/planner.py): extraction, validation, coercion of the single structured
decision the model emits."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from agent.planner import (
    PlanParseError,
    build_plan_prompt,
    parse_plan,
)

TOOLS = ["search_offers", "get_offer"]


def _valid_plan(**over):
    plan = {
        "tool": "search_offers",
        "args": {"merchant": "KFC", "limit": 6},
        "goal": "find KFC offers for the user",
        "entities": {"merchant": ["KFC"]},
        "constraints": {"limit": 6},
        "intent": "catalog",
        "references": [],
        "known_information": [],
        "missing_information": "",
        "next_action": "plan",
    }
    plan.update(over)
    return plan


def test_extract_json_plain():
    raw = '{"tool": "search_offers", "args": {}}'
    assert parse_plan(raw, TOOLS)["tool"] == "search_offers"


def test_extract_json_fenced():
    raw = 'Here you go:\n```json\n{"tool": "get_offer", "args": {"id": "10"}}\n```\nbye'
    assert parse_plan(raw, TOOLS)["args"]["id"] == "10"


def test_extract_json_with_prose():
    raw = 'I think {"tool": "none", "next_action": "respond_one", "goal": "greet"} is best.'
    plan = parse_plan(raw, TOOLS)
    assert plan["tool"] == "none"


def test_garbage_rejected():
    with pytest.raises(PlanParseError):
        parse_plan("let me think about this", TOOLS)


def test_broken_json_rejected():
    with pytest.raises(PlanParseError):
        parse_plan('{"tool": "search_offers", "args": ', TOOLS)


def test_unknown_tool_rejected():
    with pytest.raises(PlanParseError):
        parse_plan(_valid_json(_valid_plan(tool="drop_database")), TOOLS)


def test_bad_next_action_rejected():
    with pytest.raises(PlanParseError):
        parse_plan(_valid_json(_valid_plan(next_action="ignore_business_rules")), TOOLS)


def test_args_must_be_object():
    with pytest.raises(PlanParseError):
        parse_plan(_valid_json(_valid_plan(args=[1, 2, 3])), TOOLS)


def test_english_arabic_fields_are_decoded():
    plan = parse_plan(_valid_json(_valid_plan(
        references=["that coupon"],
        known_information=["merchant=KFC"],
        missing_information="price range",
    )), TOOLS)
    assert plan["references"] == ["that coupon"]
    assert plan["known_information"] == ["merchant=KFC"]
    assert plan["missing_information"] == "price range"


def test_references_are_string_listed():
    plan = parse_plan(_valid_json(_valid_plan(references="that coupon")), TOOLS)
    assert plan["references"] == ["that coupon"]


def test_plan_prompt_mentions_tools_and_query():
    prompt = build_plan_prompt(
        "what offers does KFC have", {
            "reply_lang": "en", "detected_intent": "catalog",
            "conversation": "", "references": [], "recent_offers": [],
        },
        tool_names=TOOLS,
        tool_docs="- search_offers(...) -- Find Waffarha offers",
    )
    assert "KFC" in prompt
    assert "search_offers" in prompt
    assert "next_action" in prompt


def test_plan_prompt_has_unclear_outcome():
    """The planner schema must offer a genuine low-signal outcome, distinct
    from the catalog browse default, and forbid catalog-dump for it."""
    prompt = build_plan_prompt(
        "w", {
            "reply_lang": "en", "detected_intent": "catalog",
            "conversation": "", "references": [], "recent_offers": [],
        },
        tool_names=TOOLS,
        tool_docs="- search_offers(...) -- Find Waffarha offers",
    )
    assert '"unclear"' in prompt
    assert "NEVER turn an unclear message into intent" in prompt
    assert "catalog listing is only the right plan for" in prompt


def test_parse_plan_accepts_unclear_intent():
    plan = parse_plan(_valid_json(_valid_plan(intent="unclear")), TOOLS)
    assert plan["intent"] == "unclear"


def _valid_json(plan):
    import json
    return json.dumps(plan, ensure_ascii=False)