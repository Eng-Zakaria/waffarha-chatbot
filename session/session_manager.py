"""
Session management for tracking offers shown during a conversation.

Provides centralized storage for offers retrieved during a user session,
allowing the compare feature to scope to previously shown offers instead
of retrieving fresh results.

Backend-agnostic, mirroring memory.py: the backend is selected by
config.MEMORY_BACKEND, so a dev box with no Redis runs the same code with
an in-process dict ("local") and a deployment shares state across workers
via Redis ("redis"). The `redis` package is imported lazily, only when the
Redis backend is actually selected -- importing this module (or
catalog_queries, which instantiates SessionManager) never requires redis.
"""
import json
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import config

SESSION_TTL_SECONDS = 3600


class _LocalPersistence:
    """In-process dict stand-in for Redis: same get/setex/delete surface,
    guarded by a lock, with lazy TTL eviction on read. Single-process/dev-
    only, exactly like memory.py's local backend."""

    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.time() > entry["expires_at"]:
                del self._store[key]
                return None
            return entry["value"] if isinstance(entry["value"], str) else None

    def setex(self, key: str, ttl: int, value: str) -> None:
        with self._lock:
            self._store[key] = {"value": value, "expires_at": time.time() + ttl}

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)


class _RedisPersistence:
    """Lazy Redis adapter. The `redis` package is imported here (not at
    module top) so the "local" backend -- and importing this whole module --
    works in environments that never installed or need redis. Follows the
    app's best-effort philosophy: if Redis is down at request time the
    caller's try/except degrades (catalog fallback) instead of blowing up
    at import/construction time."""

    def __init__(self, redis_url: str | None = None):
        import redis
        self._r = redis.from_url(redis_url or config.REDIS_URL, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._r.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._r.setex(key, ttl, value)

    def delete(self, key: str) -> None:
        self._r.delete(key)


class SessionManager:
    def __init__(self, redis_url: str | None = None, backend: str | None = None):
        """Track offers shown per session, usable with or without Redis.

        Args:
            redis_url: Redis connection URL. Defaults to config.REDIS_URL.
                Only used when the Redis backend is active.
            backend: "redis" (default, from config.MEMORY_BACKEND) or
                "local". The normal way to choose is setting
                MEMORY_BACKEND=local in your .env; this param mainly exists
                for tests that want to force a backend regardless of env.
        """
        backend = (backend or config.MEMORY_BACKEND).lower()
        if backend == "local":
            self._persist = _LocalPersistence()
        elif backend == "redis":
            self._persist = _RedisPersistence(redis_url)
        else:
            raise ValueError(
                f"Unknown MEMORY_BACKEND {backend!r}; expected 'redis' or 'local'"
            )
        self.backend = backend
        self.redis_url = redis_url

    def _key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def create_session(self) -> str:
        """Create a new session and return its ID."""
        session_id = str(uuid.uuid4())
        self._persist.setex(self._key(session_id), SESSION_TTL_SECONDS,
                            json.dumps({"offers": []}))
        return session_id

    def add_offers_to_session(self, session_id: str, offers: list[dict]) -> None:
        """Store offers in the session.

        Args:
            session_id: Session identifier
            offers: List of offer dictionaries to store
        """
        if not offers:
            return

        session_data = self.get_session(session_id)
        # Deduplicate offers by offer_id
        existing_ids = {offer.get("offer_id") for offer in session_data.get("offers", [])}
        for offer in offers:
            if offer.get("offer_id") not in existing_ids:
                session_data["offers"].append(offer)

        self._persist.setex(self._key(session_id), SESSION_TTL_SECONDS,
                            json.dumps(session_data))

    def get_session_offers(self, session_id: str) -> list[dict]:
        """Retrieve all offers stored in the session.

        Args:
            session_id: Session identifier

        Returns:
            List of offer dictionaries
        """
        session_data = self.get_session(session_id)
        return session_data.get("offers", [])

    def get_session(self, session_id: str) -> dict:
        """Retrieve full session data.

        Args:
            session_id: Session identifier

        Returns:
            Session data dictionary
        """
        data = self._persist.get(self._key(session_id))
        return json.loads(data) if data else {"offers": []}

    def clear_session(self, session_id: str) -> None:
        """Clear session data.

        Args:
            session_id: Session identifier
        """
        self._persist.delete(self._key(session_id))

    def get_offers_for_merchants(self, session_id: str, merchants: list[str]) -> dict[str, list[dict]]:
        """Get offers from session for specific merchants.

        Args:
            session_id: Session identifier
            merchants: List of merchant names to filter by

        Returns:
            Dictionary mapping merchant names to their offers
        """
        session_offers = self.get_session_offers(session_id)
        merchant_offers = {}

        for merchant in merchants:
            merchant_offers[merchant] = []
            for offer in session_offers:
                # Check both English and Arabic merchant names
                offer_merchant_en = offer.get("part_name_en", "").lower()
                offer_merchant_ar = offer.get("part_name_ar", "").lower()
                merchant_lower = merchant.lower()

                if (offer_merchant_en == merchant_lower or
                    offer_merchant_ar == merchant_lower or
                    merchant_lower in offer_merchant_en or
                    merchant_lower in offer_merchant_ar):
                    merchant_offers[merchant].append(offer)

        return merchant_offers