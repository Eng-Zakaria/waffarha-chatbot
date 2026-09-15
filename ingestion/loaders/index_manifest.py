"""Shared index-manifest helpers for the Waffarha ingestion pipeline.

A single canonical `index_manifest.json` is written NEXT TO every built index
(`data/index/<model>/<backend>/index_manifest.json`) by BOTH the full builder
(`loaders/build_index.py`) and the incremental updater
(`loaders/build_index_incremental.py`), so the two never drift: they share one
manifest format, the same per-document change-detection hashes, and the same
source lineage (before the incremental updater owned these helpers privately
and the full builder wrote no manifest at all).

The manifest records, per index:

- identity -- embedding model (canonical key), backend, schema version, the
  build tool that wrote it, created/updated timestamps;
- lineage -- sha256 + byte size of every source file the corpus was built from
  (`faqs.json`, `faqs_payment_methods.json`, `faqs_purchasing_status.json`,
  `offers_raw.json`, `type_prices.json` when present), plus the build-time
  config flags that change the corpus (`OFFER_ACTIVE_VALUES`,
  `INCLUDE_EXPIRED_OFFERS`);
- a corpus-level hash -- sha256 over every doc's stable_id `|` content hash,
  sorted and concatenated; deterministic, and changes iff any doc changes;
- per-document records -- stable_id -> {content hash, metadata,
  index_position}, the exact shape `build_index_incremental.py`'s change
  detection reads;
- stats -- total / faq / offer doc counts.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from core import config
from core.embedding_providers import canonical_model_key

MANIFEST_FILENAME = "index_manifest.json"
MANIFEST_SCHEMA_VERSION = 1

# Source files the full/incremental builders read from config.INDEX_DIR.
# Hashed for lineage; a file that doesn't exist is simply omitted.
_MANIFEST_SOURCE_FILES = (
    "faqs.json",
    "faqs_payment_methods.json",
    "faqs_purchasing_status.json",
    "offers_raw.json",
    "type_prices.json",
)


def safe_name(model_name: str) -> str:
    return model_name.replace("/", "__").replace(":", "_")


def index_dir(embedding_model: str, backend: str) -> str:
    d = os.path.join(
        config.INDEX_DIR,
        "index",
        safe_name(canonical_model_key(embedding_model)),
        backend,
    )
    os.makedirs(d, exist_ok=True)
    return d


def manifest_path_for_dir(index_dir_path: str) -> str:
    return os.path.join(index_dir_path, MANIFEST_FILENAME)


def manifest_path(embedding_model: str, backend: str) -> str:
    return manifest_path_for_dir(index_dir(embedding_model, backend))


def new_manifest(embedding_model: str, backend: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "embedding_model": canonical_model_key(embedding_model),
        "backend": backend,
        "created_at": now,
        "updated_at": now,
        "built_by": None,
        "build_config": {"include_expired_offers": bool(config.INCLUDE_EXPIRED_OFFERS),
                         "offer_active_values": list(config.OFFER_ACTIVE_VALUES) if config.OFFER_ACTIVE_VALUES else None},
        "source": {"files": {}, "corpus_hash": None},
        "stats": {"total_docs": 0, "faq_count": 0, "offer_count": 0},
        "documents": {},
    }


def load_manifest(embedding_model: str, backend: str) -> dict:
    """Load the index manifest, or a blank-but-shaped default if it's absent."""
    path = manifest_path(embedding_model, backend)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        # Backfill any keys a newer schema added since this manifest was written.
        default = new_manifest(embedding_model, backend)
        merged = dict(default)
        merged.update(stored)
        return merged
    return new_manifest(embedding_model, backend)


def save_manifest_to_dir(index_dir_path: str, manifest: dict):
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(index_dir_path, exist_ok=True)
    path = manifest_path_for_dir(index_dir_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def save_manifest(embedding_model: str, backend: str, manifest: dict):
    save_manifest_to_dir(index_dir(embedding_model, backend), manifest)


def compute_doc_hash(doc: dict) -> str:
    """Deterministic hash of a document's text + metadata (minus ephemeral fields)."""
    content = {
        "text": doc["text"],
        "metadata": {
            k: v for k, v in doc["metadata"].items()
            if k not in ("_indexed_at",)
        },
    }
    content_str = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content_str.encode("utf-8")).hexdigest()[:16]


def stable_id(doc: dict) -> str:
    m = doc["metadata"]
    return f"{m['source']}:{m['id']}:{m['lang']}"


def corpus_hash(documents: dict) -> str:
    """sha256 over sorted 'stable_id|content_hash' pairs -- deterministic and
    sensitive to any doc's content or identity."""
    payload = "".join(sorted(f"{sid}|{rec['hash']}" for sid, rec in documents.items()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_file_fingerprints() -> dict:
    """Absent source files are omitted; a fully static data/ dir -> stable dict."""
    out = {}
    for name in _MANIFEST_SOURCE_FILES:
        path = os.path.join(config.INDEX_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            data = f.read()
        out[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return out


def build_manifest(docs: list, embedding_model: str, backend: str,
                   built_by: str = "build_index.py") -> dict:
    """Build a fresh manifest for a fully-built corpus of docs."""
    documents = {}
    for i, doc in enumerate(docs):
        sid = stable_id(doc)
        documents[sid] = {
            "hash": compute_doc_hash(doc),
            "metadata": doc["metadata"],
            "index_position": i,
        }
    manifest = new_manifest(embedding_model, backend)
    manifest["built_by"] = built_by
    manifest["source"]["files"] = source_file_fingerprints()
    manifest["source"]["corpus_hash"] = corpus_hash(documents)
    manifest["stats"]["total_docs"] = len(docs)
    manifest["stats"]["faq_count"] = sum(
        1 for d in docs if d["metadata"].get("source") == "faq"
    )
    manifest["stats"]["offer_count"] = sum(
        1 for d in docs if d["metadata"].get("source") == "offer"
    )
    manifest["documents"] = documents
    return manifest