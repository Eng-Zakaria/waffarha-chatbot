"""
Session-scoped conversation memory: for each session_id, keeps:
  1. The last few conversation turns (user/assistant), most-recent-first
  2. The last few offers/FAQs that were actually SHOWN to the user

Why this exists instead of just relying on client-side localStorage:
- The frontend persists chat history in localStorage, but that can be lost
  if the user clears browser data, switches devices, or if Redis restarts.
- Server-side memory guarantees the RAG engine always has conversation
  context for follow-up resolution (e.g. "how much was it before discount?")
  and for building the LLM prompt with proper history.
- Offer memory (structured data) is separate from text history because
  template-built direct answers never touch the LLM, so relying on LLM
  context alone isn't enough for precise follow-ups.

Public interface: MemoryStore().get(session_id) returns an object with:
  - .remember_turns(user_msg, assistant_msg)  — store a completed turn pair
  - .get_turns(n)                              — get last N turns, most-recent-first
  - .remember(docs)                            — store the last N offer/FAQ items shown
  - .recent(n)                                 — get recent offers/FAQs for follow-up resolution
"""
import json
import threading
import time
from typing import Optional
import sys
import os

# Add project root to path so 'core' package can be found
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import config

MAX_OFFERS_PER_SESSION = 4      # remember the last 3 or 4 offers
MAX_TURNS_PER_SESSION = 10     # keep last 10 user+assistant pairs (20 messages)
SESSION_TTL_SECONDS = 60 * 60   # evict a session after an hour of inactivity

# Config override support
from core import config
if hasattr(config, 'MAX_SERVER_TURNS'):
    MAX_TURNS_PER_SESSION = config.MAX_SERVER_TURNS


def _key(session_id: str) -> str:
    return f"waffarha:session:{session_id}"


def _turns_key(session_id: str) -> str:
    return f"{_key(session_id)}:turns"


def _offers_key(session_id: str) -> str:
    return f"{_key(session_id)}:offers"


def _extract_items(docs: list) -> list:
    """Shared shaping logic for both backends, so they can't silently drift
    on what counts as a "rememberable" doc or what gets stored for it.

    docs: list of retrieved-doc dicts in RagEngine's shape, i.e. each one
    has a "metadata" key (this is exactly the shape of result["sources"]
    from RagEngine.answer()). Only offer/FAQ entries are kept; anything
    else is ignored. Callers should pass what was actually shown/used in
    the answer (e.g. raw_sources[:3]), not the full internal candidate
    pool, so memory reflects what the user actually saw, not everything the
    retriever merely considered.
    """
    items = []
    for doc in docs:
        meta = doc.get("metadata", {}) or {}
        source = meta.get("source")
        if source not in ("offer", "faq"):
            continue
        items.append({
            "id": f"{source}:{meta.get('id')}",
            "metadata": meta,
            "shown_at": time.time(),
        })
    return items


def _merge(items: list, existing: list) -> list:
    """`items` is already highest-relevance-first for this turn (callers
    pass raw_sources[:3] in that order). Drop any existing entries that
    duplicate an id in this turn, so re-showing an offer moves it to the
    front instead of creating a duplicate, then prepend `items` ahead of
    `existing` for "most-recent-turn-first, most-relevant-within-turn-first"
    ordering, capped at MAX_OFFERS_PER_SESSION."""
    new_ids = {it["id"] for it in items}
    existing = [e for e in existing if e.get("id") not in new_ids]
    return (items + existing)[:MAX_OFFERS_PER_SESSION]


