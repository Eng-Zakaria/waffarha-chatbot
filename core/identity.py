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
    is suitable for deployments where an upstream auth layer (nginx, API
    gateway, service mesh) validates the session and injects the user_id as
    a header before forwarding to this service.

  - SessionIdentityResolver (IDENTITY_BACKEND=session): reads user_id from
    a server-side session store (Redis) keyed by a session cookie/token.
    This is the production-ready option for cookie-based auth.

TODO(production): implement an AuthBackedIdentityResolver that maps the
request's session token / login to a user_id server-side, and set
IDENTITY_BACKEND=auth. That is the ONLY production-safe option.

The security boundary is intentional: the resolver is the ONLY place a
user_id can come from, and personal_queries.py always scopes SQL by whatever
user_id this returns.
"""
import logging
import sys
import os

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


def get_identity_resolver() -> IdentityResolver:
    backend = config.IDENTITY_BACKEND
    if backend == "static":
        return StaticIdentityResolver()
    if backend == "header":
        return HeaderIdentityResolver()
    if backend == "session":
        return SessionIdentityResolver()
    if backend == "auth":
        raise RuntimeError(
            "IDENTITY_BACKEND=auth is not implemented yet. Implement an "
            "AuthBackedIdentityResolver that maps the request's session token "
            "to a user_id server-side before enabling it."
        )
    raise ValueError(f"Unknown IDENTITY_BACKEND {backend!r}")
