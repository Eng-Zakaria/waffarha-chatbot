"""
Unified vector store interface so the retrieval backend (FAISS, Chroma,
Qdrant, LanceDB, pgvector) can be swapped without touching RagEngine's
business logic -- intent filtering, lexical bonus, direct-answer shortcuts,
fact-checking, etc. all stay backend-agnostic.

All backends are given L2-normalized embeddings and must return a
*cosine-similarity* score in [-1, 1], so config.MIN_RELEVANCE_SCORE,
config.LEXICAL_BONUS_WEIGHT, config.FAQ_DIRECT_ANSWER_SCORE etc. behave
identically no matter which backend is active. This is the main reason
this file exists: without it, swapping backends would silently change your
score distribution and every threshold in config.py would need re-tuning.

NEW backends (Qdrant, LanceDB, pgvector) each convert their native score
back to cosine similarity in search() -- see the comment in each class for
exactly how, since each library reports something different natively
(Qdrant: similarity directly, LanceDB/pgvector: cosine *distance*).

Setup notes for the new backends:
  qdrant   -- embedded by default (a folder on disk), no server needed.
              pip install qdrant-client
              Set QDRANT_URL (e.g. http://localhost:6333) to use a real
              server instead -- recommended if you'll run build_index.py
              and bench_*.py against the same collection, since embedded
              mode allows only one process to hold the folder open at a
              time and raises "already accessed by another instance"
              otherwise.
  lancedb  -- also fully embedded, no server needed.
              pip install lancedb pyarrow
  pgvector -- the ONE backend here that needs a real server. Needs a
              Postgres instance with the pgvector extension, reachable via
              the PGVECTOR_DSN env var (defaults to
              postgresql://postgres:postgres@localhost:5433/postgres).
              pip install psycopg2-binary pgvector
"""
from abc import ABC, abstractmethod
import hashlib
import os

import numpy as np


class VectorStore(ABC):
    name: str

    @abstractmethod
    def build(self, embeddings: np.ndarray, docs: list) -> None:
        """embeddings: (N, dim) float32, L2-normalized, same order as docs."""

    @abstractmethod
    def search(self, query_embeddings: np.ndarray, k: int) -> list:
        """Returns, per query row, a list of (cosine_similarity, doc_index) tuples."""

    def save(self, path: str) -> None:
        """No-op for backends that persist on write (e.g. Chroma PersistentClient)."""

    @abstractmethod
    def load(self, path: str) -> None: ...


class FaissStore(VectorStore):
    name = "faiss"

    def __init__(self):
        self.index = None

    def build(self, embeddings, docs):
        import faiss
        dim = embeddings.shape[1]
        # IndexFlatIP on normalized vectors == cosine similarity, exact (no ANN
        # approximation) -- the right default at FAQ+offer corpus scale (thousands,
        # not tens of millions, of docs). Swap for IndexHNSWFlat/IVF if you outgrow it.
        self.index = faiss.IndexFlatIP(dim)
        self.index.add(embeddings.astype("float32"))

    def search(self, query_embeddings, k):
        scores, indices = self.index.search(query_embeddings.astype("float32"), k)
        return [
            [(float(s), int(i)) for s, i in zip(row_s, row_i) if i != -1]
            for row_s, row_i in zip(scores, indices)
        ]

    def save(self, path):
        import faiss
        os.makedirs(os.path.dirname(path), exist_ok=True)
        faiss.write_index(self.index, path)

    def load(self, path):
        import faiss
        self.index = faiss.read_index(path)


class ChromaStore(VectorStore):
    """
    Wraps a Chroma PersistentClient. Unlike FaissStore, Chroma writes through
    on every .add() call, so build() already persists to `persist_path` --
    save() is a no-op and exists only to satisfy the shared interface.
    """
    name = "chroma"

    def __init__(self, persist_path: str, collection_name: str = "waffarha_docs"):
        import chromadb
        os.makedirs(persist_path, exist_ok=True)
        self.persist_path = persist_path
        self.collection_name = collection_name
        self.client = chromadb.PersistentClient(path=persist_path)
        self.collection = None

    def build(self, embeddings, docs):
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        # hnsw:space="cosine" makes .query() return cosine *distance* (1 - cos_sim),
        # which we convert back to similarity in search() below.
        self.collection = self.client.get_or_create_collection(
            self.collection_name, metadata={"hnsw:space": "cosine"}
        )
        ids = [str(i) for i in range(len(docs))]
        metadatas = [_flatten_metadata(d["metadata"]) for d in docs]
        documents = [d["text"] for d in docs]

        batch = 500  # Chroma rejects very large single .add() calls
        for start in range(0, len(docs), batch):
            end = start + batch
            self.collection.add(
                ids=ids[start:end],
                embeddings=embeddings[start:end].astype("float32").tolist(),
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )

    def search(self, query_embeddings, k):
        res = self.collection.query(
            query_embeddings=query_embeddings.astype("float32").tolist(), n_results=k
        )
        results = []
        for ids_row, dists_row in zip(res["ids"], res["distances"]):
            row = [(1.0 - float(dist), int(doc_id)) for doc_id, dist in zip(ids_row, dists_row)]
            results.append(row)
        return results

    def save(self, path=None):
        pass  # PersistentClient already wrote through in build()

    def load(self, path=None):
        self.collection = self.client.get_or_create_collection(
            self.collection_name, metadata={"hnsw:space": "cosine"}
        )


