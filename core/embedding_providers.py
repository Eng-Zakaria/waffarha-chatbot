"""
Embedding provider abstraction for Waffarha Chatbot.

Three backends are supported, selected by a prefix on the model name:

- SentenceTransformer (no prefix, the default): any model id accepted by the
  sentence-transformers library, e.g. ``BAAI/bge-m3``,
  ``intfloat/multilingual-e5-large``, ``jinaai/jina-embeddings-v5-text-small``.
  Always runs locally on ``config.EMBEDDING_DEVICE``.

- Ollama (``ollama:`` prefix): an embedding model served by a local Ollama
  instance via ``POST {OLLAMA_HOST}/api/embed``, e.g.
  ``ollama:qwen3-embedding:0.6b``. Supports batched input, so thousands of
  documents are encoded a few requests at a time instead of one per doc.

- Jina (``jina:`` prefix): Jina AI's hosted embeddings API at
  ``https://api.jina.ai/v1/embeddings``, e.g.
  ``jina:jina-embeddings-v5-text-small``. Requires ``JINA_API_KEY`` (see
  ``core/config.py``). The ``task`` argument is forwarded as the API's
  ``task`` field (``retrieval.query`` / ``retrieval.passage``...).

Every provider presents the same ``encode()`` surface as
``SentenceTransformer.encode``: it takes a list of texts and returns an
``(N, dim)`` float32 numpy array, L2-normalized by default. That is the exact
contract the VectorStore backends and the score thresholds in config.py
depend on, so swapping providers requires no changes below this module.
"""
import re

import numpy as np

from core import config


def _l2_normalize(vector: np.ndarray) -> np.ndarray:
    """L2-normalize a single vector in place-safe fashion (zero-norm safe)."""
    norm = np.linalg.norm(vector)
    if norm > 0:
        return vector / norm
    return vector


def _batch(texts, batch_size: int):
    for i in range(0, len(texts), batch_size):
        yield texts[i:i + batch_size]


def _progress(total: int):
    """Return a tqdm progress bar when available, else a silent no-op."""
    try:
        from tqdm.auto import tqdm
        return tqdm(total=total, desc="Embedding")
    except ImportError:
        class _Null:
            def update(self, *a, **k):
                pass
            def close(self, *a, **k):
                pass
        return _Null()


class EmbeddingProvider:
    """Common interface for all embedding backends."""

    provider = "base"
    name = "base"

    def encode(self, texts, batch_size: int = 64, show_progress_bar: bool = True,
               normalize_embeddings: bool = True, convert_to_numpy: bool = True,
               task: str | None = None) -> np.ndarray:
        raise NotImplementedError


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """Wraps the sentence-transformers library (local / offline)."""

    provider = "sentence-transformer"

    def __init__(self, model_name: str, device: str | None = None, **kwargs):
        from sentence_transformers import SentenceTransformer
        self.name = model_name
        self._model = SentenceTransformer(model_name, device=device or config.EMBEDDING_DEVICE, **kwargs)

    def encode(self, texts, batch_size: int = 64, show_progress_bar: bool = True,
               normalize_embeddings: bool = True, convert_to_numpy: bool = True,
               task: str | None = None) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        return self._model.encode(
            texts, batch_size=batch_size, show_progress_bar=show_progress_bar,
            normalize_embeddings=normalize_embeddings, convert_to_numpy=True,
        ).astype("float32")


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embeds via ``POST {OLLAMA_HOST}/api/embed`` (batched input supported)."""

    provider = "ollama"

    def __init__(self, model_name: str, host: str | None = None):
        self.name = model_name
        self.host = host or config.OLLAMA_HOST
        base = self.host.rstrip("/")
        self.url = f"{base}/api/embed"
        self._legacy_url = f"{base}/api/embeddings"
        # Old Ollama builds (< 0.3.x) don't serve the batched /api/embed
        # endpoint at all -- they only have the legacy single-document
        # /api/embeddings. Detected lazily on the first 404 (plain "page not
        # found", not a JSON model error) and used for every subsequent batch.
        self._legacy = False

    def _legacy_embed(self, batch):
        import requests
        embeddings = []
        for text in batch:
            resp = requests.post(self._legacy_url, json={
                "model": self.name,
                "prompt": text,
            }, timeout=(10, 300))
            if resp.status_code != 200:
                try:
                    err = resp.json().get("error", "")
                except (ValueError, AttributeError):
                    err = ""
                raise RuntimeError(
                    f"Ollama embedding failed for {self.name} over "
                    f"{self._legacy_url}: {err or f'HTTP {resp.status_code}'}"
                )
            data = resp.json()
            if "embedding" in data:
                embeddings.append(data["embedding"])
            elif "data" in data and isinstance(data["data"], list) and data["data"]:
                embeddings.append(data["data"][0]["embedding"])
            else:
                raise RuntimeError(
                    f"Unexpected Ollama /api/embeddings response for {self.name}: "
                    f"{list(data.keys())}"
                )
        return embeddings

    def _embed_batch(self, batch):
        import requests
        if self._legacy:
            return self._legacy_embed(batch)
        resp = requests.post(self.url, json={
            "model": self.name,
            "input": list(batch),
            "truncate": True,
        }, timeout=(10, 300))
        if resp.status_code == 404:
            try:
                err = resp.json().get("error", "")
            except (ValueError, AttributeError):
                err = ""
            if err:
                raise RuntimeError(f"Ollama embedding error for {self.name}: {err}")
            # Endpoint itself missing -> fall back to the legacy API for good.
            self._legacy = True
            print(f"Ollama at {self.host} has no /api/embed -- falling back to "
                  f"legacy /api/embeddings (slower, one request per doc).")
            return self._legacy_embed(batch)
        resp.raise_for_status()
        data = resp.json()
        if "embeddings" not in data:
            raise RuntimeError(
                f"Unexpected Ollama /api/embed response for {self.name}: "
                f"{list(data.keys())}"
            )
        return data["embeddings"]

    def encode(self, texts, batch_size: int = 64, show_progress_bar: bool = True,
               normalize_embeddings: bool = True, convert_to_numpy: bool = True,
               task: str | None = None) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        results = []
        bar = _progress(len(texts)) if show_progress_bar else None
        try:
            for batch in _batch(texts, max(1, int(batch_size))):
                for emb in self._embed_batch(batch):
                    vec = np.asarray(emb, dtype="float32")
                    if normalize_embeddings:
                        vec = _l2_normalize(vec)
                    results.append(vec)
                if bar is not None:
                    bar.update(len(batch))
        finally:
            if bar is not None:
                bar.close()
        return np.stack(results)


class JinaEmbeddingProvider(EmbeddingProvider):
    """Embeds via Jina AI's hosted API ``POST https://api.jina.ai/v1/embeddings``."""

    provider = "jina"

    def __init__(self, model_name: str, api_key: str | None = None, api_url: str | None = None):
        self.name = model_name
        self.api_key = api_key or config.JINA_API_KEY
        self.api_url = (api_url or config.JINA_API_URL).rstrip("/")
        if not self.api_key:
            raise RuntimeError(
                "JINA_API_KEY is not set. Get one from https://jina.ai and add "
                "JINA_API_KEY=<key> to your .env, then rebuild the index with "
                "jina:jina-embeddings-v5-text-small (or another jina: model)."
            )

    def _embed_batch(self, batch, task):
        import requests
        body = {
            "model": self.name,
            "input": list(batch),
            "normalized": True,
            "truncate": True,
        }
        if task:
            body["task"] = task
        resp = requests.post(
            self.api_url, json=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            timeout=(10, 300),
        )
        if resp.status_code in (401, 403):
            raise RuntimeError(
                f"Jina API rejected the key (HTTP {resp.status_code}): {resp.text}"
            )
        resp.raise_for_status()
        data = resp.json()
        return [item["embedding"] for item in data.get("data", [])]

    def encode(self, texts, batch_size: int = 64, show_progress_bar: bool = True,
               normalize_embeddings: bool = True, convert_to_numpy: bool = True,
               task: str | None = None) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        results = []
        bar = _progress(len(texts)) if show_progress_bar else None
        try:
            # Jina's hosted API has a request-rate limit, so keep batches
            # modest even if the caller asks for a large batch_size.
            for batch in _batch(texts, max(1, min(int(batch_size), 64))):
                for emb in self._embed_batch(batch, task):
                    vec = np.asarray(emb, dtype="float32")
                    if normalize_embeddings:
                        vec = _l2_normalize(vec)
                    results.append(vec)
                if bar is not None:
                    bar.update(len(batch))
        finally:
            if bar is not None:
                bar.close()
        return np.stack(results)


