# Waffarha Assistant

A RAG-powered customer support chat widget: `static/index.html` (frontend)
talks to `app.py` (FastAPI backend), which calls `rag_engine.py` to retrieve
from a prebuilt FAISS index (`data/index/.../faiss/`) and generate an
answer with a local Ollama model.

## 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Set up Ollama (the LLM that generates answers)

```bash
# Install from https://ollama.com if you haven't already
ollama serve                          # starts the Ollama server (skip if already running)
ollama pull qwen2.5:1.5b-instruct     # the model config.py defaults to
```

## 3. Configure (optional)

```bash
cp .env.example .env
```

Defaults work out of the box on a CPU-only machine. Edit `.env` if you:
- run Ollama somewhere other than `localhost:11434`
- want a different Ollama model (`OLLAMA_MODEL`)
- have an NVIDIA GPU and want embeddings on it (`EMBEDDING_DEVICE=cuda`,
  plus a matching `faiss-gpu` / CUDA-enabled `torch` install)

## 4. Run the server

```bash
uvicorn app:app --reload --port 8000
```

Open **http://localhost:8000** — that's `static/index.html`, now talking to
the real backend instead of the mock demo data.

The first message you send will be slow (~10-30s) while the embedding model
loads and Ollama warms up; after that, responses are much faster.

## Project layout

```
app.py                  FastAPI server -- POST /api/chat, serves static/
config.py                All tunable thresholds, model names, paths
rag_engine.py             Retrieval + generation (RagEngine) -- untouched logic
vectorstores.py           FAISS/Chroma/Qdrant/LanceDB/pgvector backends
static/
  index.html               The chat widget (dark mode, history, users, favicon)
  favicon.svg / .ico / apple-touch-icon.png
data/
  index/intfloat__multilingual-e5-base/faiss/
    docs.pkl                 Your uploaded prebuilt index
    index.faiss
ingest/
  build_index.py            Rebuilds the index from data/faqs.json + offers_raw.json
  fetch_offers.py           Pulls fresh offers from the Waffarha API
```

## Already wired up

-  `docs.pkl` + `index.faiss` are placed at
  `data/index/intfloat__multilingual-e5-base/faiss/` -- the exact path
  `RagEngine()` looks for by default (matches `config.EMBEDDING_MODEL` +
  `backend="faiss"`). No extra config needed to use them as-is.
- `config.EMBEDDING_DEVICE` now defaults to `"cpu"` (was hardcoded `"cuda"`,
  which would crash on any machine without an NVIDIA GPU) -- override via
  `.env` if you do have one.
- The widget's chat history (in `static/index.html`) sends prior turns to
  `/api/chat` on every request, so multi-turn context works out of the box.

## If you want to refresh the offers / rebuild the index later

You'll need `data/faqs.json` (not included -- wasn't among the uploaded
files) and can fetch fresh offers with:

```bash
python ingest/fetch_offers.py            # writes data/offers_raw.json
python ingest/build_index.py --backend faiss
```

That writes a new index to the same `data/index/<model>/faiss/` path, so
`app.py` will pick it up automatically on next restart.

## Troubleshooting

- **"Can't reach Ollama..."** in the chat reply -- Ollama isn't running, or
  `OLLAMA_HOST`/`OLLAMA_MODEL` don't match what you started. Run `ollama
  serve` and `ollama pull <model>` in another terminal.
- **"Index not found at ..."** -- the `data/index/...` folder got moved or
  `config.EMBEDDING_MODEL` was changed without rebuilding. Either restore
  the folder or run `ingest/build_index.py` with the new model.
- Slow first response is expected (model + index load once, then stay in
  memory for the life of the server process).
