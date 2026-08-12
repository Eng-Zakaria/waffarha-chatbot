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
import logging
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rag_engine import RagEngine, detect_lang
from memory import MemoryStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("waffarha-app")

app = FastAPI(title="Waffarha Assistant")

# ---------------------------------------------------------------------------
# Session memory: the last few offers/FAQs actually shown to each
# session_id, most-recent-first (see memory.py for why this is structured
# data rather than just relying on chat history text). One process-wide
# store, one SessionMemory ring-buffer per session_id.
#
# The frontend needs to generate a session_id once per browser tab/user
# (e.g. crypto.randomUUID(), persisted in localStorage) and send it with
# every /api/chat request. Requests with no session_id all share the
# "default" bucket below, which is fine for local single-user testing but
# means two different real users would see each other's "remembered"
# offers -- wire up a real per-user session_id before more than one person
# uses this at once.
# ---------------------------------------------------------------------------
_memory = MemoryStore()

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
# ---------------------------------------------------------------------------
_engine: Optional[RagEngine] = None


def get_engine() -> RagEngine:
    global _engine
    if _engine is None:
        log.info("Loading RagEngine (embedding model + FAISS index + Ollama check)...")
        _engine = RagEngine()
        log.info("RagEngine ready.")
    return _engine


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


def _source_card(doc: dict) -> dict:
    """Shapes one retrieved doc (RagEngine's internal metadata shape) into
    the {title, snippet} the widget's source chips render."""
    meta = doc.get("metadata", {})
    if meta.get("source") == "faq":
        return {
            "title": meta.get("question") or "FAQ",
            "snippet": meta.get("answer") or "",
        }
    bits = []
    if meta.get("price"):
        bits.append(f"{meta['price']} EGP")
    if meta.get("discount"):
        bits.append(f"{meta['discount']}% off")
    if meta.get("expiry"):
        bits.append(f"valid until {meta['expiry']}")
    return {
        "title": meta.get("title") or meta.get("merchant") or "Offer",
        "snippet": " • ".join(bits) or (meta.get("merchant") or ""),
    }


# ---------------------------------------------------------------------------
# Follow-up suggestions: cheap and template-based on purpose -- these are
# derived from what RagEngine actually retrieved for THIS turn (not a second
# LLM call), so they stay fast and never suggest something unrelated to what
# was just discussed. Capped at 3, deduped, language-matched to the reply.
# ---------------------------------------------------------------------------
def _build_suggestions(raw_sources: list, reply_lang: str) -> List[str]:
    offer_docs = [d for d in raw_sources if d.get("metadata", {}).get("source") == "offer"]
    faq_docs = [d for d in raw_sources if d.get("metadata", {}).get("source") == "faq"]

    suggestions: List[str] = []

    if len(offer_docs) >= 2:
        suggestions.append("Compare these offers" if reply_lang == "en" else "قارن بين العروض دي")

    if offer_docs:
        merchant = offer_docs[0].get("metadata", {}).get("merchant")
        if merchant:
            suggestions.append(
                f"More offers from {merchant}" if reply_lang == "en" else f"في عروض تانية من {merchant}؟"
            )
        suggestions.append(
            "Any offers under 200 EGP?" if reply_lang == "en" else "في عروض تحت 200 جنيه؟"
        )

    if faq_docs:
        suggestions.append("How do I redeem this?" if reply_lang == "en" else "أستخدم العرض ده إزاي؟")

    seen = set()
    deduped = []
    for s in suggestions:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped[:3]


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required")

    try:
        engine = get_engine()
    except FileNotFoundError as e:
        # Index not found at the expected path -- most likely cause during
        # setup, so surface the real message rather than a generic 500.
        log.error("Index not found: %s", e)
        raise HTTPException(503, str(e))
    except RuntimeError as e:
        # RagEngine.__init__ raises this if it can't reach Ollama.
        log.error("Ollama unreachable: %s", e)
        raise HTTPException(503, str(e))

    session = _memory.get(req.session_id)

    try:
        history = [{"role": t.role, "content": t.content} for t in req.history]
        # NEW: recent_offers is what this session was actually shown before
        # (see memory.py) -- RagEngine uses it to resolve follow-ups like
        # "how much was it before the discount" back to the exact offer,
        # deterministically, instead of hoping embedding similarity alone
        # lands on the right one.
        result = engine.answer(query, history=history, recent_offers=session.recent())
    except Exception:
        log.exception("chat() failed for query=%r", query)
        raise HTTPException(500, "The assistant hit an internal error. Please try again.")

    raw_sources = result.get("sources", [])

    # NEW: remember only what was actually shown in THIS answer (the top few
    # sources returned to the widget), not the full internal candidate pool
    # -- so memory reflects what the user saw, not everything the retriever
    # merely scored along the way.
    session.remember(raw_sources[:3])

    sources = [_source_card(d) for d in raw_sources[:3]]
    suggestions = _build_suggestions(raw_sources, detect_lang(query))
    return {"answer": result["answer"], "sources": sources, "suggestions": suggestions}


@app.get("/api/health")
def health():
    """Cheap liveness check that does NOT load the engine, so it stays fast
    even before the model/index/Ollama are ready -- point uptime checks here."""
    return {"status": "ok"}


# Serves static/index.html at "/" and static/favicon.* alongside it.
# Registered last so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
