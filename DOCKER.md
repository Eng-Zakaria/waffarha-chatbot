# Running with Docker

## 1. Place these files at the project root
Alongside `app.py`, `config.py`, etc.:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.env.example`
- `ollama/Dockerfile` (keep the `ollama/` subfolder -- it's its own build context)

Your `data/` folder (with `index/intfloat__multilingual-e5-base/faiss/`) must
already exist at the project root, same as for the non-Docker setup.

## 2. Configure
```bash
cp .env.example .env
# edit .env -- at minimum set WAFFARHA_SECURITY_KEY
# OLLAMA_MODEL defaults to qwen2.5:3b-instruct -- change it here if you want
# a different one baked in
```

## 3. Build and run
```bash
docker compose up --build
```

First run will:
1. Build the `ollama` image (`ollama/Dockerfile`) -- starts an internal Ollama
   server during the build, pulls `OLLAMA_MODEL` into the image itself, stops
   the server. This is the slow, network-dependent step (model download,
   several minutes depending on model size / connection) but it happens
   **once, at build time** -- not on every container start.
2. Build the `app` image (downloads the embedding model into the image --
   also one-time).
3. Start `ollama` and `redis` (fast -- ollama's model is already on disk in
   the image, redis just starts), wait for both healthchecks.
4. Start `app` -- it now serves the chat widget itself (`static/index.html`)
   at the same origin as the API, so there's nothing else to stand up:
   no GitHub Pages, no tunnel. Whoever you hand this to just opens
   `http://<this-machine>:8000`.

Open **http://localhost:8000**.

Because the model is baked into the `ollama` image, **running the built
images on a server doesn't need that server to reach Ollama's registry at
all** -- only the machine that runs `docker compose build` (or your CI) does.
That's the point of this setup: build once (wherever you have good
bandwidth), ship the image, run it anywhere.

## Deploying to a server
Build and push once from your dev machine or CI, then just pull+run on the
server:
```bash
docker compose build
docker tag waffarha-docker-ollama:latest yourregistry/waffarha-ollama:v1
docker tag waffarha-docker-app:latest yourregistry/waffarha-app:v1
docker push yourregistry/waffarha-ollama:v1
docker push yourregistry/waffarha-app:v1
```
Then, on the server, point `docker-compose.yml`'s `build:` blocks to
`image: yourregistry/waffarha-ollama:v1` / `...waffarha-app:v1` instead (or
run `docker compose pull && docker compose up -d` against a compose file
that only references the pushed images) -- the server never has to build or
pull a model from Ollama's registry itself.

## Notes
- **Changing the model** later means rebuilding the `ollama` image
  (`docker compose build ollama`), since it's baked in rather than
  runtime-pulled. If the `ollama_models` volume already has a different
  model in it from a previous build, Docker won't overwrite that volume's
  contents automatically -- either `docker compose down -v` first (wipes the
  volume) or exec in and `docker compose exec ollama ollama pull <new model>`
  manually.
- `data/` is mounted read-only into the container, so rebuilding your index
  (`ingest/build_index.py`) on the host and restarting `app`
  (`docker compose restart app`) picks up the new index without an image rebuild.
- To rebuild the index *from inside Docker* instead of on the host, run e.g.
  `docker compose run --rm app python ingest/fetch_offers.py` --
  but note the `app` image doesn't include `requests` unless you add it to
  `requirements.txt` unconditionally (currently listed for ingest scripts,
  which is already the case in the provided `requirements.txt`).
- Only `app`'s port (8000) is published; `ollama`'s 11434 and `redis`'s
  6379 stay internal to the compose network -- add a `ports:` entry under
  either service if you want to hit it directly from the host for debugging.
- Session memory (`memory.py` -- which offers/FAQs a session was already
  shown) now lives in the `redis` service instead of in-process, so it's
  safe to scale `app` to multiple replicas later and survives `app`
  restarts. Data in the `redis_data` volume is low-stakes (1hr TTL per
  session) -- `docker compose down -v` wiping it just means active sessions
  lose their "what did we just show you" memory, not a real data loss.
- GPU: uncomment the `deploy.resources` block under `ollama` in
  `docker-compose.yml` if you have `nvidia-container-toolkit` installed;
  the app's own embedding step stays on CPU regardless (`EMBEDDING_DEVICE=cpu`)
  unless you also change that and swap `faiss-cpu` for `faiss-gpu`.
- Image size: baking in `qwen2.5:3b-instruct` adds roughly 2GB to the
  `ollama` image. That's the trade you're making for a self-contained,
  network-independent deploy -- if image size/push time matters more than
  deploy-time network independence, the alternative is pulling the model
  at container start via a named volume instead (ask if you want that
  version restored).