# Known provider prefixes: keep the longest prefix match for future
# prefixes that one another. Current set only has single-token prefixes.
_PREFIXES = ("ollama:", "jina:")

# Bare Ollama embedding tags (no "ollama:" prefix) that are auto-detected so
# "qwen3-embedding:0.6b" and "ollama:qwen3-embedding:0.6b" behave identically.
# The generic "-embedding" token rule below catches the qwen3-/qwen-embedding
# families; this set covers the common tags without "embedding" in the name.
_KNOWN_OLLAMA_EMBED_TAGS = {
    "nomic-embed-text",
    "mxbai-embed-large",
    "snowflake-arctic-embed",
    "snowflake-arctic-embed2",
    "bge-m3",
    "bge-m4",
    "minilm",
    "all-minilm",
    "granite-embedding",
}


def _is_ollama_embed_tag(model_name: str) -> bool:
    """True for a bare Ollama embedding tag (HF ids always contain '/')."""
    if "/" in model_name:
        return False
    base = model_name.split(":")[0].strip().lower()
    tokens = set(re.split(r"[^a-z0-9]+", base))
    return base in _KNOWN_OLLAMA_EMBED_TAGS or "embedding" in tokens


def canonical_model_key(model_name: str) -> str:
    """
    Canonical embedding-model identifier used for the on-disk index name.

    Applies the provider prefix so that a bare Ollama embedding tag
    ("qwen3-embedding:0.6b") and its prefixed form ("ollama:...") resolve to
    the SAME directory and the SAME provider. Jina tags are already
    unambiguous ("jina:" prefix); anything else is a sentence-transformers id.
    """
    if model_name.startswith(_PREFIXES):
        return model_name
    if _is_ollama_embed_tag(model_name):
        return f"ollama:{model_name}"
    return model_name


def get_embedding_provider(model_name: str, device: str | None = None, **kwargs) -> EmbeddingProvider:
    """
    Build the right embedding provider for a model name.

    ``ollama:<model>`` (or a bare known Ollama embedding tag such as
    ``qwen3-embedding:0.6b``) routes to Ollama's ``/api/embed``;
    ``jina:<model>`` routes to Jina AI's hosted API; anything else is treated
    as a sentence-transformers model id and runs locally.
    """
    model_name = canonical_model_key(model_name)
    if model_name.startswith("ollama:"):
        return OllamaEmbeddingProvider(model_name[len("ollama:"):])
    if model_name.startswith("jina:"):
        return JinaEmbeddingProvider(model_name[len("jina:"):], **kwargs)
    return SentenceTransformerEmbeddingProvider(model_name, device=device, **kwargs)