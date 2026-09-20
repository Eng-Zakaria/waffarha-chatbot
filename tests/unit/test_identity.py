#!/usr/bin/env python3
"""Fast unit tests for identity resolution (core/identity.py).

No RagEngine, no Redis, no Ollama -- pure resolver logic with fake request
objects, so they run as part of the fast default suite.
"""
import os
import sys

os.environ.setdefault("WAFFARHA_ENV", "test")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "core")))

import time

import pytest

from core import config
from core.identity import (
    AuthBackedIdentityResolver,
    HeaderIdentityResolver,
    SessionIdentityResolver,
    StaticIdentityResolver,
    _compute_signature,
    get_identity_resolver,
    make_auth_headers,
)


class _FakeRequest:
    """Stand-in for FastAPI's Request: just headers + cookies dicts."""

    def __init__(self, headers: dict | None = None, cookies: dict | None = None):
        self.headers = headers or {}
        self.cookies = cookies or {}


# --- Static -----------------------------------------------------------------


def test_static_resolver_returns_configured_id(monkeypatch):
    monkeypatch.setattr(config, "STATIC_TEST_USER_ID", 55)
    assert StaticIdentityResolver().resolve(_FakeRequest()) == 55


def test_static_resolver_zero_resolves_to_none(monkeypatch):
    monkeypatch.setattr(config, "STATIC_TEST_USER_ID", 0)
    assert StaticIdentityResolver().resolve(_FakeRequest()) is None


# --- Header -----------------------------------------------------------------


def test_header_resolver_valid_int():
    assert HeaderIdentityResolver().resolve(_FakeRequest(headers={"X-User-ID": "42"})) == 42


def test_header_resolver_missing_header():
    assert HeaderIdentityResolver().resolve(_FakeRequest()) is None


def test_header_resolver_non_integer_rejected():
    assert HeaderIdentityResolver().resolve(_FakeRequest(headers={"X-User-ID": "abc"})) is None


def test_header_resolver_custom_header_name(monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_HEADER", "X-Custom-User")
    assert HeaderIdentityResolver().resolve(_FakeRequest(headers={"X-Custom-User": "7"})) == 7


# --- Auth (signed headers) --------------------------------------------------


def _auth_headers_with(secret, uid_header, exp, sig):
    return {"X-User-ID": str(uid_header), "X-User-Auth": f"{exp}:{sig}"}


def test_auth_fail_closed_when_secret_unset(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "")
    # Even with valid-looking headers, no secret => refuse everything.
    headers = make_auth_headers(42, "whatever", int(time.time()) + 300)
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_valid_signature_resolves(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    headers = make_auth_headers(42, "s3cr3t", int(time.time()) + 300)
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) == 42


def test_auth_tampered_user_id_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    exp = int(time.time()) + 300
    headers = make_auth_headers(42, "s3cr3t", exp)
    headers["X-User-ID"] = "43"  # attacker swaps the user id, keeps signature
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_wrong_secret_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "actual-secret")
    headers = make_auth_headers(42, "someone-elses-secret", int(time.time()) + 300)
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_expired_token_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    headers = make_auth_headers(42, "s3cr3t", int(time.time()) - 10000)
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_within_clock_skew_accepted(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    monkeypatch.setattr(config, "AUTH_CLOCK_SKEW_SECONDS", 300)
    headers = make_auth_headers(42, "s3cr3t", int(time.time()) - 100)  # expired, but inside skew
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) == 42


def test_auth_missing_signature_header_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers={"X-User-ID": "42"})) is None


def test_auth_malformed_signature_header_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    headers = _auth_headers_with("s3cr3t", 42, int(time.time()) + 300, "not-a-valid-signature")
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_non_integer_expiry_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    sig = _compute_signature("s3cr3t", "42", int(time.time()) + 300)
    headers = _auth_headers_with("s3cr3t", 42, "not-an-int", sig)
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_non_numeric_user_id_rejected(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    exp = int(time.time()) + 300
    sig = _compute_signature("s3cr3t", "abc", exp)  # signed validly, but not an int uid
    headers = {"X-User-ID": "abc", "X-User-Auth": f"{exp}:{sig}"}
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) is None


def test_auth_custom_header_names(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    monkeypatch.setattr(config, "IDENTITY_HEADER", "X-Custom-User")
    monkeypatch.setattr(config, "AUTH_SIGNATURE_HEADER", "X-Custom-Auth")
    exp = int(time.time()) + 300
    sig = _compute_signature("s3cr3t", "77", exp)
    headers = {"X-Custom-User": "77", "X-Custom-Auth": f"{exp}:{sig}"}
    assert AuthBackedIdentityResolver().resolve(_FakeRequest(headers=headers)) == 77


# --- make_auth_headers helper ------------------------------------------------


def test_make_auth_headers_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "AUTH_SIGNING_SECRET", "s3cr3t")
    exp = int(time.time()) + 300
    headers = make_auth_headers(42, "s3cr3t", exp)
    assert headers["X-User-ID"] == "42"
    resolver = AuthBackedIdentityResolver()
    assert resolver.resolve(_FakeRequest(headers=headers)) == 42


# --- Factory -----------------------------------------------------------------


def test_factory_maps_all_backends(monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_BACKEND", "static")
    assert isinstance(get_identity_resolver(), StaticIdentityResolver)
    monkeypatch.setattr(config, "IDENTITY_BACKEND", "header")
    assert isinstance(get_identity_resolver(), HeaderIdentityResolver)
    monkeypatch.setattr(config, "IDENTITY_BACKEND", "session")
    assert isinstance(get_identity_resolver(), SessionIdentityResolver)
    monkeypatch.setattr(config, "IDENTITY_BACKEND", "auth")
    assert isinstance(get_identity_resolver(), AuthBackedIdentityResolver)


def test_factory_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(config, "IDENTITY_BACKEND", "nope")
    with pytest.raises(ValueError):
        get_identity_resolver()