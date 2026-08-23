# Docker Deployment Guide — Waffarha Assistant

Complete guide to building, deploying, and operating the Waffarha Assistant using Docker. The setup is designed for **production use**: models are baked into images at build time, so runtime servers need **zero outbound internet access**.

---

## 🎯 Design Philosophy

| Principle | Implementation |
|-----------|----------------|
| **Offline runtime** | Ollama model + embedding model baked at build time |
| **Single origin** | App serves widget + API — no separate static hosting |
| **Shared memory** | Redis backs session memory across app replicas |
| **Health-aware** | Compose healthchecks ensure dependency readiness |
| **Reproducible** | Same images run locally, in CI, and in production |

---

## 📁 Required Files at Project Root

```
waffarha-chatbot/
├── Dockerfile                 # App container
├── docker-compose.yml         # Multi-service orchestration
├── .dockerignore              # Build context exclusions
├── .env.example               # Template for runtime config
├── .env                       # Your actual config (gitignored)
├── data/                      # Prebuilt vector indexes (must exist!)
│   └── index/
│       └── intfloat__multilingual-e5-base/
│           └── faiss/         # Default FAISS index
├── static/                    # Chat widget (served by app)
├── ollama-docker/
│   └── ollama-Dockerfile      # Ollama image with model baked in
└── requirements.txt           # Python deps (copied into app image)
```

> ⚠️ **Critical**: The `data/index/.../faiss/` directory **must exist locally** before building. The app image does not build indexes — it only loads them.

---

## 🔧 Configuration

### 1. Create Your `.env`

```bash
cp .env.example .env
```

### 2. Required Variables

| Variable | Required? | Description |
|----------|-----------|-------------|
| `WAFFARHA_SECURITY_KEY` | **YES** | API key for offer fetching (ingest scripts only) |
| `OLLAMA_MODEL` | No | Default: `qwen2.5:3b-instruct` — change to rebuild ollama image |
| `EMBEDDING_MODEL` | No | Default: `intfloat/multilingual-e5-base` — change to rebuild app image |
| `CLICKHOUSE_*` | If using ClickHouse fetchers | ClickHouse connection for data ingestion |

### 3. Optional Overrides

```env
# Vector backend (faiss/chroma/qdrant/lancedb/pgvector)
VECTOR_BACKEND=faiss

# Memory backend (redis/local)
MEMORY_BACKEND=redis
REDIS_URL=redis://redis:6379/0  # Use service name in Docker network

# Identity (static/header/session)
IDENTITY_BACKEND=static
STATIC_USER_ID=12345

# Performance
OLLAMA_NUM_CTX=2048
OLLAMA_NUM_PREDICT=256
```

---

## 🏗️ Building Images

### First Build (Downloads Models — Slow)

```bash
docker compose up --build
```

**What happens:**

1. **`ollama` image builds** (`ollama-docker/ollama-Dockerfile`)
   - Starts Ollama server in background
   - Polls until ready
   - `ollama pull ${OLLAMA_MODEL}` — downloads ~2GB model into `/root/.ollama`
   - Stops server — model now baked in image layer

2. **`app` image builds** (`Dockerfile`)
   - Installs Python deps (cached via BuildKit)
   - Copies source code
   - **Downloads embedding model** via `SentenceTransformer('${EMBEDDING_MODEL}')` — cached in image

3. **Services start** (after both images built)
   - `redis` starts, healthcheck passes
   - `ollama` starts, serves baked model, healthcheck passes
   - `app` starts, loads index from volume, serves on port 8000

### Subsequent Starts (Fast)

```bash
docker compose up
```

- Images already exist — no model downloads
- Containers start in ~10s

---

## 🚀 Running in Production

### Basic Production Run

```bash
# Build once (on build machine with internet)
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

# Push to registry
docker compose push

# On production server (no internet needed!)
docker compose pull
docker compose up -d
```

### Production Compose Override (`docker-compose.prod.yml`)

```yaml
services:
  app:
    restart: always
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 1G
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  ollama:
    deploy:
      resources:
        limits:
          memory: 4G  # Adjust for model size
        reservations:
          memory: 2G

  redis:
    deploy:
      resources:
        limits:
          memory: 256M
```

### Scaling the App

```bash
# Scale to 3 replicas (share Redis memory)
docker compose up -d --scale app=3

# Behind a load balancer (nginx/traefik) — all hit same Redis
```

---

## 🔍 Healthchecks & Monitoring

### Built-in Healthchecks

| Service | Healthcheck | Interval |
|---------|-------------|----------|
| `redis` | `redis-cli ping` | 10s |
| `ollama` | `ollama list` (API call) | 15s |
| `app` | `curl -f http://localhost:8000/health` | 30s |

### Add App Health Endpoint

In `app.py`, add:

```python
@app.get("/health")
async def health():
    return {"status": "ok", "service": "waffarha-assistant"}
```

### Monitoring Commands

```bash
# View logs
docker compose logs -f app
docker compose logs -f ollama

# Resource usage
docker compose top
docker stats

# Execute shell
docker compose exec app bash
docker compose exec ollama ollama list
```

---

## 🐛 Troubleshooting

### "Index not found" on app start

```
FileNotFoundError: [Errno 2] No such file or directory: '/app/data/index/.../faiss/index.faiss'
```

