"""
Waffarha Assistant -- agent backend (standalone server).

Same widget, same /api/chat + /api/chat/stream endpoints, same static/
serving -- but wired to the AgentEngine (planner → typed tools →
deterministic renderer) instead of the old RagEngine cascade.  The widget
uses ``API_BASE = ''`` (relative fetches), so served from a different port
it calls that port's /api/chat automatically.

Run (port 8001):
    ollama serve
    uvicorn core.agent_server:app --port 8001

Then open http://localhost:8001 -- identical UI to :8000, but powered by
the agentic layer.
"""
import asyncio
import json
import logging
import queue as pyqueue

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from core import config
from core.app import (
    ChatRequest,
    ChatResponse,
    ServerBusyError,
    _acquire_generation_slot,
    _agent_evidence,
    _agent_identity,
    _agent_status_text,
    _build_suggestions,
    _chunk_text,
    _memory_session_key,
    _offer_cards,
    _release_generation_slot,
    _source_card,
    get_agent_engine_async,
    get_identity,
    get_memory_store_async,
)
from core.rag_engine import _strip_scaffolding_leaks, detect_lang

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("waffarha-agent")

app = FastAPI(title="Waffarha Agent Assistant")

SSE_HEARTBEAT = "\n\n"

# Import RedisError with fallback (same pattern as core/app.py)
try:
    from redis.exceptions import RedisError
except ImportError:
    class RedisError(Exception):
        pass


async def _resolve_request_context(req):
    user_id = None
    if config.PERSONAL_QUERIES_ENABLED:
        try:
            user_id = await asyncio.to_thread(get_identity().resolve, req)
        except Exception as e:  # noqa: BLE001
            log.warning("identity resolution failed: %s", e)
            user_id = None
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")

    try:
        memory_store = await get_memory_store_async()
    except RedisError as e:
        log.warning("Redis unavailable, continuing without session memory: %s", e)
        memory_store = None

    session = memory_store.get(_memory_session_key(req.session_id, user_id)) if memory_store else None
    try:
        recent_offers = (
            await asyncio.to_thread(session.recent) if session else []
        )
    except RedisError as e:
        log.warning("Redis unavailable while reading session memory; continuing without it: %s", e)
        session = None
        recent_offers = []

    history = [{"role": t.role, "content": t.content} for t in req.history]
    reply_lang = detect_lang(query)
    return user_id, query, session, recent_offers, history, reply_lang


def _session_remember(session, raw_sources, query, bot_answer):
    if not session:
        return
    try:
        session.remember(raw_sources[:3])
        session.remember_turns(query, bot_answer)
    except RedisError as e:
        log.warning("Redis unavailable while saving session memory; continuing without it: %s", e)


