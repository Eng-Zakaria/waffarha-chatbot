FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=120 --retries 10 -r requirements.txt

COPY app.py config.py rag_engine.py vectorstores.py memory.py ./
COPY static/ ./static/
COPY ingest/ ./ingest/

# Bake the embedding model into the image at build time (see DOCKER.md) so
# the container never needs network access to huggingface.co at runtime.
ARG EMBEDDING_MODEL=intfloat/multilingual-e5-base
ENV EMBEDDING_MODEL=${EMBEDDING_MODEL}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]