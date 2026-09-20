"""Unit tests for the shared index-manifest helpers
(ingestion/loaders/index_manifest.py) added in Phase 4.

All tests are fast and offline: they monkeypatch config.INDEX_DIR to a tmp
dir and never touch a real embedding model or vector store.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from core import config
from ingestion.loaders import index_manifest as m


@pytest.fixture(autouse=True)
def tmp_index_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INDEX_DIR", str(tmp_path))
    return tmp_path


def _doc(source="faq", doc_id="1", lang="en", text="Question?",
         metadata=None, indexed_at="2026-01-01T00:00:00"):
    meta = {"source": source, "id": doc_id, "lang": lang,
            "question": text, "answer": "Answer." if source == "faq" else None}
    if metadata:
        meta.update(metadata)
    meta["_indexed_at"] = indexed_at
    return {"text": text, "metadata": meta}


def test_stable_id_format():
    assert m.stable_id(_doc("offer", "777", "ar")) == "offer:777:ar"


def test_compute_doc_hash_deterministic_and_ignores_indexed_at():
    a = _doc(indexed_at="2026-01-01T00:00:00")
    b = _doc(indexed_at="2026-01-02T00:00:00")
    c = _doc(text="Different?")
    assert m.compute_doc_hash(a) == m.compute_doc_hash(b)
    assert m.compute_doc_hash(a) != m.compute_doc_hash(c)


def test_corpus_hash_changes_iff_docs_change():
    def docs_dict(docs):
        return {m.stable_id(d): {"hash": m.compute_doc_hash(d)} for d in docs}

    same = docs_dict([_doc("offer", "1", "en"), _doc("faq", "2", "ar")])
    changed = docs_dict([_doc("offer", "1", "en"), _doc("faq", "3", "ar")])
    assert m.corpus_hash(same) != m.corpus_hash(changed)
    assert m.corpus_hash(same) == m.corpus_hash(
        docs_dict([_doc("offer", "1", "en"), _doc("faq", "2", "ar")]))


def test_canonical_model_key_applied(tmp_index_dir):
    man = m.new_manifest("BAAI/bge-m3", "qdrant")
    assert man["embedding_model"] == "BAAI/bge-m3"
    assert man["backend"] == "qdrant"
    assert man["schema_version"] == m.MANIFEST_SCHEMA_VERSION
    assert man["stats"] == {"total_docs": 0, "faq_count": 0, "offer_count": 0}
    assert man["source"] == {"files": {}, "corpus_hash": None}


def test_build_manifest_counts_and_roundtrip(tmp_index_dir):
    docs = [_doc("offer", "10", "en"), _doc("offer", "10", "ar"),
            _doc("faq", "2", "en")]
    man = m.build_manifest(docs, "BAAI/bge-m3", "qdrant", built_by="unit-test")
    assert man["stats"] == {"total_docs": 3, "faq_count": 1, "offer_count": 2}
    assert man["built_by"] == "unit-test"
    assert len(man["documents"]) == 3
    assert m.corpus_hash(man["documents"]) == man["source"]["corpus_hash"]

    d = m.index_dir("BAAI/bge-m3", "qdrant")
    m.save_manifest_to_dir(d, man)
    mp = m.manifest_path_for_dir(d)
    assert os.path.exists(mp)
    with open(mp, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["source"]["corpus_hash"] == man["source"]["corpus_hash"]
    assert loaded["stats"]["faq_count"] == 1


def test_source_file_fingerprints_skips_missing_and_hashes_present(tmp_index_dir):
    (tmp_index_dir / "faqs.json").write_text('{"faq": true}', encoding="utf-8")
    fps = m.source_file_fingerprints()
    assert "faqs.json" in fps
    assert fps["faqs.json"]["bytes"] == len('{"faq": true}')
    assert fps["faqs.json"]["sha256"]
    assert "offers_raw.json" not in fps


def test_manifest_path_shape():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        monkey_old = config.INDEX_DIR
        try:
            config.INDEX_DIR = td
            p = m.manifest_path("BAAI/bge-m3", "faiss")
            assert p == os.path.join(td, "index", "BAAI__bge-m3", "faiss", "index_manifest.json")
        finally:
            config.INDEX_DIR = monkey_old


def test_load_manifest_absent_returns_blank(tmp_index_dir):
    man = m.load_manifest("BAAI/bge-m3", "qdrant")
    assert man["documents"] == {}
    assert man["stats"]["total_docs"] == 0