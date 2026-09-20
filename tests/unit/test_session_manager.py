#!/usr/bin/env python3
"""Fast unit tests for the Redis-optional SessionManager
(session/session_manager.py).

No Redis server needed for the "local" backend tests, and module import
asserted to NOT depend on the redis package (the whole point of the Phase 2
refactor -- catalog comparisons used to raise on first use when running
with MEMORY_BACKEND=local).
"""
import os
import sys

os.environ.setdefault("WAFFARHA_ENV", "test")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from core import config
from session import session_manager as sm_module
from session.session_manager import SessionManager


def _offer(oid, en, ar):
    return {"offer_id": oid, "part_name_en": en, "part_name_ar": ar, "title": f"title-{oid}"}


def test_module_import_does_not_require_redis():
    """Regression guard for the Phase 2 fix: no top-level `import redis`."""
    assert not hasattr(sm_module, "redis")


# --- Local backend (no Redis anywhere) --------------------------------------


def test_local_create_and_empty_session():
    sm = SessionManager(backend="local")
    sid = sm.create_session()
    assert sid
    assert sm.get_session(sid) == {"offers": []}
    assert sm.get_session("unknown-session") == {"offers": []}


def test_local_add_offers_dedupes_by_offer_id():
    sm = SessionManager(backend="local")
    sid = sm.create_session()
    sm.add_offers_to_session(sid, [_offer(1, "KFC", "كنتاكي")])
    sm.add_offers_to_session(sid, [_offer(1, "KFC", "كنتاكي"), _offer(2, "Pizza Hut", "بيتزا هت")])
    offers = sm.get_session_offers(sid)
    assert {o["offer_id"] for o in offers} == {1, 2}
    assert len(offers) == 2


def test_local_add_offers_empty_is_noop():
    sm = SessionManager(backend="local")
    sid = sm.create_session()
    sm.add_offers_to_session(sid, [])
    assert sm.get_session(sid) == {"offers": []}


def test_local_merchant_lookup_matches_en_and_ar():
    sm = SessionManager(backend="local")
    sid = sm.create_session()
    sm.add_offers_to_session(
        sid, [_offer(1, "KFC", "كنتاكي"), _offer(2, "Ali Baba", "علي بابا")]
    )
    by_en = sm.get_offers_for_merchants(sid, ["kfc"])
    by_ar = sm.get_offers_for_merchants(sid, ["كنتاكي"])
    assert len(by_en["kfc"]) == 1
    assert len(by_ar["كنتاكي"]) == 1
    assert by_en["kfc"][0]["offer_id"] == 1
    assert by_ar["كنتاكي"][0]["offer_id"] == 1


def test_local_merchant_lookup_partial_en_containment():
    # A merchant query that is a substring of the stored name ("baba" from
    # "Ali Baba") matches via the `merchant_lower in offer_merchant_en` branch.
    sm = SessionManager(backend="local")
    sid = sm.create_session()
    sm.add_offers_to_session(sid, [_offer(9, "Ali Baba", "علي بابا")])
    result = sm.get_offers_for_merchants(sid, ["baba"])
    assert len(result["baba"]) == 1


def test_local_clear_session():
    sm = SessionManager(backend="local")
    sid = sm.create_session()
    sm.add_offers_to_session(sid, [_offer(1, "KFC", "كنتاكي")])
    sm.clear_session(sid)
    assert sm.get_session(sid) == {"offers": []}


def test_local_session_ttl_expiry():
    # Force a zero (actually negative) TTL so the entry expires immediately.
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(sm_module, "SESSION_TTL_SECONDS", -1)
    try:
        sm = SessionManager(backend="local")
        sid = sm.create_session()
        sm.add_offers_to_session(sid, [_offer(1, "KFC", "كنتاكي")])
        assert sm.get_session_offers(sid) == []
    finally:
        monkeypatch.undo()


# --- Backend selection -------------------------------------------------------


def test_unknown_backend_raises():
    with pytest.raises(ValueError):
        SessionManager(backend="definitely-not-a-backend")


def test_config_drives_backend_selection(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_BACKEND", "local")
    assert SessionManager().backend == "local"

    # For the redis path, stub the persistence class so the test never needs
    # a real Redis (this only asserts selection / wiring, not I/O).
    class _StubPersistence:
        def __init__(self, redis_url=None):
            self.redis_url = redis_url

    monkeypatch.setattr(sm_module, "_RedisPersistence", _StubPersistence)
    monkeypatch.setattr(config, "MEMORY_BACKEND", "redis")
    sm = SessionManager()
    assert sm.backend == "redis"
    assert isinstance(sm._persist, _StubPersistence)


def test_explicit_backend_overrides_config(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_BACKEND", "redis")
    sm = SessionManager(backend="local")
    assert sm.backend == "local"