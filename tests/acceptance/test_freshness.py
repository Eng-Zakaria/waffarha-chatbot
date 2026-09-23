"""Freshness regression tests (Phase 1, fix/routing-and-freshness).

Reads the two freshness trace files produced by the --now-split procedure
(documented in docs/LIVE_DEPS.md):
  1. eval/freshness_show.jsonl      -- show-turn at --now 2026-09-21 (offer live)
  2. eval/freshness_followup.jsonl  -- follow-up at --now 2027-06-01 (offer expired)

Regenerate with:
    $env:MEMORY_BACKEND='redis'; $env:PYTHONIOENCODING='utf-8'
    venv/Scripts/python eval/turn_trace.py --cases eval/freshness_show.json --out eval/freshness_show.jsonl --now 2026-09-21
    venv/Scripts/python eval/turn_trace.py --cases eval/freshness_followup.json --out eval/freshness_followup.jsonl --now 2027-06-01
    venv/Scripts/python -m pytest tests/acceptance/test_freshness.py -q
"""
import json
import os

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOW = os.path.join(BASE, "eval", "freshness_show.jsonl")
FOLLOW = os.path.join(BASE, "eval", "freshness_followup.jsonl")


def _load(path):
    if not os.path.exists(path):
        pytest.skip("trace file missing: %s" % path)
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def test_show_turn_serves_live_offer():
    recs = _load(SHOW)
    assert recs, "no show records"
    for r in recs:
        if r["engine"] != "cascade":
            continue
        assert len(r["cards"]) >= 1, "show turn served no cards"
        assert r["expired_count"] == 0, "show turn served expired cards"


def test_expired_followup_still_resolves():
    """Anchored follow-up about a previously-shown, now-expired offer must
    still resolve (Phase 1 exemption) — here Pizza Hut id 7270, live at
    --now 2026-09-21, expired at --now 2027-06-01."""
    recs = _load(FOLLOW)
    assert recs, "no follow-up records"
    for r in recs:
        if r["engine"] != "cascade":
            continue
        ids = [c.get("id") for c in r["cards"]]
        assert 7270 in ids, "anchored follow-up lost the shown offer: %s (%s)" % (
            ids, r["exit_gate"])
