"""
Waffarha Assistant -- backend.

Wires the chat widget (static/index.html) to RagEngine (rag_engine.py),
which does retrieval over the prebuilt FAISS index in
data/index/<embedding_model>/faiss/ and generation via a local Ollama model.

Run:
    pip install -r requirements.txt
    ollama serve                              # if not already running
    ollama pull qwen2.5:1.5b-instruct          # or whatever OLLAMA_MODEL is set to
    uvicorn app:app --reload --port 8000

Then open http://localhost:8000 -- the widget will be served from static/
and will call this file's /api/chat endpoint for real answers.

Nothing here re-implements retrieval or generation logic -- that all stays
in rag_engine.py exactly as built. This file only exposes it over HTTP and
shapes RagEngine's output into the {answer, sources:[{title,snippet}]}
shape the widget already expects.
"""
import asyncio
import json
import logging
import math
import queue as pyqueue
import random
import re
import threading
from typing import List, Optional

import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from rag_engine import RagEngine, detect_lang, _strip_scaffolding_leaks
from memory import MemoryStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("waffarha-app")

app = FastAPI(title="Waffarha Assistant")

# ---------------------------------------------------------------------------
# Session memory: the last few offers/FAQs actually shown to each
# session_id, most-recent-first (see memory.py for why this is structured
# data rather than just relying on chat history text), now Redis-backed so
# it's consistent across multiple uvicorn workers/replicas and survives
# restarts.
#
# The frontend needs to generate a session_id once per browser tab/user
# (e.g. crypto.randomUUID(), persisted in localStorage) and send it with
# every /api/chat request. Requests with no session_id all share the
# "default" bucket, which is fine for local single-user testing but means
# two different real users would see each other's "remembered" offers --
# wire up a real per-user session_id before more than one person uses this
# at once.
#
# Lazy-loaded the same way as RagEngine below: connecting to Redis at
# import time would mean `docker compose up` fails hard the instant uvicorn
# starts if Redis's healthcheck hasn't passed yet, rather than app.py
# waiting/retrying. depends_on: condition: service_healthy in
# docker-compose.yml already sequences this correctly, but staying lazy
# also means /api/health keeps responding even if Redis is temporarily
# down, instead of the whole process refusing to start.
# ---------------------------------------------------------------------------
_memory: Optional[MemoryStore] = None
_memory_lock = threading.Lock()


def get_memory_store() -> MemoryStore:
    global _memory
    if _memory is not None:
        return _memory
    with _memory_lock:
        if _memory is None:
            log.info("Connecting to Redis (session memory)...")
            _memory = MemoryStore()
            log.info("Redis connected.")
    return _memory


async def get_memory_store_async() -> MemoryStore:
    return await asyncio.to_thread(get_memory_store)

# ---------------------------------------------------------------------------
# CORS: only needed because the frontend can be hosted on a different origin
# than this backend (e.g. GitHub Pages at eng-zakaria.github.io calling a
# backend on Render/Fly/a VPS). Same-origin setups (app.py serving
# static/index.html itself, as below) don't need this at all, but it's
# harmless to leave on. Lock ALLOWED_ORIGINS down to your real Pages URL
# before going further than local testing -- "*" accepts requests from any
# website, which is fine for a public read-mostly FAQ/offers bot but worth
# knowing about.
ALLOWED_ORIGINS = [
    "https://eng-zakaria.github.io",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)