class RedisSessionMemory:
    """Thin, stateless-in-Python wrapper bound to one session_id -- all
    actual data lives in Redis. Cheap to construct per-request."""

    def __init__(self, client: "redis.Redis", session_id: str):
        self._r = client
        self._session_id = session_id

    def remember_turns(self, user_msg: str, assistant_msg: str):
        """Store a completed conversation turn pair. Most-recent-first,
        capped at MAX_TURNS_PER_SESSION. Each turn is stored as a JSON
        object with role/content for both user and assistant."""
        turn = {
            "role": "user",
            "content": user_msg,
            "timestamp": time.time(),
        }
        assistant_turn = {
            "role": "assistant",
            "content": assistant_msg,
            "timestamp": time.time(),
        }
        key = _turns_key(self._session_id)
        existing_raw = self._r.lrange(key, 0, -1)
        existing = []
        for x in existing_raw:
            try:
                existing.append(json.loads(x))
            except (json.JSONDecodeError, TypeError):
                continue
        # Prepend new turns, keep most-recent-first
        merged = ([assistant_turn, turn] + existing)[:MAX_TURNS_PER_SESSION * 2]
        pipe = self._r.pipeline()
        pipe.delete(key)
        pipe.rpush(key, *[json.dumps(m) for m in merged])
        pipe.expire(key, SESSION_TTL_SECONDS)
        pipe.execute()

    def get_turns(self, n: int = None) -> list:
        """Returns the last N conversation turns, most-recent-first, in
        the format expected by RagEngine: [{"role": "user/assistant", "content": "..."}].
        """
        key = _turns_key(self._session_id)
        stop = (n - 1) if n else -1
        raw = self._r.lrange(key, 0, stop)
        out = []
        for x in raw:
            try:
                parsed = json.loads(x)
                if isinstance(parsed, dict) and "role" in parsed and "content" in parsed:
                    out.append({"role": parsed["role"], "content": parsed["content"]})
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def remember(self, docs: list):
        """Store the last N offer/FAQ items that were actually shown.
        Most-recent-first, capped at MAX_OFFERS_PER_SESSION. De-dupe +
        reorder happens in Python (read-modify-write)."""
        items = _extract_items(docs)
        if not items:
            return

        existing_raw = self._r.lrange(_offers_key(self._session_id), 0, -1)
        existing = []
        for x in existing_raw:
            try:
                existing.append(json.loads(x))
            except (json.JSONDecodeError, TypeError):
                continue
        merged = _merge(items, existing)

        pipe = self._r.pipeline()
        pipe.delete(_offers_key(self._session_id))
        pipe.rpush(_offers_key(self._session_id), *[json.dumps(m) for m in merged])
        pipe.expire(_offers_key(self._session_id), SESSION_TTL_SECONDS)
        pipe.execute()

    def recent(self, n: int = None) -> list:
        """Returns the last-shown offers/FAQs, most-recent-first, in the
        same {"metadata": {...}} shape RagEngine's retrieved docs use."""
        stop = (n - 1) if n else -1
        raw = self._r.lrange(_offers_key(self._session_id), 0, stop)
        out = []
        for x in raw:
            try:
                parsed = json.loads(x)
                if isinstance(parsed, dict) and "metadata" in parsed:
                    out.append({"metadata": parsed["metadata"]})
            except (json.JSONDecodeError, TypeError):
                continue
        return out


