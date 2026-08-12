# syntax=docker/dockerfile:1

FROM python:3.11-slim

# faiss-cpu / sentence-transformers wheels are prebuilt for Linux, so no
# build-essential is needed. libgomp1 is required at runtime by faiss.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first so this layer is cached across code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. ingest/ must keep its package layout (ingest/__init__.py) --
# rag_engine.py imports `from ingest.build_index import index_dir`, and
# that import only resolves correctly if /app is on PYTHONPATH, which
# WORKDIR + `python -m uvicorn` from /app already guarantees.
COPY app.py config.py memory.py rag_engine.py vectorstores.py ./
COPY ingest/ ./ingest/
COPY static/ ./static/

# data/ (the prebuilt FAISS index) is intentionally NOT copied into the
# image -- it's mounted as a volume in docker-compose.yml instead, so
# rebuilding the index doesn't require rebuilding the image, and the
# image itself stays small. See docker-compose.yml's `app.volumes`.

# Pre-download the embedding model into the image at build time so the
# first real request isn't the first time SentenceTransformer downloads
# ~1GB from the Hub. Uses the same EMBEDDING_MODEL default as config.py;
# override with --build-arg if you changed it.
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-base
ENV HF_HOME=/app/.cache/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('${EMBEDDING_MODEL}', device='cpu')"

ENV EMBEDDING_DEVICE=cpu \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
