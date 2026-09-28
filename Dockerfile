FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt

COPY app.py run_servers.py ./
COPY core/ ./core/
COPY agent/ ./agent/
COPY memory/ ./memory/
COPY catalog/ ./catalog/
COPY personal/ ./personal/
COPY support/ ./support/
COPY session/ ./session/
COPY vectorstores/ ./vectorstores/
COPY utils/ ./utils/
COPY config/ ./config/
COPY api/ ./api/
COPY static/ ./static/
COPY ingestion/ ./ingestion/

# Bake the embedding model into the image at build time (see DOCKER.md) so
# the container never needs network access to huggingface.co at runtime.
# Only relevant for local sentence-transformers models -- "ollama:" / "jina:"
# prefixed models are API-backed and have nothing to pre-download.
ARG EMBEDDING_MODEL=BAAI/bge-m3
ENV EMBEDDING_MODEL=${EMBEDDING_MODEL}
RUN python -c "import os; m=os.getenv('EMBEDDING_MODEL'); assert m and not (m.startswith('ollama:') or m.startswith('jina:')), 'no local model to bake'; from sentence_transformers import SentenceTransformer; SentenceTransformer(m)"

EXPOSE 8000 8001
# One process serves both widgets (see run_servers.py): :8000 cascade +
# :8001 agent share one RagEngine/index via module singletons, avoiding the
# Qdrant folder-lock that forbids two separate processes.
CMD ["python", "run_servers.py"]