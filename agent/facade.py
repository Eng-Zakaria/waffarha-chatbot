"""
Adapter that presents the module-level RagEngine helpers (which  live at
core.rag_engine module scope, not as RagEngine methods) as first-class
methods on the facade the agent engine and tools talk to.

The agent assumes a uniform surface: facade._sanitize_user_query, detect_lang,
classify_intent_robust, _looks_like_greeting/_looks_like_gibberish/
_looks_like_injection_attempt (deterministic safety gate), normalize_arabizi_
and_arabic, plus passthrough to the RagEngine instance for _offer_card_blocks,
_lookup_doc, retrieve and the .faceted catalog.
"""
from __future__ import annotations

import core.rag_engine as _R


def rag_facade(engine) -> _RagFacade:
    """Wrap a loaded RagEngine with the agent-facing method surface."""
    return _RagFacade(engine)


class _RagFacade:
    def __init__(self, engine):
        self._engine = engine

    # --- deterministic helpers bound from module scope ------------------- #
    def _sanitize_user_query(self, query: str) -> str:
        return _R._sanitize_user_query(query)

    def detect_lang(self, query: str) -> str:
        return _R.detect_lang(query)

    def classify_intent_robust(self, query: str) -> str:
        return _R.classify_intent_robust(query)

    def normalize_arabizi_and_arabic(self, query: str) -> str:
        return _R.normalize_arabizi_and_arabic(query)

    def _looks_like_greeting(self, query: str) -> bool:
        return bool(_R._looks_like_greeting(query))

    def _looks_like_gibberish(self, query: str) -> bool:
        return bool(_R._looks_like_gibberish(query))

    def _looks_like_injection_attempt(self, query: str) -> bool:
        return bool(_R._looks_like_injection_attempt(query))

    def _lookup_doc(self, source: str, doc_id: str, lang_hint=None):
        return self._engine._lookup_doc(source, doc_id, lang_hint)

    # --- passthrough to the RagEngine instance --------------------------- #
    def __getattr__(self, name):
        return getattr(self._engine, name)