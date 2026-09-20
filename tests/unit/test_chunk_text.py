"""Unit tests for `core.app._chunk_text` (bug 1 -- answer text glued together).

The SSE streamers reassemble with `full_text += payload` and no separator, so
chunks must preserve the inter-word space removed at each boundary. These tests
assert the exact round-trip invariant: `"".join(_chunk_text(t)) == t` plus the
bounds the streaming layer relies on.
"""
import os, sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.app import _chunk_text


def _roundtrip(text: str, size: int = 24):
    chunks = _chunk_text(text, size=size)
    assert "".join(chunks) == text, "reassembly must reproduce the source text"
    assert chunks, "never return an empty chunk list"
    return chunks


def test_roundtrip_arabic_multi_word():
    _roundtrip("مرحبا! إزيك. عايز أفضل عرض ليك النهارده.")

def test_roundtrip_no_answer_found_template():
    _roundtrip("لم أجد عروضاً مطابقة الآن. جرب تاجراً أو فئة مختلفة.")

def test_roundtrip_single_short_word():
    _roundtrip("مرحبا")

def test_roundtrip_empty_string():
    assert _chunk_text("") == [""]

def test_every_chunk_within_size_bounds():
    text = "عرض ليك وفئة مختلفة وأحسن عرض النهارده من كنتاكي"
    for chunk in _chunk_text(text):
        assert len(chunk) <= 24

def test_all_non_final_chunks_keep_trailing_space():
    text = "لم أجد عروضاً مطابقة الآن. جرب تاجراً أو فئة مختلفة."
    chunks = _chunk_text(text)
    for chunk in chunks[:-1]:
        assert chunk.endswith(" "), "non-final chunk lost its boundary space"
    assert not chunks[-1].endswith(" ")

def test_ticket_repro_no_glued_merchant_name():
    # Ticket 1 repro: chunking the Arabic merchant answer reassembles with
    # "".join (SSE streamer does `full_text += chunk`). A boundary dropped
    # space produced "أنسالدمشقى" from "فروع أنس الدمشقي عروض".
    source = "فروع أنس الدمشقي عروض"
    for size in (8, 10, 14, 24):
        rebuilt = "".join(_chunk_text(source, size=size))
        assert rebuilt == source, f"size={size}: reassembly changed the text"
        assert "أنسالدمشقى" not in rebuilt
        assert "أنسالدمشقي" not in rebuilt

def test_multi_token_piece_stream_roundtrip():
    # Simulates the agent streaming path: the finished answer is ONE string
    # piece; _chunk_text sub-splits it for typing feel and the SSE loop
    # reassembles with "". Each sub-chunk must round-trip the source exactly.
    piece = "لم أجد عروضاً مطابقة الآن. جرب تاجراً أو فئة مختلفة."
    rebuilt = "".join(_chunk_text(piece))
    assert rebuilt == piece