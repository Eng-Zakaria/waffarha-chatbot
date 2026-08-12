# Running with Docker

## 1. Place these 4 files at the project root
Alongside `app.py`, `config.py`, etc.:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.env.example`

Your `data/` folder (with `index/intfloat__multilingual-e5-base/faiss/`) must
already exist at the project root, same as for the non-Docker setup.

## 2. Configure
```bash
cp .env.example .env
# edit .env -- at minimum set WAFFARHA_SECURITY_KEY
```

## 3. Build and run
```bash
docker compose up --build
```

First run will:
1. Build the `app` image (downloads the embedding model into the image --
   a few minutes, one-time).
2. Start `ollama`, wait for it to be healthy.
3. Run `ollama-pull` once to pull `OLLAMA_MODEL` into a named volume (a few
   minutes, one-time -- cached afterward, including across `docker compose down`
   as long as you don't also remove the `ollama_models` volume).
4. Start `app`, which won't accept traffic until steps 2-3 finish.

Open **http://localhost:8000**.

## Notes
- `data/` is mounted read-only into the container, so rebuilding your index
  (`ingest/build_index.py`) on the host and restarting `app`
  (`docker compose restart app`) picks up the new index without an image rebuild.
- To rebuild the index *from inside Docker* instead of on the host, run e.g.
  `docker compose run --rm app python ingest/fetch_offers.py` --
  but note the `app` image doesn't include `requests` unless you add it to
  `requirements.txt` unconditionally (currently listed for ingest scripts,
  which is already the case in the provided `requirements.txt`).
- Only `app`'s port (8000) is published; `ollama`'s 11434 stays internal to
  the compose network -- add `ports: ["11434:11434"]` under `ollama` if you
  want to `ollama pull`/debug it directly from the host.
- GPU: uncomment the `deploy.resources` block under `ollama` in
  `docker-compose.yml` if you have `nvidia-container-toolkit` installed;
  the app's own embedding step stays on CPU regardless (`EMBEDDING_DEVICE=cpu`)
  unless you also change that and swap `faiss-cpu` for `faiss-gpu`.
