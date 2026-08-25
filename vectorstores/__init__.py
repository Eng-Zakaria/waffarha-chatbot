"""
vectorstores package - Unified vector store interface.

Provides multiple retrieval backends:
- faiss, chroma, qdrant, lancedb, pgvector (from vectorstores.py)
- bm25 (from bm25_store.py)
"""
from .vectorstores import VectorStore, get_store, FaissStore, ChromaStore, QdrantStore, LanceDBStore, PgVectorStore
from .bm25_store import BM25Store, tokenize, reciprocal_rank_fusion

__all__ = [
    "VectorStore", "get_store", "FaissStore", "ChromaStore", "QdrantStore",
    "LanceDBStore", "PgVectorStore", "BM25Store", "tokenize", "reciprocal_rank_fusion",
]
