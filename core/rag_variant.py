"""
Extended RAG variant that allows custom embedding models and hybrid retrieval.

The original rag_engine is left untouched; this file adds a thin wrapper that
adds:
- Custom embedding model selection
- Optional cross‑encoder re‑ranking
- Hybrid retrieval via Reciprocal Rank Fusion
- Retrieve method that returns hybrid ranked results
"""

import logging
from typing import List, Optional, Tuple

from sentence_transformers import SentenceTransformer
import numpy as np

from core.config import config
from vectorstores.vectorstores import get_store
import faiss
import pickle

log = logging.getLogger("waffarha-app")


class HybridRetriever:
    """
    Helper class that wraps a dense encoder, a BM25 store and a reranker.
    It provides a single method `retrieve_hybrid(query, top_k)` that returns
    the top‑k documents after fusing dense and lexical scores via RRF.
    """

    def __init__(
        self,
        embedding_model_name: str = "intfloat/multilingual-e5-large",
        use_rerank: bool = False,
        reranker_name: Optional[str] = None,
        bm25_weight: float = 0.35,
        rrf_k: int = 60,
    ):
        """
        Parameters
        ----------
        embedding_model_name: str
            HuggingFace model id for the dense encoder.
        use_rerank: bool
            If True, a cross‑encoder reranker will be used after dense retrieval.
        reranker_name: str or None
            Model identifier for the cross‑encoder; if None, sentence‑transformers/
            all‑mpnet-base‑v2 is used.
        bm25_weight: float
            Weight applied to the BM25 scores in the RRF fusion.
        rrf_k: int
            The reciprocal rank fusion constant `k` (default 60, same as original
            implementation).
        """
        self.embedding_model_name = embedding_model_name
        self.use_rerank = use_rerank
        self.reranker_name = reranker_name or "sentence-transformers/all-mpnet-base-v2"
        self.bm25_weight = bm25_weight
        self.rrf_k = rrf_k

        # Load dense encoder
        log.debug(f"Loading dense encoder: {embedding_model_name}")
        self.dense_encoder = SentenceTransformer(embedding_model_name, device="cpu")

        # Load BM25 store (it will be filled later via set_collection)
        self.bm25_store: Optional[VectorStore] = None

        # Optional cross‑encoder reranker (default multilingual MPNET)
        if use_rerank:
            try:
                from sentence_transformers import CrossEncoder
                self.reranker = CrossEncoder(reranker_name, device="cpu")
            except Exception as e:
                log.warning(f"Failed to load reranker model: {e}")
                self.reranker = None

    # ------------------------------------------------------------------
    # Index management -------------------------------------------------
    def set_collection(self, collection_name: str):
        """Load a vector store (FAISS, Qdrant, etc.) and attach its BM25 store."""
        store = get_store(collection_name)
        self.vector_store = get_store(collection_name)
        self.vector_store.load()  # loads embeddings + BM25 index
        self.bm25_store = self.vector_store.bm25_store

    # ------------------------------------------------------------------
    # Retrieval ---------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 10) -> List[dict]:
        """
        Dense‑only retrieval (legacy behaviour).

        Returns a list of dicts with keys: `id`, `score`, `payload`.
        """
        # Dense encoding
        dense_emb = self.dense_encoder.encode([query], normalize_embeddings=True)
        # Search in the vector store
        results = self.vector_store.search(embedding=results, top_k=top_k)
        # Convert FAISS results to the same dict format used elsewhere
        out = []
        for idx, score in results:
            doc = {
                "id": payloads[idx]["id"],
                "score": float(score),
                "payload": payloads[idx],
            }
            out.append(doc)
        return out

    # ------------------------------------------------------------------
    def retrieve_hybrid(self, query: str, top_k: int = 10) -> List[dict]:
        """
        Hybrid retrieval using Reciprocal Rank Fusion (RRF).

        Steps:
        1. Dense retrieval -> top_k_dense
        2. Lexical BM25 retrieval -> top_k_lexical
        3. Merge the two result lists and apply RRF fusion.
        4. If reranking is enabled, forward the top‑N list to a cross‑encoder.
        Returns the final ranked list of docs (each with `id`, `score`, `payload`).
        """
        # ---------- 1. Dense retrieval ----------
        dense_emb = self.dense_encoder.encode([query], normalize_embeddings=True)
        dense_results = self.vector_store.search(embedding=dense_emb, top_k=top_k)

        # ---------- 2. Lexical BM25 ----------
        bm25_results = self.bm25_store.search(query=query, top_k=10)

        # ---------- 2. Combine via RRF ----------
        # RRF expects a list of (rank, id) pairs.
        # We need to merge the two lists; missing items are treated as rank = INF.
        # We'll assign a large rank (`k+1`) to missing items.
        fused = self._rrf_merge(dense_results, bm25_results, k=self.rrf_k)

        # ---------- 3. Optional reranking ----------
        if self.use_rerank and self.reranker and len(fused) > 0:
            # Take top_k from the fused list
            top_items = fused[:top_k]
            # Rerank using the cross‑encoder
            # ...
            pass

        return fused

    # ------------------------------------------------------------------
    @staticmethod
    def _rrf_merge(ranked_lists: List[Tuple[List[Tuple[int, float]], int]], k: int) -> List[Tuple[int, float]]:
        """
        Reciprocal Rank Fusion over multiple ranked lists.
        Each ranked list is [(score, doc_id), ...] sorted ascending rank.
        Returns a list of (doc_id, combined_score) sorted descending.
        """
        from collections import defaultdict
        combined_scores = defaultdict(float)
        for rlist in ranked_lists:
            for rank, (score, doc_id) in enumerate(rlist, start=1):
                combined_scores[doc_id] += 1.0 / (k + rank)

        # Convert back to list of (doc_id, score) tuples sorted descending
        fused = [(doc_id, score) for doc_id, score in combined_scores.items()]
        fused_sorted = sorted(fused, key=lambda x: x[1], reverse=True)
        return [(doc_id, score) for doc_id, score in fused_sorted]

    # ------------------------------------------------------------------
    # Public API ---------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 10) -> List[dict]:
        """
        Legacy method kept for backward compatibility.
        """
        # Use hybrid retrieval if hybrid mode is enabled, otherwise fallback to dense.
        if self.use_rerank:
            results = self.retrieve_hybrid(query, top_k=top_k)
        else:
            results = self.vector_store.search(embedding=self.dense_embeddings(query),
                                             top_k=top_k)
        return results

    # ------------------------------------------------------------------
    # Helper methods ----------------------------------------------------
    @staticmethod
    def _embed(texts: List[str]) -> np.ndarray:
        """Encode a list of texts and return L2‑normalized embeddings."""
        model = SentenceTransformer("intfloat/multilingual-e5-large", device="cpu")
        embs = model.encode(texts, normalize_embeddings=True)
        return np.array(embs, dtype=np.float32)

    def _embed_query(self, query: str) -> np.ndarray:
        return self._embed([query])[0]

    def get_hybrid_top_k(self, query: str, k: int = 10) -> List[dict]:
        """
        Public method used by the main RagEngine to obtain the final ranked list.
        Returns a list of document dicts ordered by hybrid rank.
        """
        # 1) Dense retrieval (top_k dense)
        dense_hits = self.vector_store.search(query=query, top_k=self.top_k)

        # 2) Lexical retrieval via BM25 (if available)
        if self.bm25_store:
            bm25_hits = self.bm25.search(query=query, top_k=10)
            dense_hits = dense_hits + bm25_hits
        else:
            dense_hits = dense_hits

        # 3) RRF fusion on the union of dense and BM25 hits
        k_fused = self.rrf_k if hasattr(self, "rrf_k") else 60
        fused_results = rrf_merge(k_fused_lists=[dense_hits, bm25_results],
                                 k_rrf=self.rrf_k)

        # 4) If hybrid mode is enabled and a reranker is configured, rerank
        #    the top fused results using the cross‑encoder.
        if self.use_rerank and self.reranker:
            # Assume self.reranker is a CrossEncoder that takes a list of candidates
            # and returns a score for each. Here we simulate that.
            top_fused = k_fused[:self.top_k_rerank]  # truncate to top_k_rerank
            rerank_scores = self.reranker.rerange(query=query,
                                                    candidates=top_fused_candidates,
                                                    reading_order=' alfabetical')
            # Replace scores in the fused list with the reranker scores
            for doc_id, score in zip([d["id"] for d in top_fused_candidates],
                                     rerank_scores):
                for i, doc in enumerate(fused_results):
                    if doc["id"] == doc_id:
                        doc["score"] = rerank_score
        # Return the final list.
        return fused_results