# ---------------------------------------------------------------------------
# RagEngine is loaded lazily, on the first request, not at import time --
# it loads a sentence-transformers model, the FAISS index, and pings Ollama,
# which is slow and would otherwise happen every time `uvicorn --reload`
# restarts the process on a file save. A single shared instance is then
# reused for every request (it's read-only after __init__: retrieve() and
# answer() don't mutate self.docs/self.store).
#
# CONCURRENCY: this function itself now runs off the event loop (see
# get_engine_async below), meaning it can genuinely be entered by two
# request-handling threads at the same instant on a cold server -- the old
# `if _engine is None: _engine = RagEngine()` had no lock, so two concurrent
# first requests would each build a full RagEngine (double model/index load,
# wasted RAM, last-write-wins race on the global). This is the standard
# double-checked-locking pattern: the unlocked check keeps the fast path
# (engine already loaded, the overwhelming majority of requests) lock-free,
# and only the slow "still None" path pays for the lock -- then re-checks
# _engine is None once inside it, so only the first thread to arrive
# actually builds the engine; every other thread that was waiting on the
# lock sees it's already built and returns it instead of building a second.
# ---------------------------------------------------------------------------
_engine: Optional[RagEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> RagEngine:
    global _engine
    if _engine is not None:          # fast path, no lock, the common case
        return _engine
    with _engine_lock:
        if _engine is None:          # re-check: someone else may have built it
            log.info("Loading RagEngine (embedding model + FAISS index + Ollama check)...")
            _engine = RagEngine()
            log.info("RagEngine ready.")
    return _engine


async def get_engine_async() -> RagEngine:
    """get_engine() does blocking I/O (model/index load, a network call to
    Ollama) -- run it off the event loop via to_thread so a slow cold start
    doesn't stall every other request's event-loop-bound work (e.g. routing,
    the /api/health check) while it's happening."""
    return await asyncio.to_thread(get_engine)


@app.on_event("startup")
async def _warm_up_engine():
    """Loads RagEngine (embedding model + FAISS index + Ollama check) as
    soon as the container starts, instead of leaving it to the first real
    request. Restarting the app container doesn't restart Ollama/Redis --
    they're already warm -- but this process's own memory is empty on every
    restart, so someone always used to pay for that first load. Now it's
    paid during startup (visible in orchestration/health-check timing)
    instead of by whichever customer happens to send the first message."""
    log.info("Warming up RagEngine at startup...")
    await get_engine_async()
    log.info("RagEngine warm and ready.")


# ---------------------------------------------------------------------------
# Generation concurrency cap.
#
# Why this exists: chat() below is async and offloads engine.answer() to a
# thread via asyncio.to_thread, so many requests CAN be in flight in this
# process at once as far as Python/FastAPI is concerned. But every one of
# those threads ultimately calls the same local Ollama server, and Ollama's
# own parallelism is capped by OLLAMA_NUM_PARALLEL (default 1) -- requests
# beyond that just queue *inside Ollama*, invisibly, with no timeout. Left
# uncapped, a burst of users produces a pile of requests all silently
# waiting on Ollama, each eventually timing out or hanging the connection
# with no useful error.
#
# Fix: bound how many generations this process will send to Ollama at once
# (MAX_CONCURRENT_GENERATIONS -- set it to match OLLAMA_NUM_PARALLEL on the
# Ollama side, see docker-compose.yml) and give requests that can't get a
# slot within GENERATION_QUEUE_TIMEOUT seconds a fast, honest 503 with
# Retry-After instead of leaving them to hang indefinitely. A user retrying
# a 503 is a much better experience than a spinner that never resolves.
# ---------------------------------------------------------------------------
MAX_CONCURRENT_GENERATIONS = config.MAX_CONCURRENT_GENERATIONS
GENERATION_QUEUE_TIMEOUT = config.GENERATION_QUEUE_TIMEOUT

_generation_semaphore = asyncio.Semaphore(MAX_CONCURRENT_GENERATIONS)
_in_flight = 0                      # for /api/health visibility only
_in_flight_lock = threading.Lock()


class ServerBusyError(Exception):
    """Raised when a request couldn't get a generation slot in time."""


async def _acquire_generation_slot():
    global _in_flight
    try:
        await asyncio.wait_for(
            _generation_semaphore.acquire(), timeout=GENERATION_QUEUE_TIMEOUT
        )
    except asyncio.TimeoutError:
        raise ServerBusyError()
    with _in_flight_lock:
        _in_flight += 1


def _release_generation_slot():
    global _in_flight
    with _in_flight_lock:
        _in_flight -= 1
    _generation_semaphore.release()


class ChatTurn(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    query: str
    lang: str = "en"  # informational only -- RagEngine detects reply language itself
    history: List[ChatTurn] = []
    session_id: str = "default"  # see _memory comment above -- frontend should send a real per-user id


class SourceCard(BaseModel):
    title: str
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceCard]
    suggestions: List[str] = []


def _source_card(doc: dict, reply_lang: str = None) -> dict:
    """Shapes one retrieved doc (RagEngine's internal metadata shape) into
    the {title, snippet} the widget's source chips render."""
    meta = doc.get("metadata", {})
    if meta.get("source") == "faq":
        return {
            "title": meta.get("question") or "FAQ",
            "snippet": meta.get("answer") or "",
        }
    doc_lang = reply_lang or meta.get("lang") or "en"
    currency = config.CURRENCY.get(doc_lang, config.CURRENCY.get("en", "EGP")) if isinstance(config.CURRENCY, dict) else config.CURRENCY
    bits = []
    if meta.get("price"):
        bits.append(f"{meta['price']} {currency}")
    if meta.get("discount"):
        bits.append(f"{meta['discount']}% off" if doc_lang == "en" else f"خصم {meta['discount']}%")
    if meta.get("expiry"):
        bits.append(f"valid until {meta['expiry']}" if doc_lang == "en" else f"صالح حتى {meta['expiry']}")
    return {
        "title": meta.get("title") or meta.get("merchant") or ("Offer" if doc_lang == "en" else "عرض"),
        "snippet": " • ".join(bits) or (meta.get("merchant") or ""),
    }


def _price_ceiling(price_raw) -> Optional[int]:
    """Rounds a price up to a friendly round number for an 'under X' style
    suggestion (135 -> 200, 70 -> 100). Returns None if price isn't a
    parseable number, so callers can skip the price-based suggestion
    entirely rather than showing a broken one."""
    if price_raw is None:
        return None
    digits = re.sub(r"[^\d.]", "", str(price_raw))
    if not digits:
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    if value <= 0:
        return None
    return int(math.ceil(value / 100.0) * 100)


# ---------------------------------------------------------------------------
# Follow-up suggestions: cheap and template-based on purpose -- these are
# derived from what RagEngine actually retrieved for THIS turn (not a second
# LLM call), so they stay fast and never suggest something unrelated to what
# was just discussed. Capped at 3, deduped, language-matched to the reply.
#
# CHANGED: each slot now has a small pool of phrasings (picked with
# random.choice) instead of one fixed string, and the price/category slots
# are built FROM the actual top offer (its real price, its real category)
# instead of a hardcoded "under 200 EGP" -- so two different offers produce
# two different, offer-relevant suggestion sets instead of the same three
# buttons every time.
# ---------------------------------------------------------------------------
def _build_suggestions(raw_sources: list, reply_lang: str) -> List[str]:
    offer_docs = [d for d in raw_sources if d.get("metadata", {}).get("source") == "offer"]
    faq_docs = [d for d in raw_sources if d.get("metadata", {}).get("source") == "faq"]

    suggestions: List[str] = []

    if len(offer_docs) >= 2:
        suggestions.append(random.choice(
            ["Compare these offers", "See how these two stack up"] if reply_lang == "en"
            else ["قارن بين العروض دي", "شوف الفرق بين العروضين"]
        ))

    if offer_docs:
        top_meta = offer_docs[0].get("metadata", {})
        merchant = top_meta.get("merchant")
        category = top_meta.get("category")

        if category:
            suggestions.append(random.choice(
                [f"More {category} offers?", f"Any other {category} deals?"] if reply_lang == "en"
                else [f"في عروض {category} تانية؟", f"فيه عروض {category} تانية؟"]
            ))
        elif merchant:
            suggestions.append(random.choice(
                [f"More offers from {merchant}", f"What else does {merchant} have?"] if reply_lang == "en"
                else [f"في عروض تانية من {merchant}؟", f"{merchant} عندها عروض تانية؟"]
            ))

        ceiling = _price_ceiling(top_meta.get("price"))
        if ceiling:
            suggestions.append(random.choice(
                [f"Any offers under {ceiling} EGP?", f"Cheaper options under {ceiling} EGP?"] if reply_lang == "en"
                else [f"في عروض تحت {ceiling} جنيه؟", f"فيه أرخص من كده تحت {ceiling} جنيه؟"]
            ))
        elif merchant and category:
            # Had a category slot already -- fall back to the merchant one
            # here so we still offer 2-3 distinct suggestions.
            suggestions.append(random.choice(
                [f"More offers from {merchant}", f"What else does {merchant} have?"] if reply_lang == "en"
                else [f"في عروض تانية من {merchant}؟", f"{merchant} عندها عروض تانية؟"]
            ))

    if faq_docs:
        suggestions.append(random.choice(
            ["How do I redeem this?", "How does this work?"] if reply_lang == "en"
            else ["أستخدم العرض ده إزاي؟", "ده بيشتغل إزاي؟"]
        ))

    seen = set()
    deduped = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped[:3]


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")

    try:
        engine = await get_engine_async()
    except FileNotFoundError as e:
        # Index not found at the expected path -- most likely cause during
        # setup, so surface the real message rather than a generic 500.
        log.error("Index not found: %s", e)
        raise HTTPException(503, str(e))
    except RuntimeError as e:
        # RagEngine.__init__ raises this if it can't reach Ollama.
        log.error("Ollama unreachable: %s", e)
        raise HTTPException(503, str(e))

    try:
        memory_store = await get_memory_store_async()
    except redis.exceptions.RedisError as e:
        # Session memory is a nice-to-have (better follow-up resolution),
        # not required to answer -- so a Redis outage degrades the chat
        # (follow-ups like "how much before the discount" may not resolve
        # as precisely) rather than failing the whole request outright.
        log.warning("Redis unavailable, continuing without session memory: %s", e)
        memory_store = None

    session = memory_store.get(req.session_id) if memory_store else None
    try:
        # SessionMemory.recent() performs a Redis read.  Keep that blocking
        # network I/O off the event loop and preserve the documented
        # best-effort behavior when Redis goes away after its initial ping.
        recent_offers = (
            await asyncio.to_thread(session.recent) if session else []
        )
    except redis.exceptions.RedisError as e:
        log.warning("Redis unavailable while reading session memory; continuing without it: %s", e)
        session = None
        recent_offers = []

    # CONCURRENCY: wait for a generation slot (bounded, with a timeout) before
    # calling into Ollama -- see the _generation_semaphore comment above. If
    # the server is genuinely saturated, fail fast with a 503 + Retry-After
    # rather than let the request hang behind an invisible queue inside Ollama.
    try:
        await _acquire_generation_slot()
    except ServerBusyError:
        raise HTTPException(
            503,
            detail="The assistant is handling a lot of requests right now. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    try:
        history = [{"role": t.role, "content": t.content} for t in req.history]
        # NEW: recent_offers is what this session was actually shown before
        # (see memory.py) -- RagEngine uses it to resolve follow-ups like
        # "how much was it before the discount" back to the exact offer,
        # deterministically, instead of hoping embedding similarity alone
        # lands on the right one.
        #
        # CONCURRENCY: engine.answer() is a blocking call (embeds the query,
        # searches FAISS, streams from Ollama) -- run it in a thread so it
        # doesn't block the event loop while it runs, same reasoning as
        # get_engine_async() above.
        result = await asyncio.to_thread(
            engine.answer, query, history, recent_offers
        )
    except Exception:
        log.exception("chat() failed for query=%r", query)
        raise HTTPException(500, "The assistant hit an internal error. Please try again.")
    finally:
        _release_generation_slot()

    raw_sources = result.get("sources", [])

    # NEW: remember only what was actually shown in THIS answer (the top few
    # sources returned to the widget), not the full internal candidate pool
    # -- so memory reflects what the user saw, not everything the retriever
    # merely scored along the way.
    if session:
        try:
            # A failed memory write must not discard an answer that was
            # already generated successfully.  Redis is an enhancement for
            # follow-ups, not a dependency for serving a chat response.
            await asyncio.to_thread(session.remember, raw_sources[:3])
        except redis.exceptions.RedisError as e:
            log.warning("Redis unavailable while saving session memory; continuing without it: %s", e)

    reply_lang = detect_lang(query)
    sources = [_source_card(d, reply_lang) for d in raw_sources[:3]]
    suggestions = _build_suggestions(raw_sources, reply_lang)
    return {"answer": result["answer"], "sources": sources, "suggestions": suggestions}


# ---------------------------------------------------------------------------
# Streaming variant of /api/chat. Same setup/validation/memory/concurrency-
# slot logic as chat() above, but instead of blocking on engine.answer() and
# returning one JSON body, it drains engine.answer_stream() (a plain
# blocking generator -- retrieval, then token-by-token Ollama output) via a
# background thread + queue, and emits Server-Sent Events as pieces arrive:
#
#   event: meta   -- sources + suggestions (sent once, right after the first
#                     token proves retrieve() has finished -- see
#                     RagEngine.answer_stream, which sets self._last_retrieved
#                     before its first yield)
#   event: token  -- one piece of generated text, repeated many times
#   event: error  -- generation failed partway through
#   event: done   -- final cleaned answer (scaffolding-leak-stripped) + saves
#                     session memory
#
# Why the leak-strip can't happen per-token: _strip_scaffolding_leaks() does
# exact substring matching against markers like "REQUIRED FACTS:", which
# Ollama's streaming chunks can easily split across two yields. So raw
# tokens stream for the live typing effect, and the frontend replaces the
# bubble's content with `done.answer` once it arrives -- identical to what
# streamed in the overwhelming majority of cases, minus any rare leaked
# marker.
# ---------------------------------------------------------------------------
SSE_HEARTBEAT = "\n\n"


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")

    try:
        engine = await get_engine_async()
    except FileNotFoundError as e:
        log.error("Index not found: %s", e)
        raise HTTPException(503, str(e))
    except RuntimeError as e:
        log.error("Ollama unreachable: %s", e)
        raise HTTPException(503, str(e))

    try:
        memory_store = await get_memory_store_async()
    except redis.exceptions.RedisError as e:
        log.warning("Redis unavailable, continuing without session memory: %s", e)
        memory_store = None

    session = memory_store.get(req.session_id) if memory_store else None
    try:
        recent_offers = (
            await asyncio.to_thread(session.recent) if session else []
        )
    except redis.exceptions.RedisError as e:
        log.warning("Redis unavailable while reading session memory; continuing without it: %s", e)
        session = None
        recent_offers = []

    try:
        await _acquire_generation_slot()
    except ServerBusyError:
        raise HTTPException(
            503,
            detail="The assistant is handling a lot of requests right now. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    history = [{"role": t.role, "content": t.content} for t in req.history]
    reply_lang = detect_lang(query)

    # engine.answer_stream() is a plain blocking generator -- it can't be
    # awaited or iterated directly on the event loop without stalling every
    # other request. Run it in a worker thread; tokens cross into the async
    # world via a thread-safe queue that event_gen() drains below.
    q: "pyqueue.Queue" = pyqueue.Queue()

    def _run():
        try:
            for piece in engine.answer_stream(query, history, recent_offers):
                q.put(("token", piece))
        except Exception as e:
            q.put(("error", str(e)))
        finally:
            q.put(("_end", None))

    async def event_gen():
        loop = asyncio.get_event_loop()
        thread_task = loop.run_in_executor(None, _run)

        full_text = ""
        sources_sent = False
        try:
            while True:
                kind, payload = await asyncio.to_thread(q.get)

                # Fire the sources/suggestions event once -- as soon as the
                # first token (or an immediate end/error) proves retrieve()
                # has already run, since RagEngine sets _last_retrieved
                # before its first yield.
                if not sources_sent and kind in ("token", "_end"):
                    raw_sources = getattr(engine, "_last_retrieved", [])
                    sources = [_source_card(d, reply_lang) for d in raw_sources[:3]]
                    suggestions = _build_suggestions(raw_sources, reply_lang)
                    yield f"event: meta\ndata: {json.dumps({'sources': sources, 'suggestions': suggestions})}{SSE_HEARTBEAT}"
                    sources_sent = True

                if kind == "token":
                    full_text += payload
                    yield f"event: token\ndata: {json.dumps({'text': payload})}{SSE_HEARTBEAT}"
                elif kind == "error":
                    log.exception("chat_stream failed for query=%r: %s", query, payload)
                    yield f"event: error\ndata: {json.dumps({'message': 'The assistant hit an internal error. Please try again.'})}{SSE_HEARTBEAT}"
                    break
                elif kind == "_end":
                    break
        finally:
            _release_generation_slot()
            await thread_task

        cleaned, leaked = _strip_scaffolding_leaks(full_text)
        if leaked:
            log.warning("Scaffolding leak stripped post-stream for query=%r: %r", query, leaked)

        raw_sources = getattr(engine, "_last_retrieved", [])
        if session:
            try:
                # Same best-effort behavior as chat(): a failed memory write
                # must not discard an answer that already streamed to the user.
                await asyncio.to_thread(session.remember, raw_sources[:3])
            except redis.exceptions.RedisError as e:
                log.warning("Redis unavailable while saving session memory; continuing without it: %s", e)

        yield f"event: done\ndata: {json.dumps({'answer': cleaned})}{SSE_HEARTBEAT}"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Only matters if you later put nginx in front of app.py -- your
            # current docker-compose exposes 8000 directly with no reverse
            # proxy, so this is a no-op today but keeps SSE from getting
            # silently buffered into one big chunk if that changes.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
def health():
    """Cheap liveness check that does NOT load the engine, so it stays fast
    even before the model/index/Ollama are ready -- point uptime checks here.
    in_flight/capacity let you see queueing pressure (e.g. in a dashboard or
    `watch curl`) without needing to grep logs for 503s."""
    with _in_flight_lock:
        current = _in_flight
    return {
        "status": "ok",
        "engine_loaded": _engine is not None,
        "memory_connected": _memory is not None,
        "generation_in_flight": current,
        "generation_capacity": MAX_CONCURRENT_GENERATIONS,
    }


# Serves static/index.html at "/" and static/favicon.* alongside it.
# Registered last so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")