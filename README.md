# eval/

Automated RAG answer-quality checks, run directly against `RagEngine` --
no FastAPI server or Redis required, so this works standalone on the
`testing` worktree/branch without touching your running app.

## Setup

Same environment as the main project (this reuses `rag_engine.py`,
`config.py`, and your existing `data/index/...` build):

```bash
source venv/bin/activate   # or wherever your venv is
ollama serve                # must be running -- RagEngine() connects to it on init
```

## 1. Fill in `queries.json`

The sample file has one case per category the codebase's own comments
already call out (direct-answer shortcuts, same-merchant disambiguation,
multi-item comparison, stock queries, follow-ups, prompt-injection
attempts). Replace the `<MERCHANT_NAME>`, `<OFFER_ID>` etc. placeholders
with real values from your `data/offers_raw.json` / `data/faqs.json` --
otherwise `expected_source`/`expected_id`/`expected_keywords` checks will
just fail on nonexistent data.

Each case supports:
- `query` (required), `id`, `category` -- for your own organization
- `history` / `recent_offers` -- to simulate follow-up turns (same shape
  `RagEngine.answer()` expects; see `memory.py`'s `SessionMemory.recent()`
  docstring for the `recent_offers` shape)
- `expected_source`: `"offer"` or `"faq"` -- checks the top retrieved doc
- `expected_id`: checks the top retrieved doc's id
- `expected_keywords`: list, all must appear in the answer (case-insensitive)
- `forbidden_keywords`: list, none may appear (use for out-of-scope /
  prompt-injection cases)

Add as many cases as you want; nothing else needs to change.

## 2. Run it

```bash
python eval/run_eval.py
```

Useful flags:
```bash
# compare a different embedding model (must already be built -- see ingest/build_index.py)
python eval/run_eval.py --embedding-model intfloat/multilingual-e5-small --tag e5-small

# compare a different Ollama model
python eval/run_eval.py --llm-model qwen2.5:1.5b-instruct --tag qwen1.5b

# pin generation temperature for reproducibility
python eval/run_eval.py --temperature 0.0
```

## 3. Read results

Each run writes `eval/results/<timestamp>[_tag]/`:
- `full.json` -- every field per query (answer text, top source/score,
  latency, which checks passed, scaffolding-leak flag)
- `summary.csv` -- same data, spreadsheet-friendly, for eyeballing or
  diffing between runs

The script exits non-zero if any checked case failed or errored, so it's
safe to drop into a pre-push hook or CI step if you want that later.

## Notes / limitations

- Checks are simple substring/id matching, not semantic grading -- a
  `passed: false` means "go read the `answer` field", not "definitely
  broken." There's no ground-truth judge here.
- There's no automated way to tell from outside `RagEngine.answer()`
  whether a reply came from a templated direct-answer shortcut or a real
  LLM generation -- `latency_s` is a rough proxy (direct answers skip the
  Ollama call entirely, so they're usually much faster) but isn't recorded
  as a separate field to avoid implying more precision than that.
- Runs are sequential and hit your local Ollama instance once per query,
  so a 3B model on CPU will make a large query set slow -- this mirrors
  real per-request latency rather than trying to parallelize past
  `config.MAX_CONCURRENT_GENERATIONS`.
