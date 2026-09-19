#!/usr/bin/env python3
"""Regression tests for the freshness reference date (FIX 3).

The uncommitted freshness gate defaulted to real "today". Once the real clock
moves past every offer's expiry (corpus max expiry 2026-08-31), that default
empties ALL offer answers. Fix: one source of truth -- REFERENCE_DATE env var
(ISO yyyy-mm-dd) -- defaulting to today only when unset.
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from agent.tools import catalog_tools
from agent.tools.catalog_tools import _fresh_only


def _reference_date():
    return catalog_tools._reference_date()


def test_reference_date_reads_env(monkeypatch):
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-31")
    assert _reference_date() == datetime.date(2026, 8, 31)


def test_reference_date_falls_back_to_today(monkeypatch):
    monkeypatch.delenv("REFERENCE_DATE", raising=False)
    assert _reference_date() == datetime.date.today()


def test_reference_date_ignores_invalid_env(monkeypatch):
    monkeypatch.setenv("REFERENCE_DATE", "not-a-date")
    assert _reference_date() == datetime.date.today()


def test_fresh_only_honors_env_reference_date(monkeypatch):
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-31")
    items = [
        {"metadata": {"id": "o1", "expiry": "2026-08-31"}},   # boundary: kept
        {"metadata": {"id": "o2", "expiry": "2026-01-15"}},   # expired: dropped
        {"metadata": {"id": "o3", "expiry": None}},           # unknown: kept
    ]
    kept, dropped = _fresh_only(items)
    assert sorted(i["metadata"]["id"] for i in kept) == ["o1", "o3"]
    assert dropped == 1