**Cause**: `data/index/` not copied into image (by design — too large) and not mounted.

**Fix**: Mount `data/` as volume in `docker-compose.yml`:

```yaml
services:
  app:
    volumes:
      - ./data:/app/data:ro
```

### Ollama model not found at runtime

```
ollama: Error: model 'qwen2.5:3b-instruct' not found, try pulling it first
```

**Cause**: Model not baked correctly, or volume not seeded.

**Fix**:

```bash
# Check ollama image has model
docker compose run --rm ollama ollama list

# If empty, rebuild ollama image
docker compose build --no-cache ollama

# Named volume should seed from image on first start
docker compose up ollama
```

### Redis connection refused

```
redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379
```

**Cause**: App trying to connect to `localhost` instead of service name.

**Fix**: In `.env`, use Docker service name:

```env
REDIS_URL=redis://redis:6379/0
```

### Out of memory (OOM) kills

```
Killed (exit code 137)
```

**Cause**: Container exceeds memory limit.

**Fix**: Increase limits in `docker-compose.prod.yml` or use smaller model:

```env
OLLAMA_MODEL=qwen2.5:1.5b-instruct  # ~1GB vs ~2GB
```

### Port already in use

```
Error: Port 8000 already in use
```

**Fix**: Change host port mapping:

```yaml
# docker-compose.yml
services:
  app:
    ports:
      - "8080:8000"  # Host:Container
```

---

## 🔐 Security Hardening

### Non-root User (App Image)

The `Dockerfile` runs as root by default. For production:

```dockerfile
# Add to Dockerfile before CMD
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser
```

### Read-only Root Filesystem

```yaml
# docker-compose.prod.yml
services:
  app:
    read_only: true
    tmpfs:
      - /tmp
      - /var/cache
```

### Secrets Management

**Don't put secrets in `.env` committed to git.**

Use Docker secrets:

```yaml
# docker-compose.prod.yml
services:
  app:
    secrets:
      - waffarha_security_key
      - clickhouse_password

secrets:
  waffarha_security_key:
    external: true
  clickhouse_password:
    external: true
```

In `config.py`, read from `/run/secrets/waffarha_security_key`.

---

## 📦 Advanced Configurations

### Using GPU (NVIDIA)

```yaml
# docker-compose.gpu.yml
services:
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - OLLAMA_NUM_GPU=1

  app:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - EMBEDDING_DEVICE=cuda
```

Run with: `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up`

### Alternative Vector Backends

#### Qdrant (Embedded Mode)

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - qdrant_data:/qdrant/storage
    ports:
      - "6333:6333"

  app:
    environment:
      - VECTOR_BACKEND=qdrant
      - QDRANT_URL=http://qdrant:6333
    depends_on:
      - qdrant
```

#### pgvector (PostgreSQL)

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: waffarha
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes:
      - pgvector_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"

  app:
    environment:
      - VECTOR_BACKEND=pgvector
      - PGVECTOR_DSN=postgresql://postgres:${PG_PASSWORD}@postgres:5432/waffarha
    depends_on:
      - postgres
```

### Custom Ollama Model

```bash
# Option 1: Build with different model
OLLAMA_MODEL=llama3.2:3b-instruct docker compose build ollama

# Option 2: Override at runtime (requires registry access)
docker compose run --rm ollama ollama pull llama3.2:3b-instruct
docker compose restart ollama
```

---

## 🔄 CI/CD Integration

### GitHub Actions Example

```yaml
# .github/workflows/docker.yml
name: Docker Build & Test

on:
  push:
    branches: [main]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Build images
        uses: docker/build-push-action@v5
        with:
          context: .
          push: false
          load: true
          tags: waffarha-assistant:test

      - name: Run evaluation suite
        run: |
          docker run --rm \
            -v $(pwd)/data:/app/data:ro \
            waffarha-assistant:test \
            python run_eval.py

      - name: Push to registry
        if: github.ref == 'refs/heads/main'
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ github.repository }}/app:${{ github.sha }}
            ghcr.io/${{ github.repository }}/ollama:${{ github.sha }}
```

---

## 📋 Maintenance Checklist

### Weekly
- [ ] Check disk usage: `docker system df`
- [ ] Review logs for errors: `docker compose logs --since=7d app`
- [ ] Verify eval suite passes: `docker compose run --rm app python run_eval.py`

### Monthly
- [ ] Update base images: `docker compose pull && docker compose up --build -d`
- [ ] Refresh offers & rebuild index (if data pipeline runs separately)
- [ ] Review Redis memory: `docker compose exec redis redis-cli INFO memory`

### Quarterly
- [ ] Evaluate newer embedding/LLM models
- [ ] Load test with production-scale traffic
- [ ] Review security advisories for base images

---

## 📚 Related Documentation

- **[README.md](README.md)** — Project overview, quick start, architecture
- **[DEVELOPMENT.md](DEVELOPMENT.md)** — Local development workflow
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — RAG pipeline deep dive
- **[API.md](API.md)** — `/api/chat` endpoint reference

---

## 🆘 Support

| Issue | Contact |
|-------|---------|
| Docker/Infra | Platform team |
| Model quality | ML team |
| Data pipeline | Data engineering |
| Chat widget | Frontend team |