class QdrantStore(VectorStore):
    """
    Two modes, switched by the QDRANT_URL env var:
      - SERVER mode (QDRANT_URL set, e.g. http://localhost:6333): talks to a
        real Qdrant server -- multiple processes/scripts can use it at once,
        no file-locking issues.
      - EMBEDDED mode (QDRANT_URL unset): `persist_path` is a folder on disk,
        no server needed, but only ONE process may hold it open at a time --
        a second QdrantClient(path=...) pointed at the same folder (even from
        the same process) raises "already accessed by another instance".

    IMPORTANT for server mode: a real server is shared across every build, so
    collection_name can't be a fixed constant the way it was before -- every
    embedding model would silently overwrite the same collection. It's now
    derived from persist_path (same hashing pgvector already uses for table
    names), so each data/index/<embedding_model>/qdrant/ build gets its own
    collection on the server.

    Qdrant's Distance.COSINE, given already-normalized vectors, returns the
    cosine similarity directly as `.score` -- no conversion needed (unlike
    Chroma/LanceDB/pgvector below, which report distance).
    """
    name = "qdrant"

    def __init__(self, persist_path: str, collection_name: str = None):
        from qdrant_client import QdrantClient
        self.persist_path = persist_path
        self.qdrant_url = os.getenv("QDRANT_URL")
        self.collection_name = collection_name or ("waffarha_" + _safe_table_name(persist_path or "default"))
        if self.qdrant_url:
            self.client = QdrantClient(url=self.qdrant_url)
        else:
            os.makedirs(persist_path, exist_ok=True)
            self.client = QdrantClient(path=persist_path)

    def build(self, embeddings, docs):
        from qdrant_client.models import Distance, VectorParams, PointStruct

        dim = embeddings.shape[1]
        if self.client.collection_exists(self.collection_name):
            self.client.delete_collection(self.collection_name)
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )

        batch = 500
        for start in range(0, len(docs), batch):
            end = min(start + batch, len(docs))
            points = [
                PointStruct(
                    id=i,
                    vector=embeddings[i].astype("float32").tolist(),
                    payload=_flatten_metadata(docs[i]["metadata"]),
                )
                for i in range(start, end)
            ]
            self.client.upsert(collection_name=self.collection_name, points=points)

    def search(self, query_embeddings, k):
        # qdrant-client 1.10+ deprecated .search() in favor of .query_points(),
        # and recent releases (1.12+) removed .search() entirely -- hence the
        # AttributeError. .query_points() returns a QueryResponse wrapping a
        # `.points` list (ScoredPoint objects, same .score/.id fields as the
        # old .search() hits), not the bare hit list .search() used to return.
        results = []
        use_query_points = hasattr(self.client, "query_points")
        for q in query_embeddings:
            vec = q.astype("float32").tolist()
            if use_query_points:
                response = self.client.query_points(
                    collection_name=self.collection_name,
                    query=vec,
                    limit=k,
                )
                hits = response.points
            else:
                hits = self.client.search(
                    collection_name=self.collection_name,
                    query_vector=vec,
                    limit=k,
                )
            results.append([(float(h.score), int(h.id)) for h in hits])
        return results

    def save(self, path=None):
        pass  # both modes write through on upsert

    def load(self, path=None):
        # __init__ already connected self.client (to the server, or to the
        # embedded folder) -- previously this method created a SECOND
        # QdrantClient(path=...) pointed at the same folder, which is what
        # actually caused the "already accessed by another instance" error
        # in embedded mode (the process was locking itself, not fighting a
        # stray external process). Nothing more to do here now.
        if self.qdrant_url and not self.client.collection_exists(self.collection_name):
            raise FileNotFoundError(
                f"Qdrant collection '{self.collection_name}' not found on {self.qdrant_url} -- "
                f"rebuild it first, e.g.: python ingest/build_index.py --backend qdrant "
                f"--embedding-model <model>"
            )


