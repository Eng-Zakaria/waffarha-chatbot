#!/usr/bin/env python3
"""Regression test for the FAQ-topic OVERRIDE wiring (the full _run_turn path).

Repository gap that let the English singular "return policy" ship: existing
tests only exercised the router directly (`_route_faq_topic`). This file drives
the REAL `engine._run_turn` flow -- plan -> FAQ override -> tool -> evidence
gate -> render -- and asserts the override actually REROUTES the plan to
`retrieve_faq` when the planner picked `search_offers`.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.rag_engine import _route_faq_topic
from agent.engine import AgentEngine
from agent.tools import Tool, ToolRegistry, ToolResult
from agent.tools.catalog_tools import SearchOffersTool
from tests.unit.test_agent_engine import _FakeFacade, _FakeFaceted, _FakePlanner


class _FaqFacade(_FakeFacade):
    """Fake facade wired to the REAL FAQ router, so the engine's
    `_facade_faq_topic` override gate exercises production logic."""

    _route_faq_topic = staticmethod(_route_faq_topic)


class _RetrieveFaqTool(Tool):
    name = "retrieve_faq"
    description = "fetch a FAQ/policy document"
    purpose = "answer FAQ/policy questions from the FAQ corpus"

    def run(self, ctx, args):
        doc = {
            "metadata": {
                "source": "faq", "id": "faq_refund_policy", "lang": "en",
                "title": "Return policy", "text": "Returns are handled within 14 days.",
                "question": "What is your return policy?",
            },
            "text": "Returns are handled within 14 days.",
        }
        return ToolResult(ok=True, items=[doc], summary="faq doc",
                          note={"text": "Returns are handled within 14 days."})


def _make_engine():
    facade = _FaqFacade(_FakeFaceted())
    reg = ToolRegistry([SearchOffersTool(), _RetrieveFaqTool()])
    return AgentEngine(facade, reg, planner=_FakePlanner())


def _stream(engine, query):
    answer = ""
    report = None
    evidence = None
    for piece in engine.answer_stream(query, reply_lang="en"):
        if isinstance(piece, str):
            answer = piece
        elif isinstance(piece, dict) and piece.get("kind") == "agent_turn_report":
            report = piece["data"]
            evidence = piece.get("evidence") or []
    assert report is not None, "answer_stream must yield an agent_turn_report piece"
    return answer, report, evidence


def _planned_tool(report):
    metrics = report.get("metrics") or {}
    planned = metrics.get("planned_tool")
    return (report.get("plan") or [{}])[0].get("tool") if planned is None else planned


def _evidence_sources(evidence):
    """Catalog-response role of each evidence doc (offer card vs FAQ answer)."""
    return sorted(
        (d.get("metadata") or {}).get("source") for d in evidence
    )


def test_return_policy_singular_reroutes_to_faq():
    engine = _make_engine()
    answer, report, evidence = _stream(engine, "What's your return policy?")

    assert _planned_tool(report) == "retrieve_faq", (
        "planner picked search_offers but the FAQ override must reroute a "
        "return-policy question to retrieve_faq"
    )
    assert _evidence_sources(evidence) == ["faq"], (
        "turn evidence must be the FAQ doc, not offers"
    )
    assert "14 days" in answer.lower(), (
        f"answer must come from the FAQ doc, got: {answer[:80]!r}"
    )


def test_related_policy_phrasings_still_route_via_override():
    engine = _make_engine()
    for query in ["Do you have a return policy?",
                  "what is your return policy?",
                  "ما هي سياسة الاسترجاع؟"]:
        _, report, _ = _stream(engine, query)
        assert _planned_tool(report) == "retrieve_faq", query


def test_offer_query_is_not_hijacked_by_override():
    engine = _make_engine()
    _, report, evidence = _stream(engine, "KFC offers with discount")
    assert _planned_tool(report) == "search_offers"
    assert _offer_ids(evidence) == ["10", "11"]


def _offer_ids(evidence):
    return sorted(
        str((d.get("metadata") or {}).get("id"))
        for d in evidence
        if (d.get("metadata") or {}).get("source") == "offer"
    )