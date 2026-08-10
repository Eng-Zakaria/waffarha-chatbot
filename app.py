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
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from rag_engine import RagEngine

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("waffarha-app")

app = FastAPI(title="Waffarha Assistant")

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


class SourceCard(BaseModel):
    title: str
    snippet: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceCard]


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

    try:
        history = [{"role": t.role, "content": t.content} for t in req.history]
        result = engine.answer(query, history=history)
    except Exception:
        log.exception("chat() failed for query=%r", query)
        raise HTTPException(500, "The assistant hit an internal error. Please try again.")

    sources = [_source_card(d) for d in result.get("sources", [])[:3]]
    return {"answer": result["answer"], "sources": sources}


@app.get("/api/health")
def health():
    """Cheap liveness check that does NOT load the engine, so it stays fast
    even before the model/index/Ollama are ready -- point uptime checks here."""
    return {"status": "ok"}


# Serves static/index.html at "/" and static/favicon.* alongside it.
# Registered last so it doesn't shadow the /api/* routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