class LanceDBStore(VectorStore):
    """
    Fully embedded, no server -- `persist_path` is a folder LanceDB manages.

    LanceDB's .metric("cosine") search returns `_distance` = 1 - cosine_sim,
    same convention as Chroma, so we convert back to similarity the same way.
    """
    name = "lancedb"

    def __init__(self, persist_path: str, table_name: str = "waffarha_docs"):
        import lancedb
        os.makedirs(persist_path, exist_ok=True)
        self.persist_path = persist_path
        self.table_name = table_name
        self.db = lancedb.connect(persist_path)
        self.table = None

    def build(self, embeddings, docs):
        data = [
            {"id": i, "vector": embeddings[i].astype("float32").tolist()}
            for i in range(len(docs))
        ]
        if self.table_name in self.db.table_names():
            self.db.drop_table(self.table_name)
        self.table = self.db.create_table(self.table_name, data=data)

    def search(self, query_embeddings, k):
        results = []
        for q in query_embeddings:
            hits = (
                self.table.search(q.astype("float32").tolist())
                .metric("cosine")
                .limit(k)
                .to_list()
            )
            results.append([(1.0 - float(h["_distance"]), int(h["id"])) for h in hits])
        return results

    def save(self, path=None):
        pass  # LanceDB writes through on create_table

    def load(self, path):
        import lancedb
        self.db = lancedb.connect(path)
        self.table = self.db.open_table(self.table_name)


class PgVectorStore(VectorStore):
    """
    The one backend here that needs a real, already-running Postgres server
    with the pgvector extension (`CREATE EXTENSION vector;`) -- see the
    setup notes at the top of this file. Connection string comes from the
    PGVECTOR_DSN env var.

    pgvector's `<=>` operator returns cosine *distance* (1 - cosine_sim,
    same convention as Chroma/LanceDB), converted back to similarity in the
    SELECT itself below.

    `persist_path` isn't a filesystem path here (there's no local file to
    write) -- it's just hashed into a stable table name, so the existing
    call pattern (`get_store(backend, persist_path=...)` from
    ingest/build_index.py / eval/bench_vectorstores.py) still works
    unmodified for this backend too.
    """
    name = "pgvector"

    def __init__(self, persist_path: str, dsn: str = None):
        import psycopg2

        self.dsn = dsn or os.getenv(
            "PGVECTOR_DSN", "postgresql://postgres:postgres@localhost:5433/postgres"
        )
        self.table_name = "waffarha_" + _safe_table_name(persist_path or "default")
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    def build(self, embeddings, docs):
        dim = int(embeddings.shape[1])
        with self.conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {self.table_name};")
            cur.execute(
                f"CREATE TABLE {self.table_name} (id INTEGER PRIMARY KEY, embedding VECTOR({dim}));"
            )
            for i in range(len(docs)):
                cur.execute(
                    f"INSERT INTO {self.table_name} (id, embedding) VALUES (%s, %s::vector)",
                    (i, embeddings[i].astype("float32").tolist()),
                )
            cur.execute(
                f"CREATE INDEX ON {self.table_name} USING hnsw (embedding vector_cosine_ops);"
            )

    def search(self, query_embeddings, k):
        results = []
        with self.conn.cursor() as cur:
            for q in query_embeddings:
                vec = q.astype("float32").tolist()
                cur.execute(
                    f"SELECT id, 1 - (embedding <=> %s::vector) AS similarity "
                    f"FROM {self.table_name} ORDER BY embedding <=> %s::vector LIMIT %s",
                    (vec, vec, k),
                )
                rows = cur.fetchall()
                results.append([(float(sim), int(idx)) for idx, sim in rows])
        return results

    def save(self, path=None):
        pass  # writes through on INSERT

    def load(self, path=None):
        pass  # connection + table already set up in __init__ / build()


def _safe_table_name(path: str) -> str:
    """Postgres identifiers must be <=63 chars, alnum/underscore -- hash the
    path down to something short and stable instead of sanitizing it verbatim
    (avoids collisions between e.g. `/` and `:` both becoming `_`)."""
    h = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
    return h


def _flatten_metadata(meta: dict) -> dict:
    """Chroma/Qdrant metadata values must be str/int/float/bool and non-None."""
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out


def get_store(name: str, persist_path: str = None) -> VectorStore:
    if name == "faiss":
        return FaissStore()
    if name == "chroma":
        if persist_path is None:
            raise ValueError("chroma backend requires a persist_path directory")
        return ChromaStore(persist_path)
    if name == "qdrant":
        if persist_path is None:
            raise ValueError("qdrant backend requires a persist_path directory (embedded mode)")
        return QdrantStore(persist_path)
    if name == "lancedb":
        if persist_path is None:
            raise ValueError("lancedb backend requires a persist_path directory")
        return LanceDBStore(persist_path)
    if name == "pgvector":
        return PgVectorStore(persist_path)
    if name == "bm25":
        # BM25 is a separate lexical index; import lazily to avoid
        # requiring rank_bm25 unless the user actually uses this backend
        from vectorstores.bm25_store import BM25Store
        return BM25Store()
    raise ValueError(f"Unknown backend '{name}'. Choose from: faiss, chroma, qdrant, lancedb, pgvector, bm25")