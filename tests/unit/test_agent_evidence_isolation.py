#!/usr/bin/env python3
"""Regression tests for the per-call evidence contract (FIX 1).

The agent published retrieval evidence through the shared singleton attribute
`AgentEngine._last_evidence`, read back by handlers AFTER the turn finished.
Any turn that returns WITHOUT going through `_finalize` -- planner-error /
budget / retrieval-blocked fallback and zero-card replies -- leaves that slot
STALE, so the NEXT request's cards/sources are assembled from the PREVIOUS
request's evidence (the QA log's T18/T19 freeze signature: a pizza-answer whose
catalog cards showed the previous circus-turn offers).

Fix: every turn carries its OWN evidence (`out["evidence"]`), delivered inside
the streaming `agent_turn_report` piece, so handlers assemble cards from the
current turn's data only.

This file asserts the new streaming contract too: the report piece MUST include
an 'evidence' key (the old engine never set it), which is exactly why the pre-
fix test run fails.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent.engine import AgentEngine
from agent.tools import ToolRegistry
from agent.tools.catalog_tools import SearchOffersTool
from tests.unit.test_agent_engine import (
    _FakeFacade,
    _FakeFaceted,
    _FakePlanner,
)


class _EmptyRetrievalFacade(_FakeFacade):
    """Facade whose semantic retrieval returns nothing, so a planner-error
    fallback turn has ZERO evidence of its own -- proving it can never borrow
    the previous turn's slot contents."""

    def retrieve(self, query, top_k=None, history=None, recent_offers=None,
                 normalized_query=None):
        return []


def _make_engine(facade=None):
    facade = facade or _FakeFacade(_FakeFaceted())
    reg = ToolRegistry([SearchOffersTool()])
    return AgentEngine(facade, reg, planner=_FakePlanner())


def _stream(engine, query, **kw):
    """Collect answer + per-call evidence exactly like /api/agent/chat does."""
    answer = ""
    evidence = None
    for piece in engine.answer_stream(query, reply_lang="en", **kw):
        if isinstance(piece, str):
            answer = piece
        elif isinstance(piece, dict) and piece.get("kind") == "agent_turn_report":
            assert "evidence" in piece, (
                "agent_turn_report must carry per-call evidence "
                "(stale shared-slot leak regression)"
            )
            evidence = piece.get("evidence") or []
    assert evidence is not None, "answer_stream must yield an agent_turn_report piece"
    return answer, evidence


def _offer_ids(evidence):
    return sorted(
        str((d.get("metadata") or {}).get("id"))
        for d in evidence
        if (d.get("metadata") or {}).get("source") == "offer"
    )


def test_report_piece_carries_this_turns_evidence():
    engine = _make_engine()
    _, evidence = _stream(engine, "show me KFC offers")
    assert _offer_ids(evidence) == ["10", "11"]


def test_fallback_turn_returns_its_own_evidence_not_stale_slot():
    """Production-singleton engine: turn 1 leaves the shared slot full of KFC
    offers; turn 2 is a planner-error fallback whose OWN retrieval returns
    nothing. The client must see ZERO cards for turn 2 -- never turn 1's."""
    engine = _make_engine(facade=_EmptyRetrievalFacade(_FakeFaceted()))
    _, ev1 = _stream(engine, "show me KFC offers")
    assert _offer_ids(ev1) == ["10", "11"]

    engine._planner = _FakePlanner(error=True)   # PlanParseError path
    _, ev2 = _stream(engine, "is there still a pizza deal?")
    assert _offer_ids(ev2) == []
    assert ev2 == []


def test_concurrent_turns_keep_separate_evidence():
    engine = _make_engine()
    barrier = threading.Barrier(2)
    results = {}

    def run(name, query):
        barrier.wait()
        _, evidence = _stream(engine, query)
        results[name] = _offer_ids(evidence)

    a = threading.Thread(target=run, args=("A", "show me KFC offers"))
    b = threading.Thread(target=run, args=("B", "KFC zinger deals"))
    a.start()
    b.start()
    a.join()
    b.join()

    assert results == {"A": ["10", "11"], "B": ["10", "11"]}