"""
Identity resolution for personal-data queries.

The chatbot can answer per-user questions ("show my coupons", "what's the
status of my order") by querying ClickHouse's fct_coupons fact table. But
every such query MUST be scoped to a *trusted* user_id -- never one supplied
by the client.

This module defines the IdentityResolver interface and ships backends:

  - StaticIdentityResolver (IDENTITY_BACKEND=static): returns a fixed,
    configured test user id. LOCAL DEV / DEMO ONLY -- every request resolves
    to the same account, so it is NOT safe for real users.

  - HeaderIdentityResolver (IDENTITY_BACKEND=header): reads user_id from a
    trusted HTTP header (e.g. X-User-ID set by an auth proxy/gateway). This
    is suitable ONLY for deployments where an upstream auth layer (nginx, API
    gateway, service mesh) validates the session and injects the user_id as
    a header before forwarding to this service -- and the service is never
    exposed directly to untrusted clients, or anyone can spoof any user_id.

  - SessionIdentityResolver (IDENTITY_BACKEND=session): reads user_id from
    a server-side session store (Redis) keyed by a session cookie/token.
    Requires a login service + Redis that the chat service shares.

  - AuthBackedIdentityResolver (IDENTITY_BACKEND=auth): THE production-safe
    option. Reads a user_id header PLUS a self-verifying HMAC-SHA256
    signature over that user id and an expiry timestamp, using a shared
    AUTH_SIGNING_SECRET. The signature proves the headers were written by
    whatever holds the secret (the auth layer), so the chat endpoint does
    not need to be firewalled away from the world the way the plain
    "header" backend does. Fails closed -- unset secret, or missing /
    tampered / expired token, all resolve to None (personal queries simply
    get refused).

The security boundary is intentional: the resolver is the ONLY place a
user_id can come from, and personal_queries.py always scopes SQL by whatever
user_id this returns.
"""
import hashlib
import hmac
import logging
import os
import sys
import time

# Add project root to path so 'core' package can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import config

log = logging.getLogger("waffarha-app")


class IdentityResolver:
    """Interface: turns a request into a trusted user_id (int) or None."""

    def resolve(self, request) -> int | None:
        raise NotImplementedError


class StaticIdentityResolver(IdentityResolver):
    """Returns a fixed, configured user_id. LOCAL DEV / DEMO ONLY."""

    def resolve(self, request) -> int | None:
        return config.STATIC_TEST_USER_ID or None


class HeaderIdentityResolver(IdentityResolver):
    """Reads user_id from a trusted HTTP header set by an upstream auth layer.
    
    The header name is configurable via IDENTITY_HEADER (default: X-User-ID).
    The header value must be a valid integer user_id.
    
    SECURITY: This backend assumes the header is ONLY set by a trusted
    upstream component (auth proxy, API gateway, service mesh) that has
    already validated the user's session. Never expose this endpoint directly
    to untrusted clients, or they could spoof any user_id.
    """
    
    def __init__(self):
        self.header_name = config.IDENTITY_HEADER or "X-User-ID"
    
    def resolve(self, request) -> int | None:
        # FastAPI Request object has .headers dict-like access
        header_value = request.headers.get(self.header_name)
        if not header_value:
            log.debug("Identity header %s not present in request", self.header_name)
            return None
        try:
            user_id = int(header_value)
            log.debug("Resolved user_id=%s from header %s", user_id, self.header_name)
            return user_id
        except ValueError:
            log.warning("Invalid user_id in header %s: %r", self.header_name, header_value)
            return None


class SessionIdentityResolver(IdentityResolver):
    """Reads user_id from a server-side session store (Redis) keyed by session cookie/token.
    
    The session cookie/token name is configurable via SESSION_COOKIE_NAME (default: session_id).
    The session store is expected to map session_id -> user_id (as string/int).
    
    This is the production-ready option for cookie-based authentication where
    the session is established by a separate login service and stored in Redis.
    """
    
    def __init__(self):
        self.cookie_name = config.SESSION_COOKIE_NAME or "session_id"
        self._redis = None
    
    def _get_redis(self):
        if self._redis is None:
            import redis
            self._redis = redis.from_url(config.REDIS_URL, decode_responses=True)
        return self._redis

    def resolve(self, request) -> int | None:
        # Try cookie first, then Authorization header as bearer token
        session_id = request.cookies.get(self.cookie_name)
        if not session_id:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                session_id = auth_header[7:]  # strip "Bearer "

        if not session_id:
            log.debug("No session cookie or bearer token found")
            return None

        try:
            redis_client = self._get_redis()
            user_id_str = redis_client.get(f"session:{session_id}")
            if not user_id_str:
                log.debug("Session %s not found or expired", session_id[:8] + "...")
                return None
            user_id = int(user_id_str)
            log.debug("Resolved user_id=%s from session %s", user_id, session_id[:8] + "...")
            return user_id
        except Exception as e:
            log.warning("Session identity resolution failed: %s", e)
            return None