class LocalSessionMemory:
    """Same interface/behavior as RedisSessionMemory, but backed by a plain
    dict on the shared _LocalBackend instead of a Redis key. No JSON
    round-trip needed since there's no network boundary -- items are stored
    as the actual Python dicts.

    Same race-condition tradeoff as the Redis version applies here too, just
    guarded with a plain Lock instead of a Redis pipeline: two concurrent
    remember() calls for the same session_id could still race the
    read-modify-write, so the lock is held across the whole thing to at
    least make each call atomic with respect to the others.
    """

    def __init__(self, backend: "_LocalBackend", session_id: str):
        self._backend = backend
        self._session_id = session_id

    def remember_turns(self, user_msg: str, assistant_msg: str):
        """Store a completed conversation turn pair."""
        turn = {
            "role": "user",
            "content": user_msg,
            "timestamp": time.time(),
        }
        assistant_turn = {
            "role": "assistant",
            "content": assistant_msg,
            "timestamp": time.time(),
        }
        with self._backend.lock:
            entry = self._backend.store.get(self._session_id)
            if entry is None:
                turns = []
            else:
                turns = entry.get("turns", [])
            merged = ([assistant_turn, turn] + turns)[:MAX_TURNS_PER_SESSION * 2]
            if entry is None:
                entry = {}
            entry["turns"] = merged
            entry["expires_at"] = time.time() + SESSION_TTL_SECONDS
            self._backend.store[self._session_id] = entry

    def get_turns(self, n: int = None) -> list:
        """Returns the last N conversation turns, most-recent-first."""
        with self._backend.lock:
            entry = self._backend.store.get(self._session_id)
            if entry is None:
                return []
            if time.time() > entry.get("expires_at", 0):
                del self._backend.store[self._session_id]
                return []
            turns = entry.get("turns", [])
        sliced = turns if n is None else turns[:n]
        return [{"role": t["role"], "content": t["content"]} for t in sliced]

    def remember(self, docs: list):
        items = _extract_items(docs)
        if not items:
            return
        with self._backend.lock:
            entry = self._backend.store.get(self._session_id)
            if entry is None:
                existing = []
            else:
                existing = entry.get("offers", [])
            merged = _merge(items, existing)
            if entry is None:
                entry = {}
            entry["offers"] = merged
            entry["expires_at"] = time.time() + SESSION_TTL_SECONDS
            self._backend.store[self._session_id] = entry

    def recent(self, n: int = None) -> list:
        with self._backend.lock:
            entry = self._backend.store.get(self._session_id)
            if entry is None:
                return []
            if time.time() > entry.get("expires_at", 0):
                # Lazy eviction: cheap stand-in for Redis's key TTL. Only
                # checked on access, so an idle session's memory for a
                # single-process dev run just sits there unused until either
                # read again (and evicted here) or the process restarts.
                del self._backend.store[self._session_id]
                return []
            items = entry.get("offers", [])
        sliced = items if n is None else items[:n]
        return [{"metadata": it["metadata"]} for it in sliced]


class _LocalBackend:
    """Holds the actual in-process state for the "local" backend: a plain
    dict of session_id -> {"turns": [...], "offers": [...], "expires_at": ...},
    guarded by a lock. Exists only so LocalSessionMemory instances constructed
    for the same MemoryStore share the same dict."""

    def __init__(self):
        self.store: dict = {}
        self.lock = threading.Lock()

    def get(self, session_id: str) -> LocalSessionMemory:
        return LocalSessionMemory(self, session_id)


class _RedisBackend:
    """Holds the actual Redis connection for the "redis" backend. Import of
    the `redis` package is deferred to here (rather than module top-level)
    so the "local" backend -- and this whole module, at import time -- works
    even in an environment that never installed/needs the redis package."""

    def __init__(self, redis_url: Optional[str] = None):
        import redis
        self._r = redis.from_url(redis_url or config.REDIS_URL, decode_responses=True)
        # Fail fast if Redis isn't reachable, same spirit as RagEngine's
        # Ollama check in rag_engine.py -- a clear error at startup beats a
        # mysterious failure on the first chat request.
        self._r.ping()

    def get(self, session_id: str) -> RedisSessionMemory:
        return RedisSessionMemory(self._r, session_id)


class MemoryStore:
    """Process-wide handle to session memory. Picks a backend at
    construction time and delegates to it for the rest of its life --
    callers (app.py) don't need to know or care which one is active.

    backend: "redis" (default) or "local". Falls back to
    config.MEMORY_BACKEND when not given, so the normal way to switch is
    setting MEMORY_BACKEND=local in your .env rather than passing this
    explicitly -- the explicit param mainly exists for tests that want to
    force one backend regardless of the environment.

      - "redis": shared across workers/replicas, survives restarts.
        Requires a reachable Redis server -- connects (and .ping()s) here
        in __init__, so a bad/missing Redis fails fast at construction,
        same as before.
      - "local": plain in-process dict, no server, no `redis` package import
        even attempted. Use this to run/test the app without Redis. NOT
        shared across workers/replicas and NOT persisted across restarts --
        single-process/dev-only, same caveat the old pre-Redis version had.
    """

    def __init__(self, redis_url: Optional[str] = None, backend: Optional[str] = None):
        backend = (backend or config.MEMORY_BACKEND).lower()
        if backend == "local":
            self._impl = _LocalBackend()
        elif backend == "redis":
            self._impl = _RedisBackend(redis_url)
        else:
            raise ValueError(
                f"Unknown MEMORY_BACKEND {backend!r}; expected 'redis' or 'local'"
            )
        self.backend = backend

    def get(self, session_id: str):
        return self._impl.get(session_id)