# ---------------------------------------------------------------------------
# Warm the engine at startup (same as core/app.py) so the first request
# doesn't pay for model + index loading.
# ---------------------------------------------------------------------------
@app.on_event("startup")
async def _warm_up():
    log.info("Agent server: warming up AgentEngine (loads shared RagEngine)...")
    await get_agent_engine_async()
    log.info("Agent server: AgentEngine ready.")


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    user_id, query, session, recent_offers, history, reply_lang = (
        await _resolve_request_context(req)
    )

    try:
        engine = await get_agent_engine_async()
    except FileNotFoundError as e:
        log.error("Index not found: %s", e)
        raise HTTPException(503, str(e))
    except RuntimeError as e:
        log.error("Ollama unreachable: %s", e)
        raise HTTPException(503, str(e))

    try:
        await _acquire_generation_slot()
    except ServerBusyError:
        raise HTTPException(
            503,
            detail="The assistant is handling a lot of requests right now. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    try:
        bot_answer = ""
        for piece in engine.answer_stream(
                query, reply_lang=reply_lang, history=history,
                recent_offers=recent_offers, user_id=user_id,
                identity=_agent_identity(user_id)):
            if isinstance(piece, str):
                bot_answer = piece
        raw_sources = _agent_evidence(engine)
    except Exception:
        log.exception("agent /api/chat failed for query=%r", query)
        raise HTTPException(500, "The assistant hit an internal error. Please try again.")
    finally:
        _release_generation_slot()

    await asyncio.to_thread(_session_remember, session, raw_sources, query, bot_answer)

    sources = [_source_card(d, reply_lang) for d in raw_sources[:3]]
    suggestions = _build_suggestions(raw_sources, reply_lang)
    offer_cards = _offer_cards(raw_sources[:5], reply_lang)
    resp_type = "offers" if offer_cards else "text"
    return {"answer": bot_answer, "type": resp_type, "offers": offer_cards,
            "sources": sources, "suggestions": suggestions}


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest):
    user_id, query, session, recent_offers, history, reply_lang = (
        await _resolve_request_context(req)
    )

    try:
        engine = await get_agent_engine_async()
    except FileNotFoundError as e:
        log.error("Index not found: %s", e)
        raise HTTPException(503, str(e))
    except RuntimeError as e:
        log.error("Ollama unreachable: %s", e)
        raise HTTPException(503, str(e))

    try:
        await _acquire_generation_slot()
    except ServerBusyError:
        raise HTTPException(
            503,
            detail="The assistant is handling a lot of requests right now. Please retry shortly.",
            headers={"Retry-After": "5"},
        )

    q: pyqueue.Queue = pyqueue.Queue()

    def _run():
        try:
            def _progress(code):
                q.put(("status", code))
            for piece in engine.answer_stream(
                    query, reply_lang=reply_lang, history=history,
                    recent_offers=recent_offers, user_id=user_id,
                    identity=_agent_identity(user_id), progress=_progress):
                if isinstance(piece, str):
                    for chunk in _chunk_text(piece):
                        q.put(("token", {"text": chunk}))
                elif isinstance(piece, dict) and piece.get("kind") == "agent_turn_report":
                    q.put(("report", piece["data"]))
        except Exception as e:  # noqa: BLE001
            q.put(("error", str(e)))
        finally:
            q.put(("_end", None))

    async def event_gen():
        loop = asyncio.get_event_loop()
        thread_task = loop.run_in_executor(None, _run)
        full_text = ""
        try:
            while True:
                kind, payload = await asyncio.to_thread(q.get)
                if kind == "status":
                    yield f"event: status\ndata: {json.dumps({'phase': payload, 'text': _agent_status_text(engine, reply_lang, payload)})}{SSE_HEARTBEAT}"
                    continue
                if kind == "token":
                    full_text += payload.get("text", "")
                    yield f"event: token\ndata: {json.dumps(payload)}{SSE_HEARTBEAT}"
                elif kind == "error":
                    log.exception("agent chat_stream failed for query=%r: %s", query, payload)
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

        raw_sources = _agent_evidence(engine)
        sources = [_source_card(d, reply_lang) for d in raw_sources[:3]]
        suggestions = _build_suggestions(raw_sources, reply_lang)
        offer_cards = _offer_cards(raw_sources[:5], reply_lang)
        resp_type = "offers" if offer_cards else "text"
        yield f"event: meta\ndata: {json.dumps({'type': resp_type, 'offers': offer_cards, 'sources': sources, 'suggestions': suggestions})}{SSE_HEARTBEAT}"

        await asyncio.to_thread(_session_remember, session, raw_sources, query, cleaned)

        yield f"event: done\ndata: {json.dumps({'answer': cleaned})}{SSE_HEARTBEAT}"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# /api/health for the agent server
@app.get("/api/health")
def health():
    return {"status": "ok", "engine": "agent"}


# Static files — registered last so it doesn't shadow /api/* routes.
# Serves the same static/index.html, widget calls /api/chat relative to
# this port (8001) — which hits our agent handler above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
