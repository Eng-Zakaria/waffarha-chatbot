"""
BM25 lexical index for hybrid search.

This is a separate companion to the vector stores in vectorstores.py. It provides
keyword-based retrieval (BM25) that complements semantic embedding search.

Why hybrid (BM25 + embeddings)?
- Embeddings are great for semantic similarity ("burgers" matches "food")
- BM25 is great for exact keyword matches ("KFC" -> KFC documents)
- Combining both via Reciprocal Rank Fusion (RRF) typically improves retrieval
  accuracy by 5-15% over either method alone, especially for queries with
  specific named entities (merchants, product names).

This module implements:
- BM25Store: A pickle-persisted BM25 index using the rank_bm25 library
- tokenize(): Lightweight Arabic/English tokenization for the index
- reciprocal_rank_fusion(): Combines ranked results from multiple retrievers
"""
import math
import os
import pickle
import re
import unicodedata
from typing import List, Tuple
import numpy as np

# Stopwords - common function words that shouldn't count toward BM25 relevance
_ARABIC_STOPWORDS = set("""
في من على إلى الى عن مع او أو لو ان إن ده دي هذا هذه ذلك هؤلاء هو هي
هم هن نحن انا أنا انت أنت انتم أنتم هي هي هم ثم لكن لا لم لن قد سوف
كما كما أن أن ما ما الذي التي الذين اللذان اللتان اللواتي اللاتي هنا هناك
كان كانت كانوا كانوا يكون تكون تكونون يكونن يكونا هكذا كذلك
الذي التى عند عندنا عندكم عندهم عندها
""".split())

_ENGLISH_STOPWORDS = set("""
a an the of in on at for to is are was were be been being have has had
do does did doing will would shall should can could may might must
i me my we us our you your he him his she her it its they them their
this that these those and or but if then else when where while what which who whom
as from by with about into through during before after above below between
up down out off over under again further not no nor so than too very
""".split())


def tokenize(text: str, remove_stopwords: bool = True) -> List[str]:
    """
    Lightweight Arabic + English tokenizer for BM25.

    - Lowercases
    - Normalizes Arabic variants (أ/إ/آ -> ا, ى -> ي)
    - Strips diacritics
    - Splits on word boundaries
    - Filters short tokens (<2 chars) and optional stopwords
    """
    if not text:
        return []
    # Normalize Arabic hamza forms and alef maksura
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي")
    # Strip Arabic diacritics
    text = re.sub(r"[ً-ْٰـ]", "", text)
    # Normalize unicode (NFC)
    text = unicodedata.normalize("NFC", text)
    # Lowercase
    text = text.lower()
    # Extract word tokens (Arabic + Latin + digits)
    tokens = re.findall(r"[؀-ۿ]+|[a-z0-9]+", text)
    # Filter short tokens
    tokens = [t for t in tokens if len(t) >= 2]
    # Filter stopwords if requested
    if remove_stopwords:
        tokens = [t for t in tokens if t not in _ARABIC_STOPWORDS and t not in _ENGLISH_STOPWORDS]
    return tokens