def _compute_signature(secret: str, user_id: str, expires_at: int) -> str:
    """HMAC-SHA256 hex signature over "<user_id>:<expires_at>".

    Used to sign the identity headers (see AuthBackedIdentityResolver and
    make_auth_headers). Keeping it here in the resolver module means the
    signing and verifying sides can never drift apart.
    """
    payload = f"{user_id}:{expires_at}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_auth_headers(user_id, secret: str, expires_at: int) -> dict:
    """Build the two identity headers an auth layer should inject.

    Meant for the auth proxy / login service side (and tests). Drop the
    result onto the upstream request to the chat service:

        headers = make_auth_headers(42, "shared-secret", int(time.time()) + 300)
        # -> {"X-User-ID": "42", "X-User-Auth": "<exp>:<hex-hmac>"}

    The chat service verifies it via AuthBackedIdentityResolver.
    """
    uid = str(user_id)
    return {
        "X-User-ID": uid,
        "X-User-Auth": f"{expires_at}:{_compute_signature(secret, uid, expires_at)}",
    }


class AuthBackedIdentityResolver(IdentityResolver):
    """Production-safe user_id resolution from a self-verifying header pair.

    An upstream auth layer that has already validated the user's session
    injects two headers before forwarding the request:

      X-User-ID:   <user_id>
      X-User-Auth: <expiry_unix_ts>:<hex hmac of "<user_id>:<expiry_unix_ts>">

    signed with the shared AUTH_SIGNING_SECRET (see make_auth_headers for a
    helper). This backend recomputes the signature with hmac.compare_digest
    (constant-time, so length/timing attacks don't leak the secret) and
    checks the expiry is within AUTH_CLOCK_SKEW_SECONDS of the current time.

    Fail-closed by design: if AUTH_SIGNING_SECRET is unset, or the token is
    missing / malformed / tampered / expired, resolve() returns None and the
    personal-data layer simply refuses the query -- never a default user.

    Config:
      IDENTITY_BACKEND=auth
      AUTH_SIGNING_SECRET=<shared secret>          (required; empty => refuse all)
      IDENTITY_HEADER=X-User-ID                     (user id header name)
      AUTH_SIGNATURE_HEADER=X-User-Auth             (signature header name)
      AUTH_CLOCK_SKEW_SECONDS=300                   (expiry tolerance)
    """

    def __init__(self):
        self.user_header = config.IDENTITY_HEADER or "X-User-ID"
        self.auth_header = config.AUTH_SIGNATURE_HEADER or "X-User-Auth"
        self.clock_skew = int(config.AUTH_CLOCK_SKEW_SECONDS or 300)
        self.secret = config.AUTH_SIGNING_SECRET or ""

    def resolve(self, request) -> int | None:
        if not self.secret:
            log.error(
                "IDENTITY_BACKEND=auth requires AUTH_SIGNING_SECRET to be set; "
                "refusing all personal queries (fail-closed)."
            )
            return None

        uid_header = request.headers.get(self.user_header)
        auth_header = request.headers.get(self.auth_header)
        if not uid_header or not auth_header:
            log.debug("Missing identity headers (%s / %s)", self.user_header, self.auth_header)
            return None

        # <expiry>:<signature>
        if ":" not in auth_header:
            log.warning("Malformed %s header", self.auth_header)
            return None
        expires_raw, _, signature = auth_header.partition(":")
        if not expires_raw:
            return None
        try:
            expires_at = int(expires_raw)
        except ValueError:
            log.warning("Non-integer expiry in %s header", self.auth_header)
            return None

        if expires_at < time.time() - self.clock_skew:
            log.warning("Expired identity token (exp=%s)", expires_at)
            return None

        expected = _compute_signature(self.secret, uid_header, expires_at)
        if not hmac.compare_digest(signature.encode("utf-8"), expected.encode("utf-8")):
            log.warning("Invalid identity signature for user header %r", self.user_header)
            return None

        try:
            return int(uid_header)
        except ValueError:
            log.warning("Non-integer user id in %s header", self.user_header)
            return None


def get_identity_resolver() -> IdentityResolver:
    backend = config.IDENTITY_BACKEND
    if backend == "static":
        return StaticIdentityResolver()
    if backend == "header":
        return HeaderIdentityResolver()
    if backend == "session":
        return SessionIdentityResolver()
    if backend == "auth":
        return AuthBackedIdentityResolver()
    raise ValueError(f"Unknown IDENTITY_BACKEND {backend!r}")