class BM25Store:
    """
    BM25 (Okapi BM25) lexical index for hybrid search.

    Persisted to disk via pickle (single .pkl file containing the index + metadata).
    Scores are in [0, +inf) -- higher is better. We normalize to [0, 1] in
    search() for consistency with the cosine similarity range used by embedding
    backends (see VectorStore.search contract in vectorstores.py).
    """

    name = "bm25"

    def __init__(self):
        self.bm25 = None
        self.docs: list = []
        self.tokenized_corpus: List[List[str]] = []
        self.doc_lengths: List[int] = []
        self.avgdl: float = 0.0
        self.n_docs: int = 0
        self._max_bm25_score: float = 1.0  # for normalization

    def build(self, docs: list) -> None:
        """Build the BM25 index from a list of docs (each with 'text' key)."""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            raise ImportError(
                "rank_bm25 is required for BM25Store. Install with: pip install rank_bm25"
            )

        self.docs = docs
        self.tokenized_corpus = [tokenize(d.get("text", "")) for d in docs]
        self.doc_lengths = [len(toks) for toks in self.tokenized_corpus]
        self.n_docs = len(docs)
        self.avgdl = (sum(self.doc_lengths) / self.n_docs) if self.n_docs > 0 else 0.0
        self.bm25 = BM25Okapi(self.tokenized_corpus)
        # Pre-compute max score for normalization: top hit on the first doc
        # ensures we always have a valid normalization factor
        if self.n_docs > 0:
            sample_scores = self.bm25.get_scores(self.tokenized_corpus[0])
            self._max_bm25_score = max(sample_scores) or 1.0

    def search(self, query: str, k: int) -> List[Tuple[float, int]]:
        """
        Search the BM25 index.

        Returns a list of (normalized_score, doc_index) tuples, where
        normalized_score is in [0, 1] (1 = best possible match, 0 = no match).
        This keeps scores in the same range as the embedding backends so
        RRF fusion and threshold logic work consistently.

        Note: signature is (query, k) -- the VectorStore.search() uses
        (query_embeddings, k) for neural backends, but BM25 only needs the
        raw text query. We accept a string here and ignore the array form.
        """
        if self.bm25 is None or self.n_docs == 0:
            return []
        tokenized_query = tokenize(query)
        if not tokenized_query:
            return []
        scores = self.bm25.get_scores(tokenized_query)
        # Get top-k indices by score (descending)
        top_indices = np.argsort(scores)[::-1][:k]
        results = []
        for idx in top_indices:
            raw_score = float(scores[idx])
            if raw_score <= 0:
                continue  # BM25 can return 0 or negative; skip non-matches
            # Normalize to [0, 1] using the precomputed max
            normalized = raw_score / self._max_bm25_score
            results.append((min(normalized, 1.0), int(idx)))
        return results

    def save(self, path: str) -> None:
        """Save the index to a pickle file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state = {
            "docs": self.docs,
            "tokenized_corpus": self.tokenized_corpus,
            "doc_lengths": self.doc_lengths,
            "avgdl": self.avgdl,
            "n_docs": self.n_docs,
            "_max_bm25_score": self._max_bm25_score,
            # BM25Okapi doesn't pickle cleanly, so we rebuild from corpus on load
        }
        with open(path, "wb") as f:
            pickle.dump(state, f)

    def load(self, path: str) -> None:
        """Load the index from a pickle file. Rebuilds the BM25Okapi object."""
        with open(path, "rb") as f:
            state = pickle.load(f)
        self.docs = state["docs"]
        self.tokenized_corpus = state["tokenized_corpus"]
        self.doc_lengths = state["doc_lengths"]
        self.avgdl = state["avgdl"]
        self.n_docs = state["n_docs"]
        self._max_bm25_score = state["_max_bm25_score"]
        # Rebuild BM25 from the saved corpus
        from rank_bm25 import BM25Okapi
        self.bm25 = BM25Okapi(self.tokenized_corpus)


def reciprocal_rank_fusion(
    ranked_lists: List[List[Tuple[float, int]]],
    k: int = 60,
    weights: List[float] = None,
) -> List[Tuple[int, float]]:
    """
    Reciprocal Rank Fusion (RRF) to combine multiple ranked retrieval lists.

    RRF formula: score(d) = sum( weight_i / (k + rank_i(d)) )
    where rank_i(d) is the 1-based rank of document d in list i, or 0
    if the document is not in list i.

    Args:
        ranked_lists: List of ranked result lists, each in [(score, doc_idx), ...] form
        k: RRF dampening constant (default 60, the value used in the original paper)
        weights: Per-list weight multipliers (default = equal weights)

    Returns:
        List of (doc_idx, rrf_score) sorted descending by rrf_score
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights must have the same length as ranked_lists")

    rrf_scores: dict = {}
    for list_idx, ranked in enumerate(ranked_lists):
        weight = weights[list_idx]
        for rank, (score, doc_idx) in enumerate(ranked, start=1):
            contribution = weight / (k + rank)
            rrf_scores[doc_idx] = rrf_scores.get(doc_idx, 0.0) + contribution

    sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return [(doc_idx, score) for doc_idx, score in sorted